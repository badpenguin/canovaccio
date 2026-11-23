"""Editor a griglia (piano roll) di una traccia.

Rotella: avanti/indietro lungo la timeline · Maiusc+rotella: su/giù ·
Ctrl+rotella: zoom orizzontale · click sul righello: sposta il cursore ·
click sulla tastiera: suona la nota (finché il tasto è premuto).

Gli strumenti della toolbar (F2–F7) agiscono sulle note, come sui segmenti:
- selezione sempre attiva: click = seleziona (e sposta il punto di play),
  Maiusc+click = mette/toglie, trascina da uno spazio vuoto (o con Maiusc) =
  rettangolo di selezione;
- Create: premi in uno spazio vuoto e trascina = nuova nota (solo dentro un
  segmento della traccia, altrimenti anteprima rossa e niente nota);
- Move: trascina le note (selezionate) nel tempo e in altezza;
- Split: click su una nota = la divide lì; Resize: trascina inizio/fine;
- Delete: click = cancella (tutta la selezione se la nota ne fa parte); tasto Canc.
Aggancio (Snap) scelto in basso: 1/4, 1/8 (default), 1/16, 1/32, Off.
Le note dei cloni sono grigie e non si modificano (si modifica l'originale).
Doppio click su una nota: proprietà della nota.
"""

from __future__ import annotations

from bisect import bisect_right
from fractions import Fraction

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
from gi.repository import Gdk, Gio, GLib, Gtk  # noqa: E402

from ..core.document import AddNotes, Document, EditNotes  # noqa: E402
from ..core.instrument import GM_DRUMS  # noqa: E402
from ..engraving.layout import _pretty_symbol  # noqa: E402
from .score_render import TEXT_FONT  # noqa: E402
from ..core.model import Note, PlacedNote, Track  # noqa: E402
from ..output.transport import Transport  # noqa: E402
from .follow import follow_center  # noqa: E402
from . import ruler  # noqa: E402
from .icons import themed  # noqa: E402
from .toolbar import snap_buttons  # noqa: E402
from .note_editing import DEFAULT_SNAP, SNAPS, NoteEditing  # noqa: E402
from .popup import popup_menu  # noqa: E402
from .tracks_view import _translate  # noqa: E402
from .theme import PLAYHEAD, group_color, is_dark  # noqa: E402

KEYBOARD_W = 52
DRUM_NAMES_W = 150  # colonna dei nomi dei suoni sulle tracce di batteria
DRUM_LENGTH = Fraction(1, 4)  # durata scritta di un colpo creato sulla batteria
DRUM_MARGIN = 2  # righe vuote lasciate sopra e sotto i suoni GM della batteria
SEG_H = 6  # banda dei segmenti, sotto il righello
RULER_H = SEG_H + ruler.RULER_H
KEY_H = 12
DEFAULT_PX_PER_WHOLE = 160.0
NOTE_NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]
BLACK = {1, 3, 6, 8, 10}
SELECT_RGB = (0.21, 0.52, 0.89)
CLONE_RGB = (0.55, 0.55, 0.55)
EDGE_PX = 5
TIE_BRIDGE = 20  # px: ponticello scuro a cavallo del punto in cui due note legate si toccano
TIE_BRIDGE_H = 4  # px: spessore del ponticello
SELECT_EDGE_MIN_PX = 12  # in Select, sulle note più strette vince lo spostamento (si afferrano sempre)


