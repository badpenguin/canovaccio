"""Scheda "Tracce": righello, elenco tracce, dati del brano.

- In cima il righello (primo elemento, fisso): un click sposta il punto di play.
- Ogni riga ha due parti: nome (doppio click → parametri) e segmenti.
- Modalità Select (F2, default) sui segmenti: click = seleziona (e sposta il
  punto di play); Maiusc+click = mette o toglie il segmento dalla selezione;
  click e trascina = rettangolo di selezione (con Maiusc: aggiunge);
  doppio click (o Invio) = notazione della selezione.
- La selezione è sempre attiva, in ogni modalità: trascinando da uno spazio vuoto
  (o, in Select/Split, da qualsiasi punto) si traccia il rettangolo; Maiusc+click
  mette/toglie un segmento.
- Modalità Create (F3): premendo in uno spazio vuoto di una corsia compare subito
  il nuovo segmento (una battuta); trascinando lo si allunga, battuta per battuta;
  al rilascio viene creato (se c'è spazio). Maiusc+trascina = rettangolo di selezione.
- Modalità Move (F4): trascina i segmenti selezionati; aggancio al movimento
  (battuta), si può cambiare traccia; al rilascio vale solo se c'è spazio.
- Modalità Split (F5): il puntatore diventa una barra verticale (provvisoria); click su un segmento =
  lo divide dove si clicca (agganciato alla battuta più vicina; una linea
  tratteggiata mostra dove); niente divisione sui bordi (lì è già diviso).
  Split agisce solo sul segmento cliccato, non sulla selezione.
- Modalità Resize (F6): trascinando l'inizio o la fine di un segmento lo si
  allunga/accorcia (aggancio alla battuta); vale per un segmento alla volta.
- Modalità Delete (F7): click su un segmento = lo cancella (se fa parte della
  selezione, cancella tutta la selezione). Tasto Canc: cancella la selezione.
- La colonna dei nomi si allarga/stringe trascinando il bordo nel righello.
- Scala fissa (pixel per semibreve): rotella = avanti/indietro lungo la
  timeline, Maiusc+rotella = su/giù, Ctrl+rotella o pulsanti in basso = zoom.
- Toolbar inferiore: dati del brano (solo lettura) e zoom.
"""

from __future__ import annotations

from bisect import bisect_left, bisect_right
from dataclasses import replace
from fractions import Fraction
from pathlib import Path
from typing import Callable

import cairo
import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
gi.require_version("PangoCairo", "1.0")
from gi.repository import Gdk, GLib, Gtk, Pango, PangoCairo  # noqa: E402

from ..core.document import (  # noqa: E402
    AddSegments, AddTrack, Compound, DeleteSegments, DeleteTrack, Document, MoveSegments, MoveTrack,
    ResizeSegment, SplitSegment, UnlinkSegments,
)
from ..core.mixer import volume_bars  # noqa: E402
from ..core.model import Group  # noqa: E402
from ..core.key import MAJOR, Key  # noqa: E402
from ..core.model import Segment, Track, new_id, remap_slurs  # noqa: E402
from ..core.paste import paste_from_other_song  # noqa: E402
from ..output.transport import State, Transport  # noqa: E402
from .keys import PRIMARY_MASK  # noqa: E402
from . import ruler  # noqa: E402
from .follow import follow_center  # noqa: E402
from .icons import themed  # noqa: E402
from .popup import popup_menu  # noqa: E402
from .theme import PLAYHEAD, group_color, is_dark  # noqa: E402

DEFAULT_NAME_WIDTH = 200  # px della colonna dei nomi
CLONE_COLOR = (0.55, 0.55, 0.55)  # le copie collegate (cloni) sono sempre grigie
LABEL_FONT_PX = 14  # etichette dei segmenti: grandezza del testo (px), come prima di Pango
LABEL_TOP = 3  # distanza (px) del testo dell'etichetta dal bordo alto della corsia
LABEL_HALO_PX = 1.5  # spessore (px) dell'alone attorno alle lettere dell'etichetta
LISTEN_SIZE = 22  # px dei pulsanti Mute / Solo (CSS .listen-toggle in app.py)
GAP = 10  # px fra nome e corsia (nel righello è la maniglia per ridimensionare)
# 2026-11-24: linea fra la colonna dei nomi e le corsie: nel righello (maniglia) e, sopra l'elenco,
# per tutta l'altezza delle tracce (richiesta dell'utente: da sola nel righello sembrava un "|" sbagliato)
# 2026-11-24: la linea sull'elenco più chiara (non tutta è trascinabile), la maniglia nel righello più scura
# e larga 2 px, così si riconosce come presa (richiesta dell'utente)
SEPARATOR_RGBA = (0.5, 0.5, 0.5, 0.3)
HANDLE_RGBA = (0.35, 0.35, 0.35, 0.9)
DEFAULT_PX_PER_WHOLE = 64.0  # 4/4 → 64 px per misura
ZOOM_STEP = 1.25


def key_label(key: Key) -> str:
    """Nome inglese della tonalità: "D minor (Dm)", "Bb major (Bb)"."""
    mode = "major" if key.mode is MAJOR else "minor"
    return f"{key.tonic} {mode} ({key})"


class TracksView(Gtk.Box):
    def __init__(
        self,
        transport: Transport,
        on_properties: Callable[[Track], None],
        on_open_selection: Callable[[], None],
        get_mode: Callable[[], str] = lambda: "select",
        on_message: Callable[[str], None] = lambda _t: None,
        segment_menu=None,
        track_menu=None,
    ) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self.transport = transport
        self.on_properties, self.on_open_selection = on_properties, on_open_selection
        self.get_mode = get_mode
        self.on_message = on_message
        self.segment_menu = segment_menu  # menu Segment, usato anche col tasto destro
        self.track_menu = track_menu  # menu Track, col tasto destro sul nome di una traccia
        self.reordering: dict | None = None  # Track > Reorder in corso: {"track", "row", "orig"}
        self.name_width = DEFAULT_NAME_WIDTH
        self._name_widgets: list[Gtk.Widget] = []  # tutto ciò che è largo quanto la colonna dei nomi
        self._move: dict | None = None  # spostamento in corso (strumento Move)
        self._split_hint: tuple[TrackRow, float] | None = None  # (riga, x della corsia) dove cadrebbe il taglio
        self._resize: dict | None = None  # ridimensionamento in corso (strumento Resize)
        self._create: dict | None = None  # segmento in creazione (strumento Create)
        self.clipboard: list[dict] = []  # segmenti copiati (Edit > Copy / Cut)
        self._paste_fail: list[tuple[Track, float, float]] = []  # incolla fallito: dove sarebbe andato (in rosso)
        self._paste_fail_timer = 0
        self._band: tuple[float, float, float, float] | None = None  # rettangolo di selezione (coord. elenco)
        self._band_add = False
        # chiamata a ogni cambio di selezione (MainWindow: voci del menu Segment attive o nascoste)
        self.on_selection: Callable[[], None] = lambda: None
        # 2026-11-24: chiamata a ogni cambio di Mute / Solo / Preview (MainWindow: spunte del menu Track)
        self.on_listen: Callable[[], None] = lambda: None
        # 2026-11-24: click sulla "V" del righello (CC7 sul primo movimento della misura): la finestra dei volumi;
        # 2026-11-25: il play va su quella misura e si apre il mixer
        self.on_bar_volumes: Callable[[int], None] = lambda _bar: None
        self.selection: list[tuple[Track, Segment]] = []  # segmenti selezionati, in ordine di click
        self.doc: Document | None = None
        self.rows: list[TrackRow] = []
        self.ruler: RulerRow | None = None
        self.px_per_whole = DEFAULT_PX_PER_WHOLE
        self.hadj = Gtk.Adjustment(step_increment=40, page_increment=400)
        self.hadj.connect("value-changed", lambda *_: self.redraw_all())
        self.bar_x: list[float] = []  # inizio di ogni misura (semibrevi), + fine brano
        self.beat_x: list[float] = []
        self.min_beat = 0.25
        self.play_pos = 0.0  # posizione del cursore (semibrevi)
        self._last_secs = -1.0
        self._pointer_x = 0.0
        self._name_drag: tuple[int, float | None] = (0, None)  # trascinamento del bordo dei nomi: (larghezza, x)

        # righello fisso: ListBox a parte con lo stesso stile, così resta allineato alle corsie
        self.header = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
        self.header.add_css_class("navigation-sidebar")
        self.append(self.header)

        self.list = Gtk.ListBox(selection_mode=Gtk.SelectionMode.SINGLE)
        self.list.add_css_class("navigation-sidebar")
        self.scroller = Gtk.ScrolledWindow(vexpand=True, hscrollbar_policy=Gtk.PolicyType.NEVER)
        self.scroller.set_child(self.list)
        # rettangolo di selezione disegnato sopra l'elenco
        self.band_area = Gtk.DrawingArea(can_target=False)
        self.band_area.set_draw_func(self._draw_band)
        overlay = Gtk.Overlay(vexpand=True)
        overlay.set_child(self.scroller)
        overlay.add_overlay(self.band_area)
        self.append(overlay)
        drag = Gtk.GestureDrag()
        drag.connect("drag-begin", self._on_drag_begin)
        drag.connect("drag-update", self._on_drag_update)
        drag.connect("drag-end", self._on_drag_end)
        self.list.add_controller(drag)
        # Track > Reorder: la linea d'inserimento segue il mouse, il click rilascia la traccia
        reorder_motion = Gtk.EventControllerMotion()
        reorder_motion.connect("motion", lambda _c, _x, y: self._reorder_hover(y))
        self.list.add_controller(reorder_motion)
        reorder_click = Gtk.GestureClick(propagation_phase=Gtk.PropagationPhase.CAPTURE)
        reorder_click.connect("pressed", self._reorder_pressed)
        self.list.add_controller(reorder_click)

        # barra di scorrimento orizzontale, allineata alle corsie
        hbar_list = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
        hbar_list.add_css_class("navigation-sidebar")
        hbar_row = Gtk.ListBoxRow(selectable=False, activatable=False)
        hbar_box = Gtk.Box(margin_start=6, margin_end=6)
        hbar_box.append(self.name_spacer())
        hbar_box.append(Gtk.Box(width_request=GAP))
        hbar_box.append(Gtk.Scrollbar(orientation=Gtk.Orientation.HORIZONTAL, adjustment=self.hadj, hexpand=True))
        hbar_row.set_child(hbar_box)
        hbar_list.append(hbar_row)
        self.append(hbar_list)

        # toolbar inferiore: dati del brano (doppio click = Song Properties) e zoom
        bottom = Gtk.Box(spacing=18, margin_start=12, margin_end=6, margin_top=2, margin_bottom=2)
        self.on_song_properties: Callable[[], None] = lambda: None
        song_info = Gtk.Box(spacing=18, tooltip_text="Double-click: song properties")
        self.key_label = Gtk.Label()
        self.tempo_label = Gtk.Label()
        self.meter_label = Gtk.Label()
        self.bars_label = Gtk.Label()
        for lbl in (self.key_label, self.tempo_label, self.meter_label, self.bars_label):
            song_info.append(lbl)
        info_click = Gtk.GestureClick()
        info_click.connect("pressed", lambda _g, n, _x, _y: n == 2 and self.on_song_properties())
        song_info.add_controller(info_click)
        bottom.append(song_info)
        bottom.append(Gtk.Box(hexpand=True))
        zoom = Gtk.Box(spacing=2)
        for icon, step in (("zoom-out-symbolic", -1), ("zoom-in-symbolic", 1)):
            b = Gtk.Button(icon_name=themed(icon), tooltip_text="Zoom")
            b.add_css_class("flat")
            b.set_focus_on_click(False)
            b.connect("clicked", lambda _b, s=step: self.zoom_step(s))
            zoom.append(b)
        bottom.append(zoom)
        self.append(Gtk.Separator())
        self.append(bottom)

        self._timer = GLib.timeout_add(40, self._on_tick)

    # --- colonna dei nomi ----------------------------------------------------

    def name_handle(self, draw_line: bool) -> Gtk.DrawingArea:
        """Spazio fra il nome e la corsia (GAP px) che, trascinato, allarga o stringe la colonna dei nomi.
        Nel righello disegna la linea (draw_line); nelle righe delle tracce la linea è quella sopra
        l'elenco (_draw_name_separator) e qui c'è solo la presa.
        2026-11-24: prima solo nel righello; ora per tutta l'altezza della linea (richiesta dell'utente)."""
        handle = Gtk.DrawingArea(width_request=GAP, tooltip_text="Drag to widen or narrow the name column")
        handle.set_cursor(Gdk.Cursor.new_from_name("col-resize"))
        if draw_line:
            handle.set_draw_func(_draw_grip)
        drag = Gtk.GestureDrag()

        def view_x(gesture: Gtk.GestureDrag) -> float | None:
            # coordinate della vista Tracce (ferma), non della maniglia (che si sposta):
            # usare quelle della maniglia fa "vibrare" la colonna
            ok, x, y = gesture.get_point(None)
            point = _translate(handle, self, x, y) if ok else None
            return point[0] if point else None

        def begin(gesture, _x, _y):
            # il trascinamento è della maniglia: niente rettangolo di selezione né click sulla riga
            gesture.set_state(Gtk.EventSequenceState.CLAIMED)
            self._name_drag = (self.name_width, view_x(gesture))

        def update(gesture, _dx, _dy):
            x = view_x(gesture)
            w0, x0 = self._name_drag
            if x is not None and x0 is not None:
                self.set_name_width(w0 + (x - x0))

        drag.connect("drag-begin", begin)
        drag.connect("drag-update", update)
        handle.add_controller(drag)
        return handle

    def name_spacer(self) -> Gtk.Box:
        box = Gtk.Box(hexpand=False)
        self.register_name_widget(box)
        return box

    def register_name_widget(self, widget: Gtk.Widget) -> None:
        widget.set_size_request(self.name_width, -1)
        self._name_widgets.append(widget)

    def set_name_width(self, width: int) -> None:
        self.name_width = int(min(max(width, 60), 600))
        alive = []
        for w in self._name_widgets:
            if w.get_parent() is not None:
                w.set_size_request(self.name_width, -1)
                alive.append(w)
        self._name_widgets = alive
        GLib.idle_add(lambda: self.update_hadj() or False)
        GLib.idle_add(lambda: self.band_area.queue_draw() or False)  # la linea fra nomi e corsie si sposta

    # --- documento -------------------------------------------------------

    def set_document(self, doc: Document) -> None:
        if self.doc:
            self.doc.unsubscribe(self.refresh)
        self.doc = doc
        self.reordering = None
        for box in (self.header, self.list):
            while (row := box.get_row_at_index(0)) is not None:
                box.remove(row)
        self.ruler = RulerRow(self, doc)
        self.header.append(self.ruler)
        self.rows = [TrackRow(self, doc, t) for t in doc.song.tracks]
        for r in self.rows:
            self.list.append(r)
        GLib.idle_add(lambda: self.band_area.queue_draw() or False)  # linea fra nomi e corsie, col righello nuovo
        self.set_mode_cursor(self.get_mode())
        if self.rows:
            self.list.select_row(self.rows[0])
        self.selection = []
        self.hadj.set_value(0)
        doc.subscribe(self.refresh)
        self.refresh(doc)

    def refresh(self, doc: Document) -> None:
        """A ogni modifica del documento: dati del brano, righe delle tracce, selezione, disegno."""
        song = doc.song
        if self.reordering is not None:
            # 2026-11-20: una modifica durante Track > Reorder (Ctrl+Z, Ctrl+T, fine di una
            # registrazione…) ricrea le righe: il riordino si annulla, altrimenti la riga in
            # spostamento non esisteva più e l'elenco smetteva di rispondere fino a Esc
            self.cancel_reorder()
        # 2026-11-20: dopo undo/redo la selezione può riferirsi a segmenti tolti dal brano o
        # tornati su un'altra traccia (es. Move + Ctrl+Z): restano solo i segmenti presenti,
        # ciascuno con la traccia che lo contiene davvero (confronto per identità).
        owner = {id(s): t for t in song.tracks for s in t.segments}
        self.selection = [(owner[id(s)], s) for _t, s in self.selection
                          if id(s) in owner and s.group_id in song.groups]
        self.bar_x =[float(song.meters.bar_start(b)) for b in range(song.bar_count + 1)]
        _bars, beats = ruler.bar_and_beat_positions(song, 0, song.bar_count)
        self.beat_x = [float(p) for p in beats]
        self.min_beat = min(float(e.value.beat) for e in song.meters)
        self.key_label.set_label(f"Key: {key_label(song.keys.initial)}")
        tempo = f"Tempo: {song.tempos.initial:g} BPM"
        if len(song.tempos) > 1:
            tempo += f" (+{len(song.tempos) - 1} changes)"
        self.tempo_label.set_label(tempo)
        meter = f"Time signature: {song.meters.initial}"
        if len(song.meters) > 1:
            meter += f" (+{len(song.meters) - 1} changes)"
        self.meter_label.set_label(meter)
        self.bars_label.set_label(f"{song.bar_count} bars · {len(song.tracks)} tracks")
        if [r.track for r in self.rows] != song.tracks:
            self._rebuild_rows()
        if self.ruler:
            self.ruler.leds.get_first_child().sync()  # M generale: tracce aggiunte/tolte
        for r in self.rows:
            r.refresh()
        self.update_hadj()
        self.redraw_all()

    # --- selezione ----------------------------------------------------------

    @property
    def selection(self) -> list[tuple[Track, Segment]]:
        """Segmenti selezionati (traccia, segmento), in ordine di click."""
        return self._selection

    @selection.setter
    def selection(self, value: list[tuple[Track, Segment]]) -> None:
        # 2026-11-24: proprietà invece di attributo, per avvisare la finestra a ogni cambio (menu Segment:
        # Make Independent solo sui cloni, solo gli editor possibili). Si assegna sempre una lista nuova.
        self._selection = value
        self.on_selection()

    # --- scala e scorrimento --------------------------------------------

    @property
    def pad(self) -> float:
        """Mezza battuta vuota prima della misura 1 (px): l'inizio si prende agevolmente.
        2026-11-24: prima una battuta intera; dimezzata su richiesta dell'utente (troppo spazio fra la
        linea della colonna dei nomi e la misura 1)."""
        beat = float(self.doc.song.meters.at(0).beat) if self.doc else 0.25
        return beat / 2 * self.px_per_whole

    def x_of(self, pos: float) -> float:
        return self.pad + pos * self.px_per_whole - self.hadj.get_value()

    def pos_of(self, x: float) -> float:
        return (x - self.pad + self.hadj.get_value()) / self.px_per_whole

    def view_width(self) -> int:
        return self.ruler.area.get_width() if self.ruler else 0

    def update_hadj(self) -> None:
        if not self.doc:
            return
        page = max(self.view_width(), 1)
        # 2026-11-23: dopo la fine lo stesso spazio vuoto dell'inizio (una battuta, almeno 20 px), richiesta
        # dell'utente: prima solo 20 px, con lo zoom alto la fine del brano (es. per incollare lì) si prendeva a fatica
        upper = max(self.bar_x[-1] * self.px_per_whole + max(self.pad, 20) + self.pad, page) if self.bar_x else page
        value = min(self.hadj.get_value(), max(upper - page, 0))
        self.hadj.configure(value, 0, upper, 40, page * 0.9, page)

    def zoom_step(self, direction: int, anchor_x: float | None = None) -> None:
        """+1 ingrandisce, -1 rimpicciolisce, 0 torna allo zoom normale."""
        if direction == 0:
            new = DEFAULT_PX_PER_WHOLE
        else:
            new = self.px_per_whole * ZOOM_STEP**direction
        new = min(max(new, 4.0), 2000.0)
        ax = self.view_width() / 2 if anchor_x is None else anchor_x
        pos = self.pos_of(ax)
        self.px_per_whole = new
        self.update_hadj()
        self.hadj.set_value(max(pos * new - ax, 0))
        self.redraw_all()

    def on_scroll(self, ctrl: Gtk.EventControllerScroll, dx: float, dy: float) -> bool:
        """Rotella: avanti/indietro lungo la timeline; Maiusc+rotella: su/giù
        nell'elenco delle tracce; Ctrl+rotella: zoom."""
        state = ctrl.get_current_event_state()
        if state & PRIMARY_MASK:
            self.zoom_step(1 if dy < 0 else -1, self._pointer_x)
        elif state & Gdk.ModifierType.SHIFT_MASK:
            vadj = self.scroller.get_vadjustment()
            vadj.set_value(vadj.get_value() + (dy or dx) * 40)
        else:
            self.hadj.set_value(self.hadj.get_value() + (dy + dx) * 40)
        return True

    # --- selezione dei segmenti -------------------------------------------

    def seg_span(self, seg: Segment) -> tuple[float, float]:
        a, b = self.doc.song.segment_span(seg)
        return float(a), float(b)

    def segment_at(self, track: Track, x: float) -> Segment | None:
        pos = self.pos_of(x)
        for seg in track.segments:
            a, b = self.seg_span(seg)
            if a <= pos < b:
                return seg
        return None

    def is_selected(self, seg: Segment) -> bool:
        return any(s is seg for _t, s in self.selection)

    def click_segment(self, track: Track, seg: Segment | None, toggle: bool = False, add: bool = False) -> None:
        """Click su un segmento: sostituisce la selezione, oppure aggiunge (add) o inverte (toggle)."""
        if toggle:
            if seg is not None:
                if self.is_selected(seg):
                    self.selection = [(t, s) for t, s in self.selection if s is not seg]
                else:
                    self.selection = self.selection + [(track, seg)]
        elif add:
            if seg is not None and not self.is_selected(seg):
                self.selection = self.selection + [(track, seg)]
        else:
            self.selection = [(track, seg)] if seg is not None else []
        self.redraw_all()

    def select_track_segments(self) -> None:
        """Track > Select All Segments (Ctrl+A nella scheda Tracce): seleziona tutti i
        segmenti della traccia evidenziata, così E/G aprono la traccia intera restando
        dentro i suoi segmenti (2026-11-20)."""
        track = self.selected_track()
        if track is None:
            self.on_message("Select a track first.")
            return
        self.selection = [(track, seg) for seg in track.segments]
        self.redraw_all()

    # --- rettangolo di selezione ------------------------------------------

    def _on_drag_begin(self, gesture: Gtk.GestureDrag, x: float, y: float) -> None:
        self._drag_start = (x, y)
        self._band = None
        self._move = None
        self._band_add = bool(gesture.get_current_event_state() & Gdk.ModifierType.SHIFT_MASK)
        self._resize = None
        self._create = None
        if self.get_mode() == "create" and not self._band_add:
            self._begin_create(x, y)
        elif self.get_mode() == "move" and not self._band_add:  # con Maiusc è un gesto di selezione
            self._begin_move(x, y)
        elif self.get_mode() == "resize" and not self._band_add:
            self._begin_resize(x, y)

    def _on_drag_update(self, _g, dx: float, dy: float) -> None:
        if self._move is not None:  # Move partito su un segmento
            self._update_move(dx, dy)
            return
        if self._resize is not None:  # Resize partito su un bordo
            self._update_resize(dx)
            return
        if self._create is not None:  # Create partito in uno spazio vuoto
            self._update_create(dx)
            return
        if not self.rows:  # rettangolo di selezione: sempre attivo, in ogni modalità
            return
        if self._band is None and abs(dx) < 4 and abs(dy) < 4:
            return  # sotto la soglia: è ancora un click
        x0, y0 = self._drag_start
        self._band = (min(x0, x0 + dx), min(y0, y0 + dy), max(x0, x0 + dx), max(y0, y0 + dy))
        self.band_area.queue_draw()

    def _on_drag_end(self, _g, _dx: float, _dy: float) -> None:
        if self._move is not None:
            self._end_move()
            return
        if self._resize is not None:
            self._end_resize()
            return
        if self._create is not None:
            self._end_create()
            return
        band, self._band = self._band, None
        self.band_area.queue_draw()
        if band is None:
            return
        self.select_rect(*band, add=self._band_add)

    def select_rect(self, x0: float, y0: float, x1: float, y1: float, add: bool = False) -> None:
        """Seleziona i segmenti toccati dal rettangolo (coordinate dell'elenco tracce)."""
        origin = _translate(self.rows[0].lane, self.list, 0, 0)
        if origin is None:
            return
        lane_x = origin[0]
        p0, p1 = self.pos_of(x0 - lane_x), self.pos_of(x1 - lane_x)
        found: list[tuple[Track, Segment]] = []
        for row in self.rows:
            ok, bounds = row.compute_bounds(self.list)
            if not ok or bounds.get_y() > y1 or bounds.get_y() + bounds.get_height() < y0:
                continue
            for seg in row.track.segments:
                s0, s1 = self.seg_span(seg)
                if s0 < p1 and s1 > p0:
                    found.append((row.track, seg))
        if add:
            for t, seg in found:
                if not self.is_selected(seg):
                    self.selection = self.selection + [(t, seg)]
        else:
            self.selection = found
        self.redraw_all()

    # --- strumento Move -----------------------------------------------------

    def _lane_x0(self) -> float:
        origin = _translate(self.rows[0].lane, self.list, 0, 0) if self.rows else None
        return origin[0] if origin else 0.0

    def _row_index_at(self, y: float) -> int | None:
        for i, row in enumerate(self.rows):
            ok, b = row.compute_bounds(self.list)
            if ok and b.get_y() <= y < b.get_y() + b.get_height():
                return i
        return None

    def _begin_move(self, x: float, y: float) -> None:
        i = self._row_index_at(y)
        if i is None:
            return
        track = self.rows[i].track
        seg = self.segment_at(track, x - self._lane_x0())
        if seg is None:
            return
        # la pressione non tocca la selezione (così click e Maiusc+click restano gesti di
        # selezione): si sposta la selezione se il segmento ne fa parte, altrimenti solo lui
        items = self.selection if self.is_selected(seg) else [(track, seg)]
        index = {id(r.track): k for k, r in enumerate(self.rows)}
        self._move = {"anchor": seg, "row0": i, "items": [(t, s, index[id(t)]) for t, s in items],
                      "moves": [], "ok": True, "changed": False}

    def _update_move(self, dx: float, dy: float) -> None:
        m = self._move
        if m is None:
            return
        x0, y0 = self._drag_start
        song = self.doc.song
        anchor_start = song.segment_span(m["anchor"])[0]
        wanted = anchor_start + Fraction(dx / self.px_per_whole).limit_denominator(1 << 12)
        delta = song.snap_to_beat(wanted) - anchor_start  # aggancio al movimento (battuta)
        target = self._row_index_at(y0 + dy)
        row_delta = (target - m["row0"]) if target is not None else m.get("row_delta", 0)
        m["row_delta"] = row_delta
        moves, inside = [], True
        for track, seg, k in m["items"]:
            j = k + row_delta
            if not 0 <= j < len(self.rows):
                inside = False
                j = min(max(j, 0), len(self.rows) - 1)
            start = song.segment_span(seg)[0] + delta
            if start < 0:
                inside = False
                start = Fraction(0)
            bar, offset = song.split_position(start)
            moves.append((seg, track, self.rows[j].track, bar, offset))
        m["moves"] = moves
        m["ok"] = inside and MoveSegments.fits(song, moves)
        m["changed"] = delta != 0 or row_delta != 0
        self.band_area.queue_draw()
        for r in self.rows:  # segmenti di partenza attenuati
            r.lane.queue_draw()

    def _end_move(self) -> None:
        m, self._move = self._move, None
        self.band_area.queue_draw()
        self.redraw_all()
        if not m or not m["changed"] or not self.doc:
            return
        if not m["ok"]:
            self.on_message("Not enough room: move cancelled.")
            return
        self.doc.execute(MoveSegments(m["moves"]))
        self.selection = [(dst, seg) for seg, _src, dst, _bar, _off in m["moves"]]
        self.redraw_all()

    def moving(self, seg: Segment) -> bool:
        """True se il segmento è in corso di spostamento (disegnato attenuato nella posizione di partenza)."""
        m = self._move
        return bool(m and m["changed"] and any(s is seg for s, *_ in m["moves"]))

    def _draw_move(self, cr) -> None:
        """Anteprima: il segmento nel suo colore, pieno e luminoso, con bordo spesso
        azzurro (c'è spazio) o rosso (non c'è spazio)."""
        m = self._move
        if not m or not m["changed"]:
            return
        song = self.doc.song
        lane_x = self._lane_x0()
        ok = m["ok"]
        edge = (0.13, 0.55, 1.0) if ok else (0.95, 0.15, 0.15)
        for seg, _src, dst, bar, offset in m["moves"]:
            row = next((r for r in self.rows if r.track is dst), None)
            if row is None:
                continue
            okb, b = row.lane.compute_bounds(self.band_area)
            if not okb:
                continue
            start = float(song.meters.bar_start(bar) + offset)
            end = start + float(seg.length)
            p = _translate(self.list, self.band_area, lane_x + self.x_of(start), 0)
            q = _translate(self.list, self.band_area, lane_x + self.x_of(end), 0)
            if p is None or q is None:
                continue
            r, g, bl = group_color(song, seg.group_id) if ok else (0.95, 0.2, 0.2)  # impossibile: tutto rosso
            cr.rectangle(p[0], b.get_y() + 1, q[0] - p[0], b.get_height() - 2)
            cr.set_source_rgba(r, g, bl, 0.85)
            cr.fill_preserve()
            cr.set_source_rgb(*edge)
            cr.set_line_width(3)
            cr.stroke()

    # --- strumento Split ----------------------------------------------------

    def split_target(self, track: Track, x: float):
        """Segmento sotto x e punto di taglio agganciato alla battuta:
        (segmento, taglio in semibrevi dall'inizio del segmento, x del taglio).
        Taglio 0 o uguale alla durata = bordo del segmento."""
        seg = self.segment_at(track, x)
        if seg is None:
            return None
        song = self.doc.song
        start, end = song.segment_span(seg)
        at = song.snap_to_beat(Fraction(self.pos_of(x)).limit_denominator(1 << 12))
        at = min(max(at, start), end)
        return seg, at - start, self.x_of(float(at))

    def set_mode_cursor(self, mode: str) -> None:
        """Puntatore delle corsie (scelti dall'utente fra quelli del tema): quattro frecce in
        Move, barra verticale in Split (provvisorio), X in Delete, ↔ sui bordi in Resize,
        normale in Select."""
        if mode == "move":
            cursor = Gdk.Cursor.new_from_name("move")  # quattro frecce
        elif mode == "delete":
            cursor = Gdk.Cursor.new_from_name("X_cursor", Gdk.Cursor.new_from_name("default"))  # X
        elif mode == "create":  # "+" del tema (mirino se il tema non ce l'ha)
            cursor = Gdk.Cursor.new_from_name("plus", Gdk.Cursor.new_from_name("crosshair"))
        elif mode == "split":  # provvisorio: barra verticale, in attesa di uno migliore
            cursor = Gdk.Cursor.new_from_name("ibeam", Gdk.Cursor.new_from_name("text"))
        else:
            cursor = None  # Select: puntatore normale
        for r in self.rows:
            r.lane.set_cursor(cursor)
        if mode != "split":
            self._split_hint = None
            self.band_area.queue_draw()

    def split_hover(self, row: "TrackRow | None", x: float = 0.0) -> None:
        self._split_hint = (row, x) if row is not None and self.get_mode() == "split" else None
        self.band_area.queue_draw()
        if row is not None and self.get_mode() == "resize":  # ↔ quando si è sopra un bordo
            edge = self.edge_at(row.track, x)
            row.lane.set_cursor(Gdk.Cursor.new_from_name("col-resize") if edge else None)

    def split_click(self, track: Track, x: float) -> None:
        target = self.split_target(track, x)
        if target is None or not self.doc:
            return
        seg, cut, _x = target
        if cut <= 0 or cut >= seg.length:
            self.on_message("There's already a segment boundary here: nothing to split.")
            return
        self.doc.execute(SplitSegment(track, seg, cut))
        self.selection = [(t, s) for t, s in self.selection if s is not seg]
        self.redraw_all()

    def _draw_split_hint(self, cr) -> None:
        if self._split_hint is None:
            return
        row, x = self._split_hint
        target = self.split_target(row.track, x)
        if target is None:
            return
        seg, cut, line_x = target
        if cut <= 0 or cut >= seg.length:
            return  # bordo: non si divide
        lane_x = self._lane_x0()
        okb, b = row.lane.compute_bounds(self.band_area)
        p = _translate(self.list, self.band_area, lane_x + line_x, 0)
        if not okb or p is None:
            return
        cr.set_source_rgb(0.13, 0.55, 1.0)
        cr.set_line_width(2)
        cr.set_dash([4, 3])
        cr.move_to(p[0], b.get_y())
        cr.line_to(p[0], b.get_y() + b.get_height())
        cr.stroke()
        cr.set_dash([])

    # --- strumento Resize ---------------------------------------------------

    EDGE_PX = 6  # distanza dal bordo del segmento entro cui si afferra

    def edge_at(self, track: Track, x: float) -> tuple[Segment, str] | None:
        """Bordo di segmento sotto x: (segmento, "start" | "end")."""
        best = None
        for seg in track.segments:
            a, b = self.seg_span(seg)
            for side, pos in (("start", a), ("end", b)):
                d = abs(self.x_of(pos) - x)
                if d <= self.EDGE_PX and (best is None or d < best[0]):
                    best = (d, seg, side)
        return (best[1], best[2]) if best else None

    def _begin_resize(self, x: float, y: float) -> None:
        i = self._row_index_at(y)
        if i is None:
            return
        track = self.rows[i].track
        edge = self.edge_at(track, x - self._lane_x0())
        if edge is None:
            return  # lontano dai bordi: il trascinamento resta il rettangolo di selezione
        seg, side = edge
        start, end = self.doc.song.segment_span(seg)
        self._resize = {"track": track, "seg": seg, "side": side, "start": start, "end": end,
                        "new": (start, end), "ok": True, "changed": False}

    def _update_resize(self, dx: float) -> None:
        r = self._resize
        song = self.doc.song
        start, end = r["start"], r["end"]
        edge = start if r["side"] == "start" else end
        moved = song.snap_to_beat(edge + Fraction(dx / self.px_per_whole).limit_denominator(1 << 12))
        if r["side"] == "start":
            beat = song.meters.at(song.meters.bar_at(max(end - Fraction(1, 64), Fraction(0)))).beat
            new = (min(moved, end - beat), end)
        else:
            beat = song.meters.at(song.meters.bar_at(start)).beat
            new = (start, max(moved, start + beat))
        others = [song.segment_span(s) for s in r["track"].segments if s is not r["seg"]]
        r["new"] = new
        r["ok"] = new[0] >= 0 and not any(a < new[1] and new[0] < b for a, b in others)
        r["changed"] = new != (start, end)
        self.band_area.queue_draw()

    def _end_resize(self) -> None:
        r, self._resize = self._resize, None
        self.band_area.queue_draw()
        if not r or not r["changed"]:
            return
        if not r["ok"]:
            self.on_message("Not enough room: resize cancelled.")
            return
        self.doc.execute(ResizeSegment(r["seg"], *r["new"]))
        self.redraw_all()

    def _draw_resize(self, cr) -> None:
        r = self._resize
        if not r or not r["changed"]:
            return
        row = next((w for w in self.rows if w.track is r["track"]), None)
        okb, b = row.lane.compute_bounds(self.band_area) if row else (False, None)
        lane_x = self._lane_x0()
        p = _translate(self.list, self.band_area, lane_x + self.x_of(float(r["new"][0])), 0)
        q = _translate(self.list, self.band_area, lane_x + self.x_of(float(r["new"][1])), 0)
        if not okb or p is None or q is None:
            return
        color = group_color(self.doc.song, r["seg"].group_id) if r["ok"] else (0.95, 0.2, 0.2)
        cr.rectangle(p[0], b.get_y() + 1, q[0] - p[0], b.get_height() - 2)
        cr.set_source_rgba(*color, 0.85)
        cr.fill_preserve()
        cr.set_source_rgb(*((0.13, 0.55, 1.0) if r["ok"] else (0.95, 0.15, 0.15)))
        cr.set_line_width(3)
        cr.stroke()

    # --- strumento Delete e tasto Canc ---------------------------------------

    def delete_click(self, track: Track, x: float) -> None:
        """Delete: cancella il segmento cliccato; se è selezionato, tutta la selezione."""
        seg = self.segment_at(track, x)
        if seg is None or not self.doc:
            return
        items = list(self.selection) if self.is_selected(seg) else [(track, seg)]
        self._delete(items)

    def delete_selection(self) -> None:
        if not self.selection:
            self.on_message("No segments selected.")
            return
        self._delete(list(self.selection))

    # --- Edit: taglia / copia / incolla ---------------------------------------

    def copy_selection(self) -> bool:
        """Copia i segmenti selezionati (contenuto compreso) negli appunti dell'app."""
        if not self.selection or not self.doc:
            self.on_message("No segments selected.")
            return False
        song = self.doc.song
        first = min(song.segment_span(s)[0] for _t, s in self.selection)
        self.clipboard = []
        for track, seg in self.selection:
            group = song.groups[seg.group_id]
            self.clipboard.append({
                "track": track,
                # 2026-11-23: brano e posizione della traccia, per incollare in un altro brano (core/paste.py)
                "song": song, "track_index": song.tracks.index(track),
                "rel": song.segment_span(seg)[0] - first,
                "length": seg.length, "transpose": seg.transpose, "trim": seg.trim, "label": seg.label,
                "arrangement": seg.arrangement(),
                "name": group.name, "length_bars": group.length_bars,
                "group": group, "origin": seg.clone_of or seg.id,  # per l'incolla collegato
                "content": group.copy(),  # per l'incolla indipendente (note, staccato, slur)
            })
        self.on_message(f"Copied {len(self.clipboard)} segments.", False)
        return True

    def cut_selection(self) -> None:
        if self.copy_selection():
            self._delete(list(self.selection))
            self.on_message(f"Cut {len(self.clipboard)} segments.", False)

    def paste_at_play(self, linked: bool = True) -> None:
        """Incolla gli appunti sul punto di play (la linea rossa, agganciato alla battuta) e sulla
        traccia evidenziata. Con segmenti di più tracce la prima traccia copiata va lì e le altre
        sotto, mantenendo le distanze. linked=True (Ctrl+V): cloni, stesso gruppo dell'originale;
        linked=False (Ctrl+Shift+V): copie indipendenti. Annullato se non c'è spazio o mancano
        tracce. Appunti copiati in un altro brano: sempre copie indipendenti (core/paste.py).
        2026-11-23: prima incollava al puntatore del mouse; l'utente si aspettava il punto di play
        (come nella notazione) e incollando a fine brano i segmenti finivano dove stava il mouse."""
        if not self.clipboard or not self.doc:
            self.on_message("Nothing to paste.")
            return
        song = self.doc.song
        track = self.selected_track()
        if track is None or track not in song.tracks:
            self.on_message("Click the name of the track where you want to paste.")
            return
        here = Fraction(song.tempos.position(self.transport.position)).limit_denominator(1 << 12)
        at = song.snap_to_beat(here)
        if all(c.get("song", song) is not song for c in self.clipboard):
            # 2026-11-23: appunti copiati in un altro brano: copie indipendenti dalla traccia evidenziata in giù
            items = paste_from_other_song(song, self.clipboard, song.tracks.index(track), at)
            if items is None:
                self.on_message("Not enough tracks below the highlighted one: paste cancelled.")
                return
            if self._add_pasted(items):
                self.on_message(f"Pasted {len(items)} segments from another song as independent copies.", False)
            return
        sources = [song.tracks.index(c["track"]) for c in self.clipboard if c["track"] in song.tracks]
        if not sources:  # 2026-11-20: prima nessun messaggio (traccia cancellata o altro brano aperto)
            self.on_message("The copied tracks are no longer in the song: nothing to paste.")
            return
        shift = song.tracks.index(track) - min(sources)
        if max(sources) + shift >= len(song.tracks):
            self.on_message("Not enough tracks below the highlighted one: paste cancelled.")
            return
        items = []
        heirs: dict[str, str] = {}  # originale sparito → id del segmento incollato che lo sostituisce
        for c in self.clipboard:
            if c["track"] not in song.tracks:
                continue
            dest = song.tracks[song.tracks.index(c["track"]) + shift]
            bar, offset = song.split_position(at + c["rel"])
            if linked:  # clone: stesso gruppo (rimesso nel brano se nel frattempo era uscito, es. dopo Cut)
                group = c["group"]
                origin = heirs.get(c["origin"], c["origin"])
                # un originale nato in questo stesso Paste non è ancora nel brano: conta come presente
                # (2026-11-20: prima Cut di originale + clone e Paste davano due originali dello stesso gruppo)
                present = origin in heirs.values() or song.segment_by_id(origin) is not None
                if not present:  # originale sparito: vale quello rimasto del gruppo
                    origin = next((x.id for t in song.tracks for x in t.segments
                                   if x.group_id == group.id and x.clone_of is None), origin)
                    present = song.segment_by_id(origin) is not None
                if not present:
                    # 2026-11-20: l'originale non c'è più (tipico: Cut + Paste per spostare un segmento):
                    # il primo incollato diventa l'originale, gli altri suoi cloni. Prima restava un
                    # clone di un segmento sparito, grigio e non modificabile.
                    seg = Segment(group.id, bar, c["length"], c["transpose"], offset, c["trim"], c.get("label", ""),
                                  **c["arrangement"])
                    heirs[c["origin"]] = seg.id
                else:
                    seg = Segment(group.id, bar, c["length"], c["transpose"], offset, c["trim"], clone_of=origin,
                                  **c["arrangement"])
            else:
                group = c["content"].copy(c["name"])
                seg = Segment(group.id, bar, c["length"], c["transpose"], offset, c["trim"], c.get("label", ""),
                              **c["arrangement"])
            items.append((dest, seg, group))
        if not items:
            return
        if self._add_pasted(items):
            for c in self.clipboard:  # i Paste successivi fanno cloni del nuovo originale
                c["origin"] = heirs.get(c["origin"], c["origin"])

    def _add_pasted(self, items: list) -> bool:
        """Aggiunge i segmenti incollati e li seleziona; se non c'è spazio li mostra in rosso
        e non aggiunge nulla. 2026-11-23: messo a parte per usarlo anche per l'incolla da un altro brano."""
        song = self.doc.song
        if not AddSegments.fits(song, items):
            self.on_message("Not enough room there: paste cancelled.")
            self._flash_paste_fail([(t, float(song.meters.bar_start(sg.start_bar) + sg.offset),
                                     float(song.meters.bar_start(sg.start_bar) + sg.offset + sg.length)) for t, sg, _g in items])
            return False
        self.doc.execute(AddSegments(items))
        self.selection = [(t, s) for t, s, _g in items]
        self.redraw_all()
        return True

    # --- Segment: duplica, unisci, etichetta -----------------------------------

    def _copy_segment(self, seg: Segment, start) -> tuple[Segment, Group]:
        """Copia indipendente (gruppo nuovo) di un segmento, che inizia in `start`."""
        song = self.doc.song
        g = song.groups[seg.group_id]
        group = g.copy()
        bar, offset = song.split_position(start)
        return Segment(group.id, bar, seg.length, seg.transpose, offset, seg.trim, seg.label,
                       **seg.arrangement()), group

    def duplicate_selection(self) -> None:
        """Duplica la selezione subito dopo la sua fine (anche più segmenti e più tracce,
        mantenendo le distanze) come copie collegate: stesso gruppo dell'originale.
        Annullato, con anteprima rossa, se non c'è spazio."""
        if not self.selection or not self.doc:
            self.on_message("No segments selected.")
            return
        song = self.doc.song
        spans = [song.segment_span(s) for _t, s in self.selection]
        shift = max(b for _a, b in spans) - min(a for a, _b in spans)
        items = []
        for (track, seg), (a, _b) in zip(self.selection, spans):
            bar, offset = song.split_position(a + shift)
            linked = Segment(seg.group_id, bar, seg.length, seg.transpose, offset, seg.trim, **seg.arrangement(),
                             clone_of=seg.clone_of or seg.id)  # clone dell'originale
            items.append((track, linked, song.groups[seg.group_id]))
        if not AddSegments.fits(song, items):
            self.on_message("Not enough room after the selection: duplicate cancelled.")
            self._flash_paste_fail([(t, float(a + shift), float(b + shift)) for (t, _s), (a, b) in zip(self.selection, spans)])
            return
        self.doc.execute(AddSegments(items))
        self.selection = [(t, s) for t, s, _g in items]
        self.redraw_all()

    def merge_selection(self) -> None:
        """Unisce, traccia per traccia, i segmenti selezionati in uno solo che va dal primo
        inizio all'ultima fine (spazi vuoti compresi); i segmenti non selezionati che stanno
        in mezzo vengono assorbiti anch'essi."""
        if not self.selection or not self.doc:
            self.on_message("No segments selected.")
            return
        song = self.doc.song
        by_track: dict[int, tuple[Track, list[Segment]]] = {}
        for t, sg in self.selection:
            by_track.setdefault(id(t), (t, []))[1].append(sg)
        removed, added = [], []
        for track, segs in by_track.values():
            if len(segs) < 2:
                continue
            lo = min(song.segment_span(sg)[0] for sg in segs)
            hi = max(song.segment_span(sg)[1] for sg in segs)
            inside = [sg for sg in track.segments if song.segment_span(sg)[0] < hi and song.segment_span(sg)[1] > lo]
            first = min(inside, key=lambda sg: song.segment_span(sg)[0])
            notes, slurs, chords, chord_events, events = [], [], [], [], []
            for sg in inside:  # segmento per segmento (due cloni hanno le stesse note: copie distinte)
                id_map = {}
                for p in song.placed_notes(track):
                    if p.segment is sg:
                        # durata mostrata: la coda nascosta da un Resize non torna (2026-11-20)
                        id_map[p.note.id] = p.note.copy(start=p.start - lo, midi=p.midi, duration=p.duration,
                                                        spelling=p.note.spelling if not sg.transpose else None)
                notes += id_map.values()
                slurs += remap_slurs(song.groups[sg.group_id].slurs, id_map)
                origin = song.segment_span(sg)[0] - lo  # sigle visibili del segmento, nella nuova posizione
                chords += [(origin + p - sg.trim, c) for p, c in song.groups[sg.group_id].chords
                           if 0 <= p - sg.trim < sg.length]
                chord_events += [replace(e, pos=origin + e.pos - sg.trim) for e in song.groups[sg.group_id].chord_events
                                 if 0 <= e.pos - sg.trim < sg.length]  # traccia accordi
                events += [replace(e, start=origin + e.start - sg.trim, id=new_id()) for e in song.groups[sg.group_id].events
                           if 0 <= e.start - sg.trim < sg.length]  # controller, pitch bend…
            notes.sort(key=lambda n: (n.start, n.midi))
            g = song.groups[first.group_id]
            group = Group(g.name, g.length_bars, notes, slurs=slurs, chords=sorted(chords), style=g.style,
                          chord_events=sorted(chord_events, key=lambda e: e.pos),
                          events=sorted(events, key=lambda e: e.start))
            bar, offset = song.split_position(lo)
            removed += [(track, sg) for sg in inside]
            added.append((track, Segment(group.id, bar, hi - lo, 0, offset, label=first.label, **first.arrangement()),
                          group))
        if not added:
            self.on_message("Select at least two segments of the same track to merge.")
            self._flash_paste_fail([(t, *map(float, song.segment_span(sg))) for t, sg in self.selection])
            return
        self.doc.execute(Compound([DeleteSegments(removed), AddSegments(added)], "Merge segments"))
        self.selection = [(t, sg) for t, sg, _g in added]
        self.redraw_all()

    def _begin_create(self, x: float, y: float) -> None:
        """Create: alla pressione compare subito il segmento nuovo (una battuta)."""
        i = self._row_index_at(y)
        if i is None:
            return
        track = self.rows[i].track
        lane_x = x - self._lane_x0()
        if self.segment_at(track, lane_x) is not None:
            return  # su un segmento: resta un gesto di selezione
        song = self.doc.song
        anchor = song.snap_to_beat(Fraction(max(self.pos_of(lane_x), 0.0)).limit_denominator(1 << 12))
        self._create = {"track": track, "anchor": anchor, "lane_x": lane_x}
        self._update_create(0.0)

    def _update_create(self, dx: float) -> None:
        c = self._create
        song = self.doc.song
        anchor = c["anchor"]
        beat = song.meters.at(song.meters.bar_at(anchor)).beat
        here = song.snap_to_beat(Fraction(max(self.pos_of(c["lane_x"] + dx), 0.0)).limit_denominator(1 << 12))
        start, end = (anchor, max(here, anchor + beat)) if here >= anchor else (here, anchor + beat)
        # 2026-11-20: nome predefinito in inglese (prima "Nuovo segmento"): UI tutta in inglese
        group = Group("New segment", 1, [])
        bar, offset = song.split_position(start)
        seg = Segment(group.id, bar, end - start, 0, offset)
        c["item"] = (c["track"], seg, group)
        c["span"] = (start, end)
        c["ok"] = AddSegments.fits(song, [c["item"]])
        self.band_area.queue_draw()

    def _end_create(self) -> None:
        c, self._create = self._create, None
        self.band_area.queue_draw()
        if not c or "item" not in c:
            return
        if not c["ok"]:
            self.on_message("Not enough room: new segment not created.")
            self._flash_paste_fail([(c["track"], float(c["span"][0]), float(c["span"][1]))])
            return
        self.doc.execute(AddSegments([c["item"]]))
        self.selection = [(c["track"], c["item"][1])]
        self.redraw_all()

    def _draw_create(self, cr) -> None:
        c = self._create
        if not c or "span" not in c:
            return
        row = next((r for r in self.rows if r.track is c["track"]), None)
        okb, b = row.lane.compute_bounds(self.band_area) if row else (False, None)
        lane_x = self._lane_x0()
        p = _translate(self.list, self.band_area, lane_x + self.x_of(float(c["span"][0])), 0)
        q = _translate(self.list, self.band_area, lane_x + self.x_of(float(c["span"][1])), 0)
        if not okb or p is None or q is None:
            return
        fill, edge = ((0.13, 0.55, 1.0), (0.13, 0.55, 1.0)) if c["ok"] else ((0.95, 0.2, 0.2), (0.95, 0.15, 0.15))
        cr.rectangle(p[0], b.get_y() + 1, q[0] - p[0], b.get_height() - 2)
        cr.set_source_rgba(*fill, 0.45)
        cr.fill_preserve()
        cr.set_source_rgb(*edge)
        cr.set_line_width(3)
        cr.stroke()

    def unlink_selection(self) -> None:
        """Rende indipendenti i cloni selezionati."""
        clones = [sg for _t, sg in self.selection if sg.clone_of is not None]
        if not clones:
            self.on_message("No clones selected.")
            return
        self.doc.execute(UnlinkSegments(clones))
        self.redraw_all()

    def show_segment_menu(self, widget: Gtk.Widget, x: float, y: float) -> None:
        """Menu Segment al tasto destro, nel punto del click."""
        self._popup_menu(self.segment_menu, widget, x, y)

    def show_track_menu(self, widget: Gtk.Widget, x: float, y: float) -> None:
        """Menu Track al tasto destro sul nome di una traccia, nel punto del click."""
        self._popup_menu(self.track_menu, widget, x, y)

    def _popup_menu(self, model, widget: Gtk.Widget, x: float, y: float) -> None:
        if model is None:
            return
        # agganciato alla vista Tracce (non alla corsia, alta pochi pixel) e senza
        # pagine scorrevoli: niente barra di scorrimento nel menu
        point = _translate(widget, self, x, y)
        if point is None:
            return
        popup_menu(model, self, point[0], point[1])

    def _flash_paste_fail(self, spans: list) -> None:
        """Mostra per un attimo, in rosso, dove sarebbero finiti i segmenti incollati."""
        self._paste_fail = spans
        self.band_area.queue_draw()
        if self._paste_fail_timer:
            GLib.source_remove(self._paste_fail_timer)

        def clear() -> bool:
            self._paste_fail, self._paste_fail_timer = [], 0
            self.band_area.queue_draw()
            return False

        self._paste_fail_timer = GLib.timeout_add(1200, clear)

    def _draw_paste_fail(self, cr) -> None:
        lane_x = self._lane_x0()
        for track, start, end in self._paste_fail:
            row = next((r for r in self.rows if r.track is track), None)
            okb, b = row.lane.compute_bounds(self.band_area) if row else (False, None)
            p = _translate(self.list, self.band_area, lane_x + self.x_of(start), 0)
            q = _translate(self.list, self.band_area, lane_x + self.x_of(end), 0)
            if not okb or p is None or q is None:
                continue
            cr.rectangle(p[0], b.get_y() + 1, q[0] - p[0], b.get_height() - 2)
            cr.set_source_rgba(0.95, 0.2, 0.2, 0.85)
            cr.fill_preserve()
            cr.set_source_rgb(0.95, 0.15, 0.15)
            cr.set_line_width(3)
            cr.stroke()

    def _delete(self, items: list) -> None:
        self.doc.execute(DeleteSegments(items))
        gone = {id(s) for _t, s in items}
        self.selection = [(t, s) for t, s in self.selection if id(s) not in gone]
        self.redraw_all()

    # --- tracce: aggiungi, elimina, riordina ------------------------------

    def _rebuild_rows(self) -> None:
        """Ricrea le righe quando l'elenco delle tracce cambia; resta evidenziata la stessa traccia."""
        song = self.doc.song
        current = self.selected_track()
        old_index = next((i for i, r in enumerate(self.rows) if r.track is current), 0)
        for r in self.rows:
            self.list.remove(r)
        self.rows = [TrackRow(self, self.doc, t) for t in song.tracks]
        for r in self.rows:
            self.list.append(r)
        self.selection = [(t, s) for t, s in self.selection if t in song.tracks]
        if self.rows:
            row = next((r for r in self.rows if r.track is current), None)
            self.list.select_row(row or self.rows[min(old_index, len(self.rows) - 1)])

    def _select_track(self, track: Track) -> None:
        row = next((r for r in self.rows if r.track is track), None)
        if row is not None:
            self.list.select_row(row)

    def add_track(self) -> None:
        """Track > Add Track: traccia vuota sotto quella evidenziata (in fondo se non ce n'è)."""
        song = self.doc.song
        current = self.selected_track()
        index = song.tracks.index(current) + 1 if current in song.tracks else len(song.tracks)
        names = {t.name for t in song.tracks}
        n = len(song.tracks) + 1
        # 2026-11-20: nome predefinito in inglese ("Track N", prima "Traccia N"): UI tutta in inglese.
        # I nomi già salvati restano com'erano; il controllo di unicità vale solo sui nomi nuovi.
        while f"Track {n}" in names:
            n += 1
        used = {t.channel for t in song.tracks}
        channel = next((c for c in range(1, 17) if c != 10 and c not in used), 1)  # primo canale libero
        track = Track(f"Track {n}", channel=channel)
        self.doc.execute(AddTrack(track, index))
        self._select_track(track)

    def duplicate_track_empty(self) -> Track | None:
        """Track > Duplicate Track (no segments): sotto quella evidenziata, una traccia con le
        stesse impostazioni (strumento, canale, suoni per uscita, chiave, velocità), vuota."""
        song, current = self.doc.song, self.selected_track()
        if current is None or current.kind == "chords":
            self.on_message("Select a track (not the chord track).")
            return None
        names = {t.name for t in song.tracks}
        n = 2
        while f"{current.name} ({n})" in names:
            n += 1
        track = Track(f"{current.name} ({n})", current.instrument, current.clef, channel=current.channel,
                      velocity=current.velocity, sounds=dict(current.sounds))
        self.doc.execute(AddTrack(track, song.tracks.index(current) + 1))
        self._select_track(track)
        return track

    def delete_track(self) -> None:
        """Track > Delete Track: elimina subito la traccia evidenziata (Ctrl+Z la ripristina)."""
        track = self.selected_track()
        if track is None:
            self.on_message("No track selected.")
            return
        self.cancel_reorder()
        self.doc.execute(DeleteTrack(track))

    def begin_reorder(self) -> None:
        """Track > Reorder: la riga della traccia segue il mouse, click = rilascia, Esc = annulla."""
        track = self.selected_track()
        row = next((r for r in self.rows if r.track is track), None)
        if row is None:
            self.on_message("No track selected.")
            return
        self.reordering = {"track": track, "row": row, "orig": self.rows.index(row)}
        self.on_message("Reorder: click where to move the track · Esc cancels", error=False)

    def cancel_reorder(self) -> None:
        """Esc: la riga torna al suo posto."""
        if self.reordering is None:
            return
        self.reordering = None
        self.on_message("", error=False)
        if [r.track for r in self.rows] != self.doc.song.tracks:
            self._rebuild_rows()

    def _reorder_hover(self, y: float) -> None:
        """La riga in spostamento prende il posto della riga sotto il puntatore."""
        if self.reordering is None:
            return
        moving = self.reordering["row"]
        row = self.list.get_row_at_y(int(y))
        if not isinstance(row, TrackRow) or row is moving:
            return
        i = self.rows.index(row)
        self.rows.remove(moving)
        self.rows.insert(i, moving)
        self.list.remove(moving)
        self.list.insert(moving, i)
        self.list.select_row(moving)

    def _reorder_pressed(self, gesture: Gtk.GestureClick, _n: int, _x: float, y: float) -> None:
        if self.reordering is None:
            return
        gesture.set_state(Gtk.EventSequenceState.CLAIMED)  # il click serve solo a rilasciare la traccia
        self._reorder_hover(y)
        r = self.reordering
        self.reordering = None
        self.on_message("", error=False)
        new = self.rows.index(r["row"])
        if new != r["orig"] and r["track"] in self.doc.song.tracks:
            self.doc.execute(MoveTrack(r["track"], new))  # le righe sono già nell'ordine nuovo
        self._select_track(r["track"])

    def _draw_name_separator(self, cr, h: int) -> None:
        """Linea verticale fra la colonna dei nomi e le corsie, per tutta l'altezza dell'elenco:
        sotto la maniglia del righello, alla stessa x (segue la larghezza della colonna)."""
        if self.ruler is None:
            return
        handle = self.ruler.handle
        point = _translate(handle, self.band_area, handle.get_width() // 2, 0)
        if point is None:
            return
        cr.set_source_rgba(*SEPARATOR_RGBA)
        cr.rectangle(int(point[0]), 0, 1, h)
        cr.fill()

    def _draw_band(self, _a, cr, _w: int, h: int) -> None:
        self._draw_name_separator(cr, h)
        self._draw_split_hint(cr)
        self._draw_paste_fail(cr)
        self._draw_create(cr)
        self._draw_move(cr)
        self._draw_resize(cr)
        if self._band is None:
            return
        x0, y0, x1, y1 = self._band
        a = _translate(self.list, self.band_area, x0, y0)
        b = _translate(self.list, self.band_area, x1, y1)
        if a is None or b is None:
            return
        (bx0, by0), (bx1, by1) = a, b
        cr.rectangle(bx0 + 0.5, by0 + 0.5, bx1 - bx0, by1 - by0)
        cr.set_source_rgba(0.21, 0.52, 0.89, 0.15)
        cr.fill_preserve()
        cr.set_source_rgba(0.21, 0.52, 0.89, 0.9)
        cr.set_line_width(1)
        cr.stroke()

    def add_scroll_controllers(self, widget: Gtk.Widget) -> None:
        scroll = Gtk.EventControllerScroll.new(Gtk.EventControllerScrollFlags.BOTH_AXES)
        scroll.connect("scroll", self.on_scroll)
        widget.add_controller(scroll)
        motion = Gtk.EventControllerMotion()
        motion.connect("motion", lambda _c, x, _y: setattr(self, "_pointer_x", x))
        widget.add_controller(motion)

    # --- cursore ---------------------------------------------------------

    def seek_x(self, x: float, snap: bool = True) -> None:
        """Punto di play sotto x, agganciato al movimento (1/4 in 4/4) come l'incolla."""
        if not self.doc:
            return
        song = self.doc.song
        pos = Fraction(max(self.pos_of(x), 0.0)).limit_denominator(1920)
        if snap:
            pos = song.snap_to_beat(pos)
        self.transport.seek(song.tempos.seconds(min(pos, song.end_position)))
        self.redraw_all()

    def seek_fraction(self, fraction: float) -> None:
        """Sposta il punto di play a una frazione (0..1) della lunghezza del brano."""
        if self.doc:
            end = self.doc.song.end_position
            self.seek_x(self.x_of(float(end) * min(max(fraction, 0.0), 1.0)), snap=False)

    def redraw_all(self) -> None:
        if self.doc:
            self.play_pos = float(self.doc.song.tempos.position(self.transport.position))
        if self.ruler:
            self.ruler.area.queue_draw()
        for r in self.rows:
            r.lane.queue_draw()

    def draw_playhead(self, cr, h: int) -> None:
        x = self.x_of(self.play_pos)
        cr.set_source_rgb(*PLAYHEAD)
        cr.rectangle(x, 0, 1.5, h)
        cr.fill()

    def set_listen(self, track: Track, mute: bool | None = None, solo: bool | None = None) -> None:
        """Mute / Solo: agiscono subito sul playback (anche in corso); non entrano in Ctrl+Z.
        Solo è esclusivo: accenderlo su una traccia lo spegne sulle altre."""
        if mute is not None:
            track.mute = mute
        if solo is not None:
            if solo:
                for t in self.doc.song.tracks:
                    t.solo = False
            track.solo = solo
        self._listen_changed()

    def mute_all(self, on: bool) -> None:
        """M nel righello: Mute acceso o spento su tutte le tracce."""
        for t in self.doc.song.tracks:
            t.mute = on
        self._listen_changed()

    def all_muted(self) -> bool:
        tracks = self.doc.song.tracks if self.doc else []
        return bool(tracks) and all(t.mute for t in tracks)

    def _listen_changed(self) -> None:
        self.transport.set_silenced(self.doc.song.silent_track_ids())
        self.doc.touch()  # Mute / Solo si salvano nel progetto (non entrano in Ctrl+Z)
        for w in [r.leds for r in self.rows] + ([self.ruler.leds] if self.ruler else []):
            child = w.get_first_child()
            while child is not None:
                if isinstance(child, ListenToggle):
                    child.sync()
                child = child.get_next_sibling()
        for r in self.rows:  # Preview della traccia accordi (spento da un altro Solo)
            if getattr(r, "preview", None) is not None:
                r.preview.sync()
            r.update_listen_style()  # sfondo giallo (Solo) o rosso (Mute) della riga
        self.on_listen()

    def selected_track(self) -> Track | None:
        row = self.list.get_selected_row()
        return row.track if isinstance(row, TrackRow) else None

    def shutdown(self) -> None:
        GLib.source_remove(self._timer)

    def _on_tick(self) -> bool:
        secs = self.transport.position
        if secs != self._last_secs:
            self._last_secs = secs
            if self.transport.state is State.PLAYING:
                self._follow(secs)
            self.redraw_all()
        return GLib.SOURCE_CONTINUE

    def _follow(self, secs: float) -> None:
        """Durante il play il punto di play arriva al centro e poi ci resta (follow.py)."""
        if self.doc:
            follow_center(self.hadj, self.pad + float(self.doc.song.tempos.position(secs)) * self.px_per_whole)


def _translate(src: Gtk.Widget, dest: Gtk.Widget, x: float, y: float) -> tuple[float, float] | None:
    """Coordinate di (x, y) di `src` nel sistema di `dest` (None se non collegati)."""
    res = src.translate_coordinates(dest, x, y)
    if not res:
        return None
    if len(res) == 3:  # alcune versioni di PyGObject restituiscono anche l'esito
        ok, tx, ty = res
        return (tx, ty) if ok else None
    return res[0], res[1]


def _draw_grip(_area, cr, w: int, h: int) -> None:
    """Maniglia della colonna dei nomi nel righello: una barra scura larga 2 px, sopra la linea dell'elenco
    (1 px, a w // 2) più un pixel verso il bordo sinistro della finestra, non verso il righello.
    2026-11-24: prima due linee separate; l'utente voleva una sola barra più larga."""
    cr.set_source_rgba(*HANDLE_RGBA)
    cr.rectangle(w // 2 - 1, 0, 2, h)
    cr.fill()


class RulerRow(Gtk.ListBoxRow):
    """Righello con i numeri di misura, allineato alle corsie dei segmenti."""

    def __init__(self, view: TracksView, doc: Document) -> None:
        super().__init__(selectable=False, activatable=False)
        self.view, self.doc = view, doc
        box = Gtk.Box(margin_start=6, margin_end=6)
        # stessa larghezza dei nomi; sopra la colonna dei Mute, una M che li accende/spegne tutti
        spacer = view.name_spacer()
        spacer.append(Gtk.Box(hexpand=True))
        self.leds = Gtk.Box(spacing=1)
        self.leds.append(ListenToggle("M", "Mute all tracks", view.all_muted, view.mute_all))
        self.leds.append(Gtk.Box(width_request=LISTEN_SIZE))  # al posto della S
        spacer.append(self.leds)
        box.append(spacer)
        # maniglia: trascinando si allarga/stringe la colonna dei nomi; alta quanto il righello,
        # continua nella linea disegnata sopra l'elenco (_draw_name_separator)
        self.handle = view.name_handle(draw_line=True)
        box.append(self.handle)
        self.area = Gtk.DrawingArea(hexpand=True, content_height=ruler.RULER_H)
        self.area.set_draw_func(self.draw)
        self.area.set_has_tooltip(True)
        self.area.connect("query-tooltip", self._tooltip)
        self.area.connect("resize", lambda *_: view.update_hadj())
        self.marks: list[tuple[int, float, float, float, float]] = []  # "V" disegnate: (misura, x, y, w, h)
        self._marks_version = None  # (documento, versione) di cui sono le misure con la "V"
        self._mark_bars: set[int] = set()
        click = Gtk.GestureClick()
        click.connect("pressed", self._pressed)
        self.area.add_controller(click)
        view.add_scroll_controllers(self.area)
        box.append(self.area)
        self.set_child(box)

    def draw(self, _a, cr, w: int, h: int) -> None:
        v, song = self.view, self.doc.song
        first = max(bisect_right(v.bar_x, v.pos_of(0)) - 1, 0)
        last = min(bisect_right(v.bar_x, v.pos_of(w)) + 1, song.bar_count)
        x0 = max(v.x_of(0.0), 0.0)  # la battuta vuota prima della misura 1 resta non disegnata
        # stanghetta di chiusura solo se si arriva alla fine del brano (last è anche il limite della vista)
        version = (id(self.doc), self.doc.version)
        if version != self._marks_version:  # le misure con la "V" si ricalcolano solo se il brano è cambiato
            self._marks_version, self._mark_bars = version, volume_bars(song)
        self.marks = ruler.draw_ruler(cr, x0, 0, w - x0, h, song, first, last, lambda p: v.x_of(float(p)),
                                      is_dark(self.area), end_line=last == song.bar_count, marks=self._mark_bars,
                                      mark_rgb=_selected_rgb(self.area))
        v.draw_playhead(cr, h)

    def mark_at(self, x: float, y: float) -> int | None:
        """Misura della "V" sotto il mouse (con un pixel di tolleranza), o None."""
        for bar, mx, my, mw, mh in self.marks:
            if mx - 1 <= x <= mx + mw + 1 and my - 1 <= y <= my + mh + 1:
                return bar
        return None

    def _pressed(self, _g, _n: int, x: float, y: float) -> None:
        """Click: sulla "V" porta il play all'inizio di quella misura e apre il mixer; altrove sposta il punto di play."""
        bar = self.mark_at(x, y)
        if bar is not None:
            self.view.on_bar_volumes(bar)
        else:
            self.view.seek_x(x)

    def _tooltip(self, _w, x: int, y: int, _keyboard: bool, tooltip: Gtk.Tooltip) -> bool:
        bar = self.mark_at(x, y)
        tooltip.set_text(f"Volume changes at the start of bar {bar + 1}: click to see them in the mixer" if bar is not None
                         else "Click: move the play position")
        return True


def _selected_rgb(widget: Gtk.Widget) -> tuple[float, float, float]:
    """Colore di selezione del tema GTK (fondo della "V" nel righello); blu se il tema non lo dichiara."""
    ok, color = widget.get_style_context().lookup_color("theme_selected_bg_color")
    return (color.red, color.green, color.blue) if ok else (0.21, 0.52, 0.89)


class ListenToggle(Gtk.ToggleButton):
    """Mute / Solo: pulsante a interruttore del tema (piatto), con la sola lettera.
    Acceso = pulsante premuto. `sync()` lo riallinea al modello senza rieseguire l'azione."""

    def __init__(self, letter: str, tooltip: str, get: Callable[[], bool], set_: Callable[[bool], None]) -> None:
        super().__init__(label=letter, tooltip_text=tooltip, valign=Gtk.Align.CENTER, css_classes=["flat", "listen-toggle"])
        self.get, self.set_ = get, set_
        self.set_focus_on_click(False)
        self.set_active(get())
        self._handler = self.connect("toggled", lambda b: self.set_(b.get_active()))

    def sync(self) -> None:
        with self.handler_block(self._handler):
            self.set_active(self.get())


class TrackRow(Gtk.ListBoxRow):
    def __init__(self, view: TracksView, doc: Document, track: Track) -> None:
        super().__init__(css_classes=["track-row"])  # classe per il bordo della riga selezionata (app.py)
        self.view, self.doc, self.track = view, doc, track
        self.notes: list[tuple[float, float, int, str]] = []  # (inizio, fine, altezza, id segmento)
        self.ranges: dict[str, tuple[int, int]] = {}  # altezze min/max per segmento
        box = Gtk.Box(margin_top=4, margin_bottom=4, margin_start=6, margin_end=6)

        self.name = Gtk.Label(xalign=0, max_width_chars=1, ellipsize=3, hexpand=True)
        # larghezza = colonna dei nomi: nome + Mute/Solo. hexpand=False esplicito: altrimenti
        # l'hexpand del nome si propaga e la colonna si prende lo spazio delle corsie
        name_column = Gtk.Box(spacing=4, hexpand=False)
        view.register_name_widget(name_column)
        self.name.set_tooltip_text("Double-click: track properties")
        self._double_click(self.name, lambda: view.on_properties(track))
        name_menu = Gtk.GestureClick(button=3)  # tasto destro sul nome: menu Track
        name_menu.connect("pressed", self.on_name_menu)
        self.name.add_controller(name_menu)
        name_column.append(self.name)
        self.channel = Gtk.Label(xalign=1, width_chars=2, tooltip_text="MIDI channel",
                                 margin_end=3)
        name_column.append(self.channel)
        self.leds = Gtk.Box(spacing=1)  # M e S separati da 1 px
        self.leds.append(ListenToggle("M", "Mute", lambda: track.mute, lambda on: view.set_listen(track, mute=on)))
        self.leds.append(ListenToggle("S", "Solo", lambda: track.solo, lambda on: view.set_listen(track, solo=on)))
        name_column.append(self.leds)
        if track.kind == "chords":  # traccia accordi: solo Preview (suona solo lei), niente canale né M/S
            self.channel.set_visible(False)
            self.leds.set_visible(False)
            self.preview = ListenToggle("P", "Preview: play only the chord track (chord around middle C + bass)",
                                        lambda: track.solo, lambda on: view.set_listen(track, solo=on))
            name_column.append(self.preview)
            self.name.set_tooltip_text("Chord track: double-click a segment to open the Chord Editor")
        box.append(name_column)
        box.append(view.name_handle(draw_line=False))  # anche qui si trascina il bordo della colonna dei nomi

        self.lane = Gtk.DrawingArea(hexpand=True, content_height=34)
        self.lane.set_draw_func(self.draw_lane)
        # tooltip: sopra un segmento la sua etichetta per intero (nel segmento può essere
        # tagliata); altrove l'aiuto dei gesti
        self.lane.set_has_tooltip(True)
        self.lane.connect("query-tooltip", self._lane_tooltip)
        lane_click = Gtk.GestureClick()
        lane_click.connect("pressed", self.on_lane_pressed)
        lane_click.connect("released", self.on_lane_released)
        menu_click = Gtk.GestureClick(button=3)  # tasto destro: menu Segment
        menu_click.connect("pressed", self.on_lane_menu)
        self.lane.add_controller(menu_click)
        hover = Gtk.EventControllerMotion()  # linea del taglio in modalità Split
        hover.connect("motion", lambda _c, x, _y: view.split_hover(self, x))
        hover.connect("leave", lambda _c: view.split_hover(None))
        self.lane.add_controller(hover)
        self.lane.add_controller(lane_click)
        view.add_scroll_controllers(self.lane)
        box.append(self.lane)
        self.set_child(box)
        self.refresh()
        self.update_listen_style()

    def update_listen_style(self) -> None:
        """Sfondo della riga intera secondo Mute / Solo (CSS .track-solo e .track-mute in app.py).
        2026-11-23: richiesta dell'utente, un Solo dimenticato non si notava e il brano sembrava muto.
        Solo vince su Mute, come nel suono: una traccia in Solo e in Mute suona ed è gialla."""
        solo, mute = self.track.solo, self.track.mute and not self.track.solo
        for name, on in (("track-solo", solo), ("track-mute", mute)):
            if on:
                self.add_css_class(name)
            else:
                self.remove_css_class(name)

    def _double_click(self, widget: Gtk.Widget, action: Callable[[], None]) -> None:
        click = Gtk.GestureClick()

        def pressed(_g, n_press: int, _x: float, _y: float) -> None:
            self.view.list.select_row(self)
            if n_press == 2:
                action()

        click.connect("pressed", pressed)
        widget.add_controller(click)

    def on_lane_pressed(self, _gesture: Gtk.GestureClick, n_press: int, x: float, _y: float) -> None:
        v = self.view
        if n_press == 2 and v.get_mode() not in ("split", "delete"):
            seg = v.segment_at(self.track, x)
            if seg is not None:
                if not v.is_selected(seg):
                    v.click_segment(self.track, seg)
                v.on_open_selection()

    LANE_HELP = ("Click: select and move the play position · Shift+click: add to/remove from selection · "
                 "Drag: selection rectangle · Double-click: score")

    def _lane_tooltip(self, _w, x: int, y: int, _keyboard: bool, tooltip: Gtk.Tooltip) -> bool:
        seg = self.view.segment_at(self.track, x)
        text = self.doc.song.display_label(seg) if seg is not None else ""
        tooltip.set_text(text or self.LANE_HELP)
        return True

    def on_name_menu(self, _g, _n: int, x: float, y: float) -> None:
        """Tasto destro sul nome: evidenzia la traccia e apre il menu Track (agisce su di lei)."""
        self.view.list.select_row(self)
        self.view.show_track_menu(self.name, x, y)

    def on_lane_menu(self, _g, _n: int, x: float, y: float) -> None:
        """Tasto destro su un segmento: lo seleziona (se non lo è) e apre il menu Segment."""
        v = self.view
        seg = v.segment_at(self.track, x)
        if seg is None:
            return
        if not v.is_selected(seg):
            v.click_segment(self.track, seg)
        v.show_segment_menu(self.lane, x, y)

    def on_lane_released(self, gesture: Gtk.GestureClick, n_press: int, x: float, _y: float) -> None:
        """Click semplice (non arriva se il mouse è stato trascinato: quello è il rettangolo)."""
        v = self.view
        if n_press != 1:
            return
        v.list.select_row(self)
        shift = bool(gesture.get_current_event_state() & Gdk.ModifierType.SHIFT_MASK)
        if v.get_mode() == "split" and not shift:  # Split: il click divide (Maiusc+click seleziona)
            v.split_click(self.track, x)
            return
        if v.get_mode() == "create" and not shift and v.segment_at(self.track, x) is None:
            return  # Create: lo gestisce il trascinamento (pressione → anteprima → rilascio)
        if v.get_mode() == "delete" and not shift:  # Delete: il click cancella (Maiusc+click seleziona)
            v.delete_click(self.track, x)
            return
        # selezione sempre attiva: click = seleziona, Maiusc+click = mette/toglie
        v.click_segment(self.track, v.segment_at(self.track, x), toggle=shift)
        v.seek_x(x)

    def refresh(self) -> None:
        """Ricalcola la cache delle note (da chiamare a ogni modifica del documento)."""
        self.name.set_label(self.track.name)
        self.channel.set_label(str(self.track.channel))
        song = self.doc.song
        # 2026-11-20: le note si ricalcolano solo se il contenuto è cambiato (doc.version):
        # Mute/Solo e Save non lo cambiano e su long.mid costavano ~40 ms a ogni click
        version = (id(self.doc), self.doc.version)
        if getattr(self, "_notes_version", None) == version:
            self.lane.queue_draw()
            return
        self._notes_version = version
        self.notes = [(float(p.start), float(p.end), p.midi, p.segment.id) for p in song.placed_notes(self.track)]
        self._max_len = max((e - s for s, e, _m, _g in self.notes), default=0.0)  # nota più lunga (per il disegno)
        self.ranges = {}
        for _s, _e, midi, seg in self.notes:
            lo, hi = self.ranges.get(seg, (midi, midi))
            self.ranges[seg] = (min(lo, midi), max(hi, midi))
        self._starts = [n[0] for n in self.notes]
        self.lane.queue_draw()

    def _draw_chords(self, cr, w: int, h: int) -> None:
        """Sigle della traccia accordi: un trattino e il nome nel punto dove l'accordo inizia."""
        v, song = self.view, self.doc.song
        fg = self.lane.get_color()
        cr.select_font_face("sans-serif", 0, 0)
        cr.set_font_size(11)
        for seg in self.track.segments:
            s0, s1 = v.seg_span(seg)
            x0, x1 = v.x_of(s0), v.x_of(s1)
            if x1 < 0 or x0 > w:
                continue
            cr.save()
            cr.rectangle(x0 + 2, 0, max(x1 - x0 - 4, 0), h)
            cr.clip()
            for e in song.groups[seg.group_id].chord_events:
                rel = e.pos - seg.trim
                if not 0 <= rel < seg.length:
                    continue
                x = v.x_of(float(s0 + rel))
                cr.set_source_rgba(fg.red, fg.green, fg.blue, 0.6)
                cr.rectangle(x, h - 15, 1, 11)
                cr.fill()
                cr.set_source_rgb(fg.red, fg.green, fg.blue)
                cr.move_to(x + 3, h - 6)
                cr.show_text(e.name)
            cr.restore()

    def draw_lane(self, _a, cr, w: int, h: int) -> None:
        v, song = self.view, self.doc.song
        p0, p1 = v.pos_of(0), v.pos_of(w)
        x_start = max(v.x_of(0.0), 0.0)  # prima della misura 1 (battuta vuota) non si disegna nulla:
        cr.set_source_rgba(0.5, 0.5, 0.5, 0.12)  # si vede lo sfondo della riga, selezionata o no
        cr.rectangle(x_start, 0, w - x_start, h)
        cr.fill()

        # segmenti
        labels: list[tuple[float, float, str]] = []  # (x0, x1, testo): disegnate per ultime
        for seg in self.track.segments:
            s0, s1 = v.seg_span(seg)
            x0, x1 = v.x_of(s0), v.x_of(s1)
            if x1 < 0 or x0 > w:
                continue
            r, g, b = CLONE_COLOR if seg.clone_of else group_color(song, seg.group_id)  # cloni: grigi
            cr.set_source_rgba(r, g, b, 0.12 if v.moving(seg) else 0.35)  # in spostamento: attenuato
            cr.rectangle(x0 + 1, 2, x1 - x0 - 2, h - 4)
            cr.fill()
            text = song.display_label(seg)
            if text:  # etichetta: si disegna alla fine, sopra stanghette e note
                labels.append((x0, x1, text))
            if v.is_selected(seg):  # segmento selezionato: bordo pieno
                cr.set_source_rgb(r * 0.55, g * 0.55, b * 0.55)
                cr.set_line_width(2.5)
                cr.rectangle(x0 + 2, 2.5, x1 - x0 - 4, h - 5)
                cr.stroke()

        # stanghette e movimenti, come nella griglia
        if v.beat_x and v.min_beat * v.px_per_whole >= ruler.MIN_BEAT_PX:
            cr.set_source_rgba(0.5, 0.5, 0.5, 0.25)
            for p in v.beat_x[bisect_left(v.beat_x, p0):bisect_right(v.beat_x, p1)]:
                cr.rectangle(v.x_of(p), 0, 1, h)
            cr.fill()
        cr.set_source_rgba(0.35, 0.35, 0.35, 0.7)
        for p in v.bar_x[bisect_left(v.bar_x, p0):bisect_right(v.bar_x, p1)]:
            cr.rectangle(v.x_of(p), 0, 1, h)
        cr.fill()

        if self.track.kind == "chords":  # traccia accordi: le sigle al loro posto
            self._draw_chords(cr, w, h)
            self._draw_labels(cr, labels)
            return

        # anteprima delle note visibili
        colors = {s.id: CLONE_COLOR if s.clone_of else group_color(song, s.group_id) for s in self.track.segments}
        current = None
        # 2026-11-20: si parte dalla prima nota che può essere visibile (inizio ≥ p0 meno la nota più
        # lunga), non dall'inizio del brano: durante il play si ridisegna ogni 40 ms
        first = bisect_left(self._starts, p0 - self._max_len)
        for start, end, midi, seg in self.notes[first: bisect_right(self._starts, p1)]:
            if end < p0:
                continue
            if seg != current:
                cr.fill()
                r, g, b = colors.get(seg, (0.5, 0.5, 0.5))
                cr.set_source_rgba(r * 0.7, g * 0.7, b * 0.7, 0.9)
                current = seg
            lo, hi = self.ranges[seg]
            y = 4 + (h - 8) * (1 - (midi - lo) / max(hi - lo, 12))
            x = v.x_of(start)
            cr.rectangle(x, y - 1, max(v.x_of(end) - x, 1.5), 2)
        cr.fill()
        # etichette per ultime: stanghette e note non passano più sopra il testo (2026-11-20)
        self._draw_labels(cr, labels)
        v.draw_playhead(cr, h)

    def _draw_labels(self, cr, labels: list[tuple[float, float, str]]) -> None:
        """Testo delle etichette in alto a sinistra sui segmenti; resta visibile all'inizio
        della corsia anche se il segmento comincia prima.

        2026-11-20, richieste dell'utente: con Pango invece della toy API di Cairo (font
        dell'interfaccia di sistema in grassetto e "…" in fondo quando l'etichetta è più lunga
        dello spazio, invece del taglio a metà lettera); niente banda di sfondo, ma un alone
        del colore dello sfondo attorno alle lettere che le stacca da note e stanghette."""
        if not labels:
            return
        layout = self.lane.create_pango_layout("")  # contesto e font del widget (sistema)
        font = self.lane.get_pango_context().get_font_description().copy()
        font.set_weight(Pango.Weight.BOLD)
        font.set_absolute_size(LABEL_FONT_PX * Pango.SCALE)
        layout.set_font_description(font)
        layout.set_ellipsize(Pango.EllipsizeMode.END)
        layout.set_single_paragraph_mode(True)
        fg = self.lane.get_color()
        halo = (0.1, 0.1, 0.1) if is_dark(self.lane) else (1.0, 1.0, 1.0)  # colore dello sfondo
        cr.set_line_join(cairo.LINE_JOIN_ROUND)  # alone senza spigoli
        cr.set_line_width(LABEL_HALO_PX * 2)  # il tratto è centrato sul contorno: metà fuori
        for x0, x1, text in labels:
            left = max(x0, 0) + 6
            room = x1 - 4 - left  # spazio fino al bordo destro del segmento
            if room < 8:  # segmento troppo stretto: niente testo (resta il tooltip)
                continue
            layout.set_text(text, -1)
            layout.set_width(int(room * Pango.SCALE))
            # prima l'alone (contorno delle lettere), poi il testo pieno sopra
            cr.move_to(left, LABEL_TOP)
            PangoCairo.layout_path(cr, layout)
            cr.set_source_rgba(*halo, 0.85)
            cr.stroke()
            cr.move_to(left, LABEL_TOP)
            cr.set_source_rgb(fg.red, fg.green, fg.blue)
            PangoCairo.show_layout(cr, layout)