class PianoRollView(NoteEditing, Gtk.Box):
    kind = "grid"

    @property
    def tracks(self) -> list:
        return [self.track]

    @property
    def key(self) -> tuple:
        return ("grid", self.track.id, self.first_bar, self.last_bar)

    def __init__(self, doc: Document, track: Track, transport: Transport, first_bar: int = 0, last_bar: int | None = None,
                 get_mode=lambda: "select", on_message=lambda _t, error=True: None, menu=None, notes_menu=None,
                 on_selection=lambda: None) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self.doc, self.track, self.transport = doc, track, transport
        self.get_mode, self.on_message = get_mode, on_message
        self.menu = menu  # menu Grid (strumenti): il tasto destro lo mostra sempre
        self.notes_menu = notes_menu  # menu Notes: col tasto destro, sotto, se ci sono note selezionate
        self.on_selection = on_selection  # la selezione è passata da vuota a non vuota o viceversa
        self._had_selection = False
        self.init_note_editing()  # selezione, ascolto, anteprime rosse (comandi comuni con la notazione)
        self.key_h = float(KEY_H)  # altezza di una riga (zoom verticale: Ctrl+Maiusc+rotella)
        self._op: dict | None = None  # gesto in corso: band / move / resize / create / pending
        self._split_hint: tuple[Fraction, int] | None = None  # Split: (punto del taglio, altezza) sotto il puntatore
        self.snap: Fraction | None = SNAPS[DEFAULT_SNAP][1]
        self.first_bar, self.last_bar = first_bar, last_bar  # misure mostrate (quelle dei segmenti selezionati)
        self.px_per_whole = DEFAULT_PX_PER_WHOLE
        self._pointer_x = 0.0

        bar = Gtk.Box(spacing=2, margin_start=6, margin_end=6, margin_top=2, margin_bottom=2, halign=Gtk.Align.END)  # toolbar inferiore
        snap = snap_buttons(SNAPS[DEFAULT_SNAP][1], self.set_snap)  # Snap a pulsanti (simboli delle note)
        snap.set_margin_end(12)
        bar.append(snap)
        for icon, step in (("zoom-out-symbolic", 1 / 1.25), ("zoom-in-symbolic", 1.25)):
            b = Gtk.Button(icon_name=themed(icon), tooltip_text="Zoom")
            b.connect("clicked", lambda _b, s=step: self.zoom(s, None))
            b.add_css_class("flat")
            b.set_focus_on_click(False)
            bar.append(b)

        self.hadj = Gtk.Adjustment(step_increment=40, page_increment=400)
        self.vadj = Gtk.Adjustment(upper=128 * self.key_h, step_increment=self.key_h * 3, page_increment=self.key_h * 24)
        self.area = Gtk.DrawingArea(hexpand=True, vexpand=True, focusable=True)
        self.area.set_draw_func(self.draw)
        self.area.connect("resize", self._on_resize)
        self._shown_playhead = False
        for adj in (self.hadj, self.vadj):
            adj.connect("value-changed", lambda *_: self.area.queue_draw())

        scroll = Gtk.EventControllerScroll.new(Gtk.EventControllerScrollFlags.BOTH_AXES)
        scroll.connect("scroll", self.on_scroll)
        self.area.add_controller(scroll)
        motion = Gtk.EventControllerMotion()
        motion.connect("motion", self.on_motion)
        motion.connect("leave", self.on_leave)
        self.area.add_controller(motion)
        drag = Gtk.GestureDrag()
        drag.connect("drag-begin", self.on_drag_begin)
        drag.connect("drag-update", self.on_drag_update)
        drag.connect("drag-end", self.on_drag_end)
        self.area.add_controller(drag)
        self._last_press = (0, 0.0, 0.0)  # (tempo µs, x, y) dell'ultima pressione: doppio click
        keys = Gtk.EventControllerKey()  # frecce: spostano la selezione
        keys.connect("key-pressed", self.on_key)
        self.area.add_controller(keys)
        right = Gtk.GestureClick(button=3)  # tasto destro su una nota: menu Grid
        right.connect("pressed", self.on_right_click)
        self.area.add_controller(right)

        grid = Gtk.Grid()
        grid.attach(self.area, 0, 0, 1, 1)
        grid.attach(Gtk.Scrollbar(orientation=Gtk.Orientation.VERTICAL, adjustment=self.vadj), 1, 0, 1, 1)
        grid.attach(Gtk.Scrollbar(orientation=Gtk.Orientation.HORIZONTAL, adjustment=self.hadj), 0, 1, 1, 1)
        self.append(grid)
        self.append(Gtk.Separator())
        self.append(bar)

        self._cache_notes()
        self._center_on_notes()
        self.set_mode_cursor(get_mode())
        doc.subscribe(self.on_doc_changed)
        self._timer = GLib.timeout_add(33, self.on_tick)

    # --- geometria -------------------------------------------------------

    @property
    def origin(self) -> float:
        return float(self.doc.song.meters.bar_start(self.first_bar))

    @property
    def end(self) -> float:
        song = self.doc.song
        return float(song.meters.bar_start(song.bar_count if self.last_bar is None else self.last_bar))

    @property
    def x0(self) -> float:
        """x della prima misura mostrata: sulla batteria un po' di spazio dopo la colonna dei
        nomi, perché il rombo di un colpo sulla misura 1 non resti tagliato a metà."""
        return self.kb_w + (self.key_h / 2 + 2 if self.is_drums else 0)

    def x_of(self, pos: Fraction | float) -> float:
        return self.x0 + (float(pos) - self.origin) * self.px_per_whole - self.hadj.get_value()

    def y_of(self, midi: int) -> float:
        return RULER_H + (127 - midi) * self.key_h - self.vadj.get_value()

    def update_adjustments(self) -> None:
        w, h = self.area.get_width(), self.area.get_height()
        song = self.doc.song
        content = (self.end - self.origin) * self.px_per_whole + 40
        self.hadj.configure(self.hadj.get_value(), 0, max(content, 1), 40, 400, max(w - self.kb_w, 1))
        lo, hi = self._row_range()
        self.vadj.configure(min(max(self.vadj.get_value(), lo), max(hi - (h - RULER_H), lo)), lo, hi,
                            self.key_h * 3, self.key_h * 24, max(h - RULER_H, 1))

    def _row_range(self) -> tuple[float, float]:
        """Scorrimento verticale: tutte le 128 altezze; sulla batteria solo i suoni GM."""
        if self.is_drums:  # i suoni GM più qualche riga vuota sopra e sotto
            return (127 - max(GM_DRUMS) - DRUM_MARGIN) * self.key_h, (128 - min(GM_DRUMS) + DRUM_MARGIN) * self.key_h
        return 0.0, 128 * self.key_h

    def _on_resize(self, *_a) -> None:
        self.update_adjustments()
        if not self._shown_playhead and self.area.get_width() > self.kb_w:
            self._shown_playhead = True
            self.fit_width()
            if self.is_drums:  # batteria: i 47 suoni GM entrano in altezza
                rows = max(GM_DRUMS) - min(GM_DRUMS) + 1 + 2 * DRUM_MARGIN
                self.key_h = min(max((self.area.get_height() - RULER_H) / rows, KEY_H), 22)
                self.update_adjustments()
                self.vadj.set_value(self._row_range()[0])

    def fit_width(self) -> None:
        """All'apertura: zoom in modo che le misure dei segmenti selezionati riempiano la larghezza."""
        span = self.end - self.origin
        width = self.area.get_width() - self.kb_w - 20
        if span > 0 and width > 0:
            self.px_per_whole = min(max(width / span, 20.0), 4000.0)
            self.update_adjustments()
            self.hadj.set_value(0)
            self.area.queue_draw()

    def scroll_to_playhead(self) -> None:
        """Porta in vista la misura del punto di play (cursore a ~15% della larghezza)."""
        pos = float(self.doc.song.tempos.position(self.transport.position))
        page = self.hadj.get_page_size()
        self.hadj.set_value(min(max((pos - self.origin) * self.px_per_whole - page * 0.15, 0), max(self.hadj.get_upper() - page, 0)))
        self.area.queue_draw()

    def _center_on_notes(self) -> None:
        pitches = [p.midi for p in self._placed] or [p.midi for p in self.doc.song.placed_notes(self.track)]
        mid = (min(pitches) + max(pitches)) / 2 if pitches else 60
        if self.is_drums:
            mid = (min(GM_DRUMS) + max(GM_DRUMS)) / 2  # tutti i suoni GM in vista
        GLib.idle_add(lambda: self.vadj.set_value((127 - mid) * self.key_h - self.area.get_height() / 2) and False)

    def zoom_rows(self, factor: float) -> None:
        """Zoom verticale (altezza delle righe), tenendo ferma la riga sotto il puntatore."""
        y = (self._pointer[1] if getattr(self, "_pointer", None) else self.area.get_height() / 2) - RULER_H
        row = (y + self.vadj.get_value()) / self.key_h
        self.key_h = min(max(self.key_h * factor, 6.0), 40.0)
        self.update_adjustments()
        self.vadj.set_value(row * self.key_h - y)
        self.area.queue_draw()

    def zoom_step(self, direction: int) -> None:
        """+1 ingrandisce, -1 rimpicciolisce, 0 torna allo zoom normale."""
        factor = DEFAULT_PX_PER_WHOLE / self.px_per_whole if direction == 0 else 1.25**direction
        self.zoom(factor, None)

    def zoom(self, factor: float, anchor_x: float | None) -> None:
        ax = anchor_x if anchor_x is not None else (self.area.get_width() + self.kb_w) / 2
        pos = (ax - self.kb_w + self.hadj.get_value()) / self.px_per_whole  # relativo all'inizio mostrato
        self.px_per_whole = min(max(self.px_per_whole * factor, 20.0), 4000.0)
        self.update_adjustments()
        self.hadj.set_value(pos * self.px_per_whole - (ax - self.kb_w))
        self.area.queue_draw()

    # --- eventi ----------------------------------------------------------

    def on_scroll(self, ctrl: Gtk.EventControllerScroll, dx: float, dy: float) -> bool:
        state = ctrl.get_current_event_state()
        if state & Gdk.ModifierType.CONTROL_MASK and state & Gdk.ModifierType.SHIFT_MASK:
            self.zoom_rows(1.15 if dy < 0 else 1 / 1.15)  # Ctrl+Maiusc+rotella: zoom verticale
        elif state & Gdk.ModifierType.CONTROL_MASK:
            self.zoom(1.15 if dy < 0 else 1 / 1.15, self._pointer_x)
        elif state & Gdk.ModifierType.SHIFT_MASK:  # Maiusc+rotella: su/giù (altezze)
            self.vadj.set_value(self.vadj.get_value() + (dy or dx) * self.key_h * 3)
        else:  # rotella: avanti/indietro lungo la timeline
            self.hadj.set_value(self.hadj.get_value() + (dy + dx) * 40)
        return True

    # --- posizioni, aggancio, note ----------------------------------------

    def pos_of(self, x: float) -> Fraction:
        return Fraction(self.origin + (x - self.x0 + self.hadj.get_value()) / self.px_per_whole).limit_denominator(1920)

    def midi_of(self, y: float) -> int:
        return 127 - int((y - RULER_H + self.vadj.get_value()) // self.key_h)

    def set_snap(self, value: Fraction | None) -> None:
        """Snap scelto in basso: cambia anche le linee della griglia."""
        self.snap = value
        self.area.queue_draw()

    def _cache_notes(self) -> None:
        """Note della traccia, ricalcolate solo quando cambia il documento."""
        self._placed: list[PlacedNote] = [
            p for p in self.doc.song.placed_notes(self.track)
            if float(p.start) < self.end and float(p.end) > self.origin  # solo le misure mostrate
        ]
        self._starts = [float(p.start) for p in self._placed]
        self._by_note = {id(p.note): p for p in self._placed if p.segment.clone_of is None}
        # legature di valore: note legate alla successiva / legate dalla precedente (per il disegno)
        by_start = {(p.start, p.midi): p for p in self._placed}
        self._tied_next, self._tied_prev = set(), set()
        # legature di frase: (inizio prima nota, fine ultima nota, altezza più alta, colore del gruppo)
        self._slurs = []
        seen = set()
        for p in self._placed:
            if id(p.segment) in seen:
                continue
            seen.add(id(p.segment))
            group = self.doc.song.groups[p.segment.group_id]
            mine = {q.note.id: q for q in self._placed if q.segment is p.segment}
            for first, last in group.slurs:
                a, b = mine.get(first), mine.get(last)
                if a is None or b is None:
                    continue
                # 2026-11-20: spostando la prima nota dopo l'ultima la slur si rovescia: prima
                # max() su una lista vuota bloccava la griglia (anche riaprendola)
                a, b = sorted((a, b), key=lambda q: q.start)
                inside = [q for q in mine.values() if a.start <= q.start <= b.start]
                self._slurs.append((a.start, max(q.end for q in inside if q.start == b.start),
                                    max(q.midi for q in inside), p.segment))
        for p in self._placed:
            if p.note.tie_next and (nxt := by_start.get((p.end, p.midi))) is not None:
                self._tied_next.add(id(p.note))
                self._tied_prev.add(id(nxt.note))
        self.selection = [n for n in self.selection if id(n) in self._by_note]

    def note_span_x(self, p: PlacedNote) -> tuple[float, float]:
        """Estensione orizzontale disegnata di una nota (sulla batteria: il rombo, fisso)."""
        x0 = self.x_of(p.start)
        if self.is_drums:
            return x0 - self.key_h / 2, x0 + self.key_h / 2
        return x0, max(self.x_of(p.end), x0 + 3)

    def note_at(self, x: float, y: float) -> PlacedNote | None:
        """Nota modificabile (non di un clone) sotto il puntatore."""
        midi = self.midi_of(y)
        for p in reversed(self._placed):
            if p.midi == midi and p.segment.clone_of is None:
                a, b = self.note_span_x(p)
                if a <= x < b:
                    return p
        return None

    def edge_at(self, x: float, y: float, min_width: float = 0) -> tuple[PlacedNote, str] | None:
        """Bordo (inizio/fine) di una nota modificabile vicino al puntatore (solo su note
        larghe almeno `min_width` px). Sulla batteria nessuno: i colpi non si ridimensionano."""
        if self.is_drums:
            return None
        midi = self.midi_of(y)
        for p in reversed(self._placed):
            if p.midi != midi or p.segment.clone_of is not None:
                continue
            if self.x_of(p.end) - self.x_of(p.start) < min_width:
                continue
            if abs(x - self.x_of(p.end)) <= EDGE_PX:
                return p, "end"
            if abs(x - self.x_of(p.start)) <= EDGE_PX:
                return p, "start"
        return None

    # --- gesti -------------------------------------------------------------

    def set_mode_cursor(self, mode: str) -> None:
        """Puntatore come nella vista Tracce: quattro frecce in Move, barra in Split, X in Delete."""
        if mode == "move":
            cursor = Gdk.Cursor.new_from_name("move")
        elif mode == "delete":
            cursor = Gdk.Cursor.new_from_name("X_cursor", Gdk.Cursor.new_from_name("default"))
        elif mode == "create":  # "+" del tema (mirino se il tema non ce l'ha)
            cursor = Gdk.Cursor.new_from_name("plus", Gdk.Cursor.new_from_name("crosshair"))
        elif mode == "split":
            cursor = Gdk.Cursor.new_from_name("ibeam", Gdk.Cursor.new_from_name("text"))
        else:
            cursor = None
        self._mode_cursor = cursor
        self.area.set_cursor(cursor)
        if mode != "split" and getattr(self, "_split_hint", None) is not None:
            self._split_hint = None
            self.area.queue_draw()

    def on_motion(self, _c, x: float, y: float) -> None:
        self._pointer_x = x
        self._pointer = (x, y)  # zoom verticale: riga sotto il puntatore
        mode = self.get_mode()
        self._update_split_hint(x, y if mode == "split" and self._op is None else None)
        if mode in ("resize", "select") and self._op is None:  # ↔ sui bordi delle note
            edge = x > self.kb_w and y > RULER_H and self.edge_at(x, y, SELECT_EDGE_MIN_PX if mode == "select" else 0)
            self.area.set_cursor(Gdk.Cursor.new_from_name("col-resize") if edge else self._mode_cursor)

    def on_leave(self, *_a) -> None:
        self._update_split_hint(0, None)

    def _update_split_hint(self, x: float, y: float | None) -> None:
        """Split: linea tratteggiata dove cadrebbe il taglio (agganciato allo Snap); niente
        linea se il punto agganciato è un bordo della nota (lì non si divide)."""
        hint = None
        if self.is_drums:
            y = None  # sulla batteria Split non si usa
        if y is not None and x > self.kb_w and y > RULER_H and (hit := self.note_at(x, y)) is not None:
            cut = self.snapped(self.pos_of(x))
            if hit.start < cut < hit.end:
                hint = (cut, hit.midi)
        if hint != self._split_hint:
            self._split_hint = hint
            self.area.queue_draw()

    def on_drag_begin(self, gesture: Gtk.GestureDrag, x: float, y: float) -> None:
        self.area.grab_focus()
        self._op = None
        if y <= RULER_H:  # righello: sposta il punto di play
            if x > self.kb_w:
                # 2026-11-20: anche dal righello il punto di play si aggancia allo Snap scelto,
                # come nell'area delle note e nel righello della notazione (prima andava al pixel).
                pos = self.snapped(self.pos_of(x))
                self.transport.seek(self.doc.song.tempos.seconds(max(pos, Fraction(0))))
                self.area.queue_draw()
            return
        if x < self.kb_w:  # tastiera: suona la nota finché è premuta (con Create la inserisce)
            if self.get_mode() == "create":
                self._insert_from_key(self.midi_of(y))
            else:
                self._audition(self.midi_of(y), self.track.velocity)
            return
        shift = bool(gesture.get_current_event_state() & Gdk.ModifierType.SHIFT_MASK)
        mode, hit = self.get_mode(), self.note_at(x, y)
        now, (t0, px, py) = GLib.get_monotonic_time(), self._last_press
        self._last_press = (now, x, y)
        double_ms = self.area.get_settings().get_property("gtk-double-click-time")
        if not shift and now - t0 <= double_ms * 1000 and abs(x - px) < 5 and abs(y - py) < 5:
            self._last_press = (0, 0.0, 0.0)
            if hit is not None and mode not in ("split", "delete"):
                self._op = None
                self._open_note(hit)
                return
        self._op = {"kind": "pending", "x0": x, "y0": y, "hit": hit, "shift": shift}
        if hit is not None and mode not in ("split", "delete"):
            self._audition(hit.midi, hit.note.velocity)  # selezionando una nota la si sente
        if shift:
            return  # con Maiusc: sempre gesti di selezione
        if mode == "create" and hit is None:
            # batteria: un colpo al punto agganciato più vicino, durata scritta fissa (1/4)
            start = self.snapped(self.pos_of(x), "round" if self.is_drums else "floor")
            length = DRUM_LENGTH if self.is_drums else self.unit
            midi = self.midi_of(y)
            seg = self.segment_at(self.track, start)
            self._op = {"kind": "create", "x0": x, "y0": y, "start": start, "end": start + length, "midi": midi, "seg": seg,
                        "ok": self._create_ok(seg, start, start + length, midi)}
            self._audition(midi, self.track.velocity)  # si sente la nota che si sta creando
            self.area.queue_draw()
        elif (mode == "resize" and (edge := self.edge_at(x, y))) or \
                (mode == "select" and (edge := self.edge_at(x, y, SELECT_EDGE_MIN_PX))):  # in Select: Resize sempre
            p, side = edge
            self._op = {"kind": "resize", "note": p, "side": side, "start": p.start, "end": p.end, "ok": True}

    def _create_ok(self, seg, start: Fraction, end: Fraction, midi: int) -> bool:
        """Create col mouse: la nota sta nel segmento e non tocca note della stessa altezza
        (2026-11-20: prima si controllava solo il segmento)."""
        return seg is not None and self._fits(seg, start, end, midi) and not self._same_pitch_clashes(
            [(self.track, start, end, midi)])

    def _insert_from_key(self, midi: int) -> None:
        """Create + tastiera: nota lunga un passo di Snap (Off: 1/16), di quell'altezza, sulla
        linea rossa (agganciata allo Snap); la si sente e la linea avanza alla sua fine.
        Solo dentro un segmento e senza sovrapporsi a una nota della stessa altezza."""
        if not 0 <= midi <= 127:
            return
        song = self.doc.song
        length = DRUM_LENGTH if self.is_drums else (self.snap or Fraction(1, 16))
        start = max(self.snapped(song.tempos.position(self.transport.position), step=self.snap or Fraction(1, 16)),
                    Fraction(0))
        seg = self.segment_at(self.track, start)
        clash = any(q.midi == midi and self._overlap(q, start, start + length) for q in song.placed_notes(self.track))
        if seg is None or not self._fits(seg, start, start + length, midi) or clash:
            self.on_message("The note doesn't fit here (outside a segment or over another note): not created.")
            self._flash_fail([(start, start + length, midi)])
            return
        if self.is_drums:  # 2026-11-20: un colpo vicino alla fine del segmento si accorcia fino alla fine
            length = min(length, song.segment_span(seg)[1] - start)
        note = Note(self._note_start(seg, start), length, midi - seg.transpose, velocity=self.track.velocity)
        self.doc.execute(AddNotes([(self._group(seg), note)]))
        self._audition(midi, self.track.velocity)
        GLib.timeout_add(250, lambda: self._audition(None) or False)
        self.transport.seek(song.tempos.seconds(start + length))
        self.area.queue_draw()

    def on_drag_update(self, _g, dx: float, dy: float) -> None:
        op = self._op
        if op is None:
            return
        if op["kind"] == "pending":
            if abs(dx) < 3 and abs(dy) < 3:
                return
            hit = op["hit"]
            if self.get_mode() in ("move", "select") and not op["shift"] and hit is not None:  # in Select: Move sempre
                if hit.note not in self.selection:  # la nota trascinata diventa (e resta) la selezione
                    self.selection = [hit.note]
                notes = self.selection
                op.update(kind="move", notes=[self._by_note[id(n)] for n in notes if id(n) in self._by_note],
                          dpos=Fraction(0), dmidi=0, ok=True)
            else:
                op.update(kind="band", x1=op["x0"], y1=op["y0"])
        x, y = op.get("x0", 0) + dx, op.get("y0", 0) + dy
        kind = op["kind"]
        if kind == "band":
            op["x1"], op["y1"] = x, y
        elif kind == "create":
            if self.is_drums:  # un colpo non si allunga trascinando
                self.area.queue_draw()
                return
            op["end"] = max(self.snapped(self.pos_of(x), "ceil"), op["start"] + self.unit)
            op["ok"] = self._create_ok(op["seg"], op["start"], op["end"], op["midi"])
        elif kind == "resize":
            p = op["note"]
            pos = self.snapped(self.pos_of(self.x_of(p.end if op["side"] == "end" else p.start) + dx))
            if op["side"] == "end":
                op["start"], op["end"] = p.start, max(pos, p.start + self.unit)
            else:
                op["start"], op["end"] = min(pos, p.end - self.unit), p.end
            # 2026-11-20: anche senza toccare note della stessa altezza (come frecce e Paste)
            op["ok"] = self._fits(p.segment, op["start"], op["end"], p.midi) and not self._same_pitch_clashes(
                [(self.track, op["start"], op["end"], p.midi)], ignore={id(p.note)})
        elif kind == "move":
            hit = op["hit"]
            raw = hit.start + Fraction(dx / self.px_per_whole).limit_denominator(1920)
            op["dpos"] = self.snapped(raw) - hit.start
            op["dmidi"] = -int(round(dy / self.key_h))
            self._audition(hit.midi + op["dmidi"], hit.note.velocity)  # si sente la nuova altezza
            # 2026-11-20: _free controlla segmento e note della stessa altezza (come le frecce)
            op["ok"] = self._free(op["notes"], op["dpos"], op["dmidi"])
        self.area.queue_draw()

    def on_drag_end(self, _g, dx: float, dy: float) -> None:
        self._audition(None)
        op, self._op = self._op, None
        if op is None:
            return
        kind = op["kind"]
        if kind == "pending":
            self._click(op)
        elif kind == "band":
            self._select_band(op)
        elif kind == "create":
            self._end_create(op)
        elif kind == "resize":
            self._end_resize(op)
        elif kind == "move":
            self._end_move(op)
        self.area.queue_draw()

    def _click(self, op: dict) -> None:
        hit, mode = op["hit"], self.get_mode()
        if op["shift"]:  # Maiusc+click: mette/toglie dalla selezione
            if hit is not None:
                if hit.note in self.selection:
                    self.selection.remove(hit.note)
                else:
                    self.selection.append(hit.note)
            return
        if mode == "split" and hit is not None:
            if self.is_drums:
                self.on_message("Drum hits can't be split.")
                return
            self._split(hit, self.snapped(self.pos_of(op["x0"])))
            self._split_hint = None
            return
        if mode == "delete" and hit is not None:
            notes = self.selection if hit.note in self.selection else [hit.note]
            self._delete(notes)
            return
        self.selection = [hit.note] if hit is not None else []
        pos = self.snapped(self.pos_of(op["x0"]))  # punto di play agganciato allo Snap scelto
        self.transport.seek(self.doc.song.tempos.seconds(max(pos, Fraction(0))))

    def _select_band(self, op: dict) -> None:
        x0, x1 = sorted((op["x0"], op["x1"]))
        y0, y1 = sorted((op["y0"], op["y1"]))
        inside = [p.note for p in self._placed if p.segment.clone_of is None
                  and self.note_span_x(p)[0] < x1 and self.note_span_x(p)[1] > x0
                  and self.y_of(p.midi) < y1 and self.y_of(p.midi) + self.key_h > y0]
        if op["shift"]:
            self.selection += [n for n in inside if n not in self.selection]
        else:
            self.selection = inside

    def _end_create(self, op: dict) -> None:
        if not op["ok"]:
            self.on_message("The note doesn't fit here (outside a segment or over a note of the same pitch): not created.")
            return
        seg = op["seg"]
        end = op["end"]
        if self.is_drums:  # 2026-11-20: un colpo vicino alla fine del segmento si accorcia fino alla fine
            end = min(end, self.doc.song.segment_span(seg)[1])
        note = Note(self._note_start(seg, op["start"]), end - op["start"], op["midi"] - seg.transpose,
                    velocity=self.track.velocity)
        self.doc.execute(AddNotes([(self._group(seg), note)]))
        self.selection = [note]

    def _end_resize(self, op: dict) -> None:
        p = op["note"]
        if not op["ok"]:
            self.on_message("The note would leave its segment or overlap a note of the same pitch: resize canceled.")
            return
        if (op["start"], op["end"]) != (p.start, p.end):
            self.doc.execute(EditNotes([(self._group(p.segment), p.note, {
                "start": self._note_start(p.segment, op["start"]), "duration": op["end"] - op["start"]})]))

    def _end_move(self, op: dict) -> None:
        if not op["ok"]:
            self.on_message("The notes would leave their segment or overlap a note of the same pitch: move canceled.")
            return
        if op["dpos"] == 0 and op["dmidi"] == 0:
            return
        self.doc.execute(EditNotes([(self._group(p.segment), p.note, {
            "start": p.note.start + op["dpos"], "midi": p.note.midi + op["dmidi"]}) for p in op["notes"]]))

    def on_right_click(self, _g, _n: int, x: float, y: float) -> None:
        """Tasto destro in un punto qualsiasi delle note: sempre gli strumenti (menu Grid) e,
        se ci sono note selezionate, sotto i comandi del menu Notes. Su una nota non
        selezionata, prima la seleziona."""
        if self.menu is None or x <= self.kb_w or y <= RULER_H:
            return
        hit = self.note_at(x, y)
        if hit is not None and hit.note not in self.selection:
            self.selection = [hit.note]
            self.area.queue_draw()
        model = self.menu
        if self.selection and self.notes_menu is not None:  # strumenti + separatore + comandi sulle note
            model = Gio.Menu()
            model.append_section(None, self.menu)
            model.append_section(None, self.notes_menu)
        point = _translate(self.area, self, x, y)
        if point is not None:
            popup_menu(model, self, *point)  # agganciato alla scheda intera, come nella vista Tracce

    # --- trasposizione e frecce ----------------------------------------------

    def on_key(self, _c, keyval: int, _code: int, state: Gdk.ModifierType) -> bool:
        """Tasti dei comandi sulle note (gli stessi del menu Notes, vedi note_editing)."""
        return self.note_key(Gdk.keyval_name(keyval), bool(state & Gdk.ModifierType.CONTROL_MASK))

    @property
    def kb_w(self) -> int:
        """Larghezza della colonna a sinistra: tastiera, o nomi dei suoni sulla batteria."""
        return DRUM_NAMES_W if self.is_drums else KEYBOARD_W

    # --- richiesti da NoteEditing ------------------------------------------

    @property
    def edit_tracks(self) -> list[Track]:
        return [self.track]

    def redraw(self) -> None:
        self.area.queue_draw()

    def insert_point(self) -> tuple[Track, Fraction, Fraction]:
        """Composer: sulla linea rossa (agganciata allo Snap), durata = Snap (Off: 1/16)."""
        length = self.snap or Fraction(1, 16)
        pos = self.doc.song.tempos.position(self.transport.position)
        return self.track, max(self.snapped(pos, step=length), Fraction(0)), length

    def paste_target(self) -> tuple[Track, Fraction] | None:
        """Paste: la traccia della griglia e il **punto di play** (la linea rossa, agganciato allo Snap).
        2026-11-23: prima il puntatore del mouse; ora come notazione e scheda Tracks (richiesta dell'utente:
        tutti gli incolla sulla linea rossa, qualsiasi sia la schermata)."""
        pos = self.doc.song.tempos.position(self.transport.position)
        return self.track, max(self.snapped(pos), Fraction(0))

    def on_doc_changed(self, _doc: Document) -> None:
        if self._tracks_gone():  # traccia cancellata: la finestra chiude la scheda
            return
        self._cache_notes()
        self.area.queue_draw()

    def on_tick(self) -> bool:
        if bool(self.selection) != self._had_selection:  # menu Notes: attivo solo con note selezionate
            self._had_selection = bool(self.selection)
            self.on_selection()
        pos = self.transport.position
        if pos != getattr(self, "_last_pos", None):
            self._last_pos = pos
            if self.transport.state.value == "playing":  # il punto di play arriva al centro e ci resta
                where = float(self.doc.song.tempos.position(pos))
                follow_center(self.hadj, (where - self.origin) * self.px_per_whole)
            self.area.queue_draw()
        return GLib.SOURCE_CONTINUE

    def shutdown(self) -> None:
        """Da chiamare quando la scheda viene chiusa."""
        GLib.source_remove(self._timer)
        self.doc.unsubscribe(self.on_doc_changed)

    # --- disegno ---------------------------------------------------------

    def draw(self, _area, cr, w: int, h: int) -> None:
        dark = is_dark(self.area)
        song = self.doc.song
        bg_white = (0.17, 0.17, 0.19) if dark else (0.97, 0.97, 0.98)
        bg_black = (0.13, 0.13, 0.15) if dark else (0.90, 0.90, 0.92)
        line = (1, 1, 1) if dark else (0, 0, 0)

        # righe dei tasti
        top_key = 127 - int((self.vadj.get_value()) // self.key_h)
        for midi in range(top_key, max(top_key - int(h // self.key_h) - 2, -1), -1):
            y = self.y_of(midi)
            cr.set_source_rgb(*(bg_black if midi % 12 in BLACK else bg_white))
            cr.rectangle(self.kb_w, y, w, self.key_h)
            cr.fill()
            if midi % 12 == 0:
                cr.set_source_rgba(*line, 0.25)
                cr.rectangle(self.kb_w, y + self.key_h - 1, w, 1)
                cr.fill()

        # linee verticali: stanghette, movimenti, suddivisioni
        meters = song.meters
        first_bar = meters.bar_at(Fraction(self.origin + max(self.hadj.get_value(), 0) / self.px_per_whole).limit_denominator(1 << 16))
        bar = first_bar
        while True:
            start = meters.bar_start(bar)
            x = self.x_of(start)
            if x > w or bar > song.bar_count or float(start) > self.end:
                break
            ts = meters.at(bar)
            sub = self.snap or Fraction(1, 16)  # linee della griglia = Snap (Off: 1/16)
            if self.px_per_whole * float(sub) < 6:  # troppo fitte: solo i movimenti
                sub = ts.beat
            n = (ts.bar_length / sub).__ceil__()
            for i in range(n):
                xi = round(self.x_of(start + sub * i))  # linea su un pixel intero: centro = xi + 0.5
                alpha = 0.45 if i == 0 else 0.18 if (sub * i) % ts.beat == 0 else 0.07
                cr.set_source_rgba(*line, alpha)
                cr.rectangle(xi, RULER_H, 1, h)
                cr.fill()
            bar += 1

        # note (dalla cache: solo quelle visibili); cloni grigi, selezionate con bordo blu
        p_end = self.origin + (w - self.kb_w + self.hadj.get_value()) / self.px_per_whole
        p_start = self.origin + self.hadj.get_value() / self.px_per_whole
        moving = {id(p.note) for p in self._op["notes"]} if self._op and self._op["kind"] == "move" else set()
        resizing = id(self._op["note"].note) if self._op and self._op["kind"] == "resize" else None
        selected = {id(n) for n in self.selection}
        bridges = []
        for p in self._placed[: bisect_right(self._starts, p_end)]:
            if float(p.end) < p_start:
                continue
            clone = p.segment.clone_of is not None
            rgb = CLONE_RGB if clone else group_color(song, p.segment.group_id)
            faded = id(p.note) in moving or id(p.note) == resizing  # in modifica: attenuata, l'anteprima sopra
            self._draw_note(cr, p.start, p.end, p.midi, rgb, 0.15 if faded else 0.45 + 0.55 * p.note.velocity / 127,
                            id(p.note) in selected, p.note.staccato)
            if id(p.note) in self._tied_next:
                bridges.append((p, rgb))
        # legature di frase (S1): arco scuro sopra la frase, dall'inizio della prima nota alla
        # fine dell'ultima
        for start, end, top_midi, seg in self._slurs:
            x0, x1 = self.x_of(start) + 2, self.x_of(end) - 2
            if x1 < self.kb_w or x0 > w:
                continue
            r, g, b = CLONE_RGB if seg.clone_of else group_color(song, seg.group_id)
            y = self.y_of(top_midi) - 3
            rise = min(max((x1 - x0) * 0.12, 8), 2.5 * self.key_h)
            cr.move_to(x0, y)
            cr.curve_to(x0 + (x1 - x0) * 0.25, y - rise, x0 + (x1 - x0) * 0.75, y - rise, x1, y)
            cr.set_source_rgb(r * 0.3, g * 0.3, b * 0.3)
            cr.set_line_width(1.5)
            cr.stroke()
        # legature di valore: le note restano attaccate (si vede che sono a tempo); un
        # ponticello scuro a cavallo del punto in cui si toccano, sopra entrambe le note
        for p, (r, g, b) in bridges:
            cr.rectangle(self.x_of(p.end) - TIE_BRIDGE / 2, self.y_of(p.midi) + (self.key_h - TIE_BRIDGE_H) / 2,
                         TIE_BRIDGE, TIE_BRIDGE_H)
            cr.set_source_rgb(r * 0.3, g * 0.3, b * 0.3)
            cr.fill()
        self._draw_op(cr, song)

        self._draw_chord_symbols(cr, w, dark)
        self._draw_ruler(cr, w, dark)
        self._draw_keyboard(cr, h, dark)

        # cursore
        pos = song.tempos.position(self.transport.position)
        x = self.x_of(pos)
        if self.kb_w <= x <= w:
            cr.set_source_rgb(*PLAYHEAD)
            cr.rectangle(x, 0, 1.5, h)
            cr.fill()

    def _draw_note(self, cr, start, end, midi: int, rgb, alpha: float, selected: bool = False,
                   staccato: bool = False) -> None:
        x0, x1 = self.x_of(start), self.x_of(end)
        y = self.y_of(midi)
        r, g, b = rgb
        if self.is_drums:  # batteria: rombo di dimensione fissa centrato sull'attacco (un colpo non ha durata)
            if x0 < self.kb_w:
                return
            half = self.key_h / 2 - 1
            cx, cy = round(x0) + 0.5, y + self.key_h / 2  # stesso centro delle linee della griglia
            cr.move_to(cx, cy - half)
            cr.line_to(cx + half, cy)
            cr.line_to(cx, cy + half)
            cr.line_to(cx - half, cy)
            cr.close_path()
        else:
            _round_rect(cr, max(x0, self.kb_w - 4), y + 1, max(x1 - x0 - 1, 2), self.key_h - 2, 2)
        if selected:  # come i segmenti selezionati: colore del gruppo scuro e pieno, bordo scuro
            cr.set_source_rgb(r * 0.55, g * 0.55, b * 0.55)
            cr.fill_preserve()
            cr.set_source_rgb(r * 0.3, g * 0.3, b * 0.3)
            cr.set_line_width(2)
        else:
            cr.set_source_rgba(r, g, b, alpha)
            cr.fill_preserve()
            cr.set_source_rgba(r * 0.6, g * 0.6, b * 0.6, 1)
            cr.set_line_width(1)
        cr.stroke()
        if staccato:  # puntino scuro all'inizio, come il segno in partitura
            cr.arc(max(x0, self.kb_w) + 5, y + self.key_h / 2, 2.5, 0, 6.2832)
            cr.set_source_rgb(r * 0.3, g * 0.3, b * 0.3) if not selected else cr.set_source_rgb(1, 1, 1)
            cr.fill()

    def _draw_op(self, cr, song) -> None:
        """Anteprima del gesto in corso: rossa se non è possibile."""
        if self._split_hint is not None:  # come lo Split dei segmenti: linea blu tratteggiata
            cut, midi = self._split_hint
            x, y = self.x_of(cut), self.y_of(midi)
            cr.set_source_rgb(0.13, 0.55, 1.0)
            cr.set_line_width(2)
            cr.set_dash([4, 3])
            cr.move_to(x, y - 3)
            cr.line_to(x, y + self.key_h + 3)
            cr.stroke()
            cr.set_dash([])
        red = (0.95, 0.2, 0.2)
        for start, end, midi in self._fail:
            self._draw_note(cr, start, end, midi, red, 0.85)
        op = self._op
        if op is None:
            return
        if op["kind"] == "band":
            x0, x1 = sorted((op["x0"], op["x1"]))
            y0, y1 = sorted((op["y0"], op["y1"]))
            cr.rectangle(x0 + 0.5, y0 + 0.5, x1 - x0, y1 - y0)
            cr.set_source_rgba(*SELECT_RGB, 0.15)
            cr.fill_preserve()
            cr.set_source_rgba(*SELECT_RGB, 0.9)
            cr.set_line_width(1)
            cr.stroke()
        elif op["kind"] == "create":
            rgb = group_color(song, op["seg"].group_id) if op["ok"] else red
            self._draw_note(cr, op["start"], op["end"], op["midi"], rgb, 0.9)
        elif op["kind"] == "resize":
            p = op["note"]
            rgb = group_color(song, p.segment.group_id) if op["ok"] else red
            self._draw_note(cr, op["start"], op["end"], p.midi, rgb, 0.9, True)
        elif op["kind"] == "move":
            for p in op["notes"]:
                rgb = group_color(song, p.segment.group_id) if op["ok"] else red
                self._draw_note(cr, p.start + op["dpos"], p.end + op["dpos"], p.midi + op["dmidi"], rgb, 0.9, True)

    def _draw_chord_symbols(self, cr, w: int, dark: bool) -> None:
        """Sigle d'accordo dei segmenti, in alto sotto il righello, alla loro posizione."""
        song = self.doc.song
        cr.select_font_face(TEXT_FONT, 0, 1)  # con ♯ e ♭
        cr.set_font_size(12)
        for seg in self.track.segments:
            start, _end = song.segment_span(seg)
            for pos, text in song.groups[seg.group_id].chords:
                rel = pos - seg.trim
                if not 0 <= rel < seg.length:
                    continue
                x = self.x_of(start + rel)
                if x < self.kb_w or x > w:
                    continue
                label = _pretty_symbol(text)
                ext = cr.text_extents(label)
                cr.set_source_rgba(*((0.17, 0.17, 0.19) if dark else (0.97, 0.97, 0.98)), 0.9)
                cr.rectangle(x + 1, RULER_H + 2, ext.width + 6, 16)
                cr.fill()
                cr.set_source_rgb(*((0.9, 0.9, 0.9) if dark else (0.15, 0.15, 0.17)))
                cr.move_to(x + 4, RULER_H + 14)
                cr.show_text(label)

    def _draw_ruler(self, cr, w: int, dark: bool) -> None:
        song = self.doc.song
        bg, _fg = ruler.ruler_colors(dark)
        cr.set_source_rgb(*bg)
        cr.rectangle(0, 0, w, RULER_H)
        cr.fill()
        # banda dei segmenti della traccia (sotto il righello, che resta il primo elemento)
        for seg in self.track.segments:
            s0, s1 = song.segment_span(seg)
            x0, x1 = self.x_of(s0), self.x_of(s1)
            r, g, b = group_color(song, seg.group_id)
            cr.set_source_rgb(r, g, b)
            cr.rectangle(max(x0, self.kb_w), ruler.RULER_H, max(x1 - max(x0, self.kb_w), 0), SEG_H)
            cr.fill()
        last = song.bar_count if self.last_bar is None else self.last_bar
        ruler.draw_ruler(cr, self.kb_w, 0, w - self.kb_w, ruler.RULER_H, song, self.first_bar, last,
                         self.x_of, dark)

    def _draw_keyboard(self, cr, h: int, dark: bool) -> None:
        cr.set_source_rgb(*((0.25, 0.25, 0.28) if dark else (1, 1, 1)))
        cr.rectangle(0, RULER_H, self.kb_w, h)
        cr.fill()
        cr.select_font_face("sans-serif")
        cr.set_font_size(min(9 + (self.key_h - KEY_H) * 0.4, 14))  # più grande con lo zoom verticale
        for midi in range(128):
            y = self.y_of(midi)
            if y < RULER_H - self.key_h or y > h:
                continue
            cr.set_source_rgb(*((0.4, 0.4, 0.44) if dark else (0.8, 0.8, 0.8)))  # linea grigia fra i tasti
            cr.rectangle(0, y + self.key_h - 1, self.kb_w, 1)
            cr.fill()
            if self.is_drums:  # batteria: nome del suono GM al posto dei tasti
                if midi == self._preview_key:
                    cr.set_source_rgb(*SELECT_RGB)
                    cr.rectangle(0, y + 1, self.kb_w - 1, self.key_h - 2)
                    cr.fill()
                name = GM_DRUMS.get(midi)
                light = midi == self._preview_key or dark
                cr.set_source_rgb(*((0.95, 0.95, 0.95) if light else (0.2, 0.2, 0.22)) if name
                                  else ((0.5, 0.5, 0.5) if dark else (0.75, 0.75, 0.75)))
                label = f"{midi} {name}" if name else str(midi)
                ext = cr.text_extents(label)
                cr.move_to(4, y + self.key_h / 2 - ext.y_bearing - ext.height / 2)  # centrato in verticale
                cr.show_text(label)
                continue
            if midi % 12 in BLACK:
                cr.set_source_rgb(0.1, 0.1, 0.12)
                cr.rectangle(0, y + 1, self.kb_w * 0.6, self.key_h - 2)
                cr.fill()
            if midi == self._preview_key:  # nota che sta suonando: tasto illuminato
                width = self.kb_w * 0.6 if midi % 12 in BLACK else self.kb_w - 1
                cr.set_source_rgb(*SELECT_RGB)
                cr.rectangle(0, y + 1, width, self.key_h - 2)
                cr.fill()
            if midi % 12 == 0:
                cr.set_source_rgb(*((0.85, 0.85, 0.85) if dark else (0.3, 0.3, 0.3)))
                cr.move_to(self.kb_w - 26, y + self.key_h - 2)
                cr.show_text(f"C{midi // 12 - 1}")
                cr.rectangle(0, y + self.key_h - 1, self.kb_w, 1)
                cr.fill()
        cr.set_source_rgba(0.5, 0.5, 0.5, 0.6)
        cr.rectangle(self.kb_w - 1, RULER_H, 1, h)
        cr.fill()


def _round_rect(cr, x: float, y: float, w: float, h: float, r: float) -> None:
    r = min(r, w / 2, h / 2)
    cr.new_sub_path()
    cr.arc(x + w - r, y + r, r, -1.5708, 0)
    cr.arc(x + w - r, y + h - r, r, 0, 1.5708)
    cr.arc(x + r, y + h - r, r, 1.5708, 3.1416)
    cr.arc(x + r, y + r, r, 3.1416, 4.7124)
    cr.close_path()


__all__ = ["PianoRollWindow", "NOTE_NAMES"]
