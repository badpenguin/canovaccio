"""Vista notazione: si legge e si modificano le note (comandi comuni con la griglia,
vedi note_editing), tranne crearne di nuove.

Una o più tracce (un rigo ciascuna) su un intervallo di misure, in un solo
rigo continuo che scorre in orizzontale. In verticale i righi sono distribuiti
come "space-evenly" del CSS nello spazio sotto il righello (stesso spazio
sopra, fra i righi e sotto); se non ci stanno, si scorre. Rotella: avanti/indietro lungo la
timeline; Maiusc+rotella: su/giù; Ctrl+rotella: zoom. Click sul righello: punto di play.
Le note arrotondate solo a video (dati non quantizzati) sono in rosso.

Sulle note (teste): selezione sempre attiva (click, Maiusc+click, rettangolo dallo
spazio vuoto); click su una nota = la seleziona, la fa sentire e sposta il punto di
play (agganciato allo Snap); trascinarla (Select o Move) = sposta: in verticale segue
il puntatore riga per riga (note della tonalità; i semitoni con le frecce), in
orizzontale a passi di Snap; Split = la divide a metà (agganciata allo
Snap); Delete = la cancella; doppio click = proprietà; tasto destro = strumenti e,
con note selezionate, i comandi del menu Notes. Note selezionate in blu.

L'area di disegno è grande quanto la finestra (non quanto lo spartito):
a ogni ridisegno si disegna solo la parte visibile.
"""

from __future__ import annotations

from fractions import Fraction

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
from gi.repository import Gdk, Gio, GLib, Gtk  # noqa: E402

from ..core.document import AddNotes, Document, EditNotes  # noqa: E402
from ..core.model import Note, PlacedNote, Track  # noqa: E402
from ..core.pitch import STEPS, Pitch, Spelling  # noqa: E402
from ..engraving.layout import RULER_HEIGHT, Glyph, ScoreLayout, _key_alters, engrave  # noqa: E402
from ..output.transport import Transport  # noqa: E402
from .keys import PRIMARY_MASK  # noqa: E402
from .follow import follow_center  # noqa: E402
from . import ruler  # noqa: E402
from .note_editing import DEFAULT_SNAP, SNAPS, NoteEditing  # noqa: E402
from .popup import popup_menu  # noqa: E402
from .score_render import SELECTED, draw_score  # noqa: E402
from .icons import themed  # noqa: E402
from .toolbar import snap_buttons  # noqa: E402
from .theme import PLAYHEAD  # noqa: E402

MARGIN_X = 24  # px a sinistra/destra dello spartito
DEFAULT_SPACE = 10.0
SCROLL_STEP = 60  # px per scatto di rotella


HEAD_W = 1.2  # larghezza di una testa (spazi), per il click
KEYBOARD_H = 70  # px della tastiera in basso
KEY_LOW, KEY_HIGH = 24, 108  # Do1–Do8: 7 ottave
BLACK_H = 0.6  # altezza dei tasti neri (frazione)
BLACK = {1, 3, 6, 8, 10}
DRAG_PX = 4  # oltre questo spostamento un click diventa trascinamento


class NotationView(NoteEditing, Gtk.Box):
    kind = "notation"

    def __init__(
        self,
        doc: Document,
        tracks: Track | list[Track],
        transport: Transport,
        first_bar: int = 0,
        last_bar: int | None = None,
        get_mode=lambda: "select",
        get_length=lambda: Fraction(1, 4),
        on_message=lambda _t, error=True: None,
        menu=None,
        notes_menu=None,
        on_selection=lambda: None,
    ) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self.doc, self.transport = doc, transport
        self.tracks = [tracks] if isinstance(tracks, Track) else list(tracks)
        self.track = self.tracks[0]
        self.first_bar = first_bar
        self.last_bar = last_bar  # None = fino alla fine del brano
        self.space = DEFAULT_SPACE  # pixel per spazio del rigo
        self.layout: ScoreLayout | None = None
        self._last_pos = -1.0
        self.get_mode, self.on_message = get_mode, on_message
        self.get_length = get_length  # valore scelto nella palette (note create con Create)
        self.menu, self.notes_menu, self.on_selection = menu, notes_menu, on_selection
        self._had_selection = False
        self.init_note_editing()
        self.snap: Fraction | None = SNAPS[DEFAULT_SNAP][1]
        self._op: dict | None = None  # gesto in corso: pending / band / move
        self._last_press = (0, 0.0, 0.0)  # doppio click
        self._pointer: tuple[float, float] | None = None
        self._clicked: tuple[Track, Fraction] | None = None  # Paste: rigo e punto dell'ultimo click

        self.area = Gtk.DrawingArea(hexpand=True, vexpand=True, focusable=True)
        self.hadj = Gtk.Adjustment()
        self.vadj = Gtk.Adjustment()
        for adj in (self.hadj, self.vadj):
            adj.connect("value-changed", lambda *_: self.area.queue_draw())
        self.area.set_draw_func(self.draw)
        self.area.connect("resize", lambda *_: self.update_adjustments())
        drag = Gtk.GestureDrag()
        drag.connect("drag-begin", self.on_drag_begin)
        drag.connect("drag-update", self.on_drag_update)
        drag.connect("drag-end", self.on_drag_end)
        self.area.add_controller(drag)
        right = Gtk.GestureClick(button=3)  # tasto destro: strumenti (+ comandi Notes con selezione)
        right.connect("pressed", self.on_right_click)
        self.area.add_controller(right)
        motion = Gtk.EventControllerMotion()
        motion.connect("motion", lambda _c, x, y: setattr(self, "_pointer", (x, y)))
        self.area.add_controller(motion)
        keys = Gtk.EventControllerKey()  # frecce e tasti dei comandi sulle note
        keys.connect("key-pressed", lambda _c, kv, _code, st: self.note_key(
            Gdk.keyval_name(kv), bool(st & PRIMARY_MASK)))
        self.area.add_controller(keys)
        scroll = Gtk.EventControllerScroll.new(Gtk.EventControllerScrollFlags.BOTH_AXES)
        scroll.connect("scroll", self.on_scroll)
        self.area.add_controller(scroll)

        # tastiera di pianoforte in basso (7 ottave, stile MuseScore): suona, si illumina
        # durante l'ascolto, con Create inserisce
        self.keyboard = Gtk.DrawingArea(content_height=KEYBOARD_H, hexpand=True)
        self.keyboard.set_draw_func(self._draw_keyboard)
        keys_click = Gtk.GestureClick()
        keys_click.connect("pressed", self._on_key_pressed)
        keys_click.connect("released", lambda *_: self._audition(None))
        self.keyboard.add_controller(keys_click)
        grid = Gtk.Grid()
        grid.attach(self.area, 0, 0, 1, 1)
        grid.attach(Gtk.Scrollbar(orientation=Gtk.Orientation.VERTICAL, adjustment=self.vadj), 1, 0, 1, 1)
        grid.attach(Gtk.Scrollbar(orientation=Gtk.Orientation.HORIZONTAL, adjustment=self.hadj), 0, 1, 1, 1)
        grid.attach(self.keyboard, 0, 2, 2, 1)

        bar = Gtk.Box(spacing=2, margin_start=6, margin_end=6, margin_top=2, margin_bottom=2, halign=Gtk.Align.END)  # toolbar inferiore
        # aggancio della notazione (indipendente dalla griglia), a pulsanti con i simboli delle note
        snap = snap_buttons(SNAPS[DEFAULT_SNAP][1], lambda v: setattr(self, "snap", v))
        snap.set_margin_end(12)
        bar.append(snap)
        for icon, step in (("zoom-out-symbolic", -1), ("zoom-in-symbolic", 1)):
            b = Gtk.Button(icon_name=themed(icon), tooltip_text="Zoom")
            b.connect("clicked", lambda _b, s=step: self.zoom_step(s))
            b.add_css_class("flat")
            b.set_focus_on_click(False)
            bar.append(b)

        self.append(grid)
        self.append(Gtk.Separator())
        self.append(bar)

        self.relayout()
        self.set_mode_cursor(get_mode())
        self.connect("map", lambda *_: GLib.timeout_add(30, self._initial_scroll))
        doc.subscribe(self.on_doc_changed)
        self.connect("map", self._on_map)
        self._timer = GLib.timeout_add(50, self.on_tick)

    @property
    def key(self) -> tuple:
        """Identità della scheda: stesse tracce e stesse misure → stessa scheda."""
        return ("notation", tuple(t.id for t in self.tracks), self.first_bar, self.last_bar)

    # --- dimensioni e scorrimento -------------------------------------------

    def content_size(self) -> tuple[float, float]:
        if not self.layout:
            return 0.0, 0.0
        height = 0.0 if self.staff_offsets() is not None else self.layout.height * self.space  # distribuito: niente scorrimento
        return self.layout.width * self.space + 2 * MARGIN_X, height

    def update_adjustments(self) -> None:
        w, h = self.area.get_width(), self.area.get_height()
        cw, ch = self.content_size()
        for adj, page, upper in ((self.hadj, w, cw), (self.vadj, h, ch)):
            page = max(page, 1)
            upper = max(upper, page)
            adj.configure(min(adj.get_value(), upper - page), 0, upper, SCROLL_STEP, page * 0.9, page)

    def zoom_step(self, direction: int) -> None:
        """+1 ingrandisce, -1 rimpicciolisce, 0 torna alla dimensione normale."""
        self.set_space(DEFAULT_SPACE if direction == 0 else self.space * 1.15**direction)

    def set_space(self, value: float) -> None:
        old = self.space
        center = (self.hadj.get_value() + self.hadj.get_page_size() / 2 - MARGIN_X) / old
        self.space = min(max(value, 4.0), 24.0)
        self.update_adjustments()
        self.hadj.set_value(max(center * self.space + MARGIN_X - self.hadj.get_page_size() / 2, 0))
        self.area.queue_draw()

    def relayout(self) -> None:
        """Rifà l'impaginazione dello spartito e le strutture che ne dipendono."""
        self._layout_version = self.doc.version
        self._stale = False
        self.layout = engrave(self.doc.song, self.tracks, None, self.first_bar, self.last_bar)
        self._cache_notes()
        self.update_adjustments()
        self.area.queue_draw()

    # --- richiesti da NoteEditing ------------------------------------------

    @property
    def edit_tracks(self) -> list[Track]:
        return self.tracks

    def redraw(self) -> None:
        self.area.queue_draw()
        self.keyboard.queue_draw()  # tasto illuminato

    # --- tastiera ---------------------------------------------------------------

    def _keyboard_track(self) -> Track:
        """Rigo attivo: quello dell'ultimo click (all'inizio il primo)."""
        return self._clicked[0] if self._clicked is not None else self.tracks[0]

    def _white_keys(self) -> list[int]:
        return [m for m in range(KEY_LOW, KEY_HIGH + 1) if m % 12 not in BLACK]

    def _key_geometry(self, w: int, h: int) -> tuple[float, list[tuple[int, float, float, bool]]]:
        """Tasti (midi, x, larghezza, nero) per un'area larga `w`: i bianchi la riempiono."""
        whites = self._white_keys()
        ww = w / len(whites)
        keys, x = [], 0.0
        for m in range(KEY_LOW, KEY_HIGH + 1):
            if m % 12 in BLACK:
                keys.append((m, x - ww * 0.3, ww * 0.6, True))
            else:
                keys.append((m, x, ww, False))
                x += ww
        return ww, keys

    def _key_at(self, x: float, y: float) -> int | None:
        w, h = self.keyboard.get_width(), self.keyboard.get_height()
        _ww, keys = self._key_geometry(w, h)
        if y < h * BLACK_H:  # prima i neri (stanno sopra)
            for m, kx, kw, black in keys:
                if black and kx <= x < kx + kw:
                    return m
        return next((m for m, kx, kw, black in keys if not black and kx <= x < kx + kw), None)

    def _on_key_pressed(self, _g, _n: int, x: float, y: float) -> None:
        """Tasto: suona; con lo strumento Create inserisce la nota (valore della palette)
        sulla linea rossa, sul rigo attivo, e la linea avanza."""
        midi = self._key_at(x, y)
        if midi is None:
            return
        track = self._keyboard_track()
        if self.get_mode() == "create":
            pos = self.doc.song.tempos.position(self.transport.position)
            self._insert(track, self._insert_start(pos), midi)
        else:
            self._audition(midi, track.velocity, track)

    def _draw_keyboard(self, _a, cr, w: int, h: int) -> None:
        """Pianoforte orizzontale (Do1–Do8): bianchi con bordo grigio, neri sopra; il tasto
        che sta suonando (ascolto) in blu; nome dei Do."""
        _ww, keys = self._key_geometry(w, h)
        cr.set_source_rgb(1, 1, 1)
        cr.paint()
        cr.select_font_face("sans-serif")
        cr.set_font_size(9)
        for m, kx, kw, black in keys:
            if black:
                continue
            if m == self._preview_key:
                cr.set_source_rgb(*SELECTED)
                cr.rectangle(kx, 0, kw, h)
                cr.fill()
            cr.set_source_rgb(0.7, 0.7, 0.7)
            cr.rectangle(kx + kw - 1, 0, 1, h)
            cr.fill()
            if m % 12 == 0:
                cr.set_source_rgb(0.35, 0.35, 0.35)
                label = f"C{m // 12 - 1}"
                ext = cr.text_extents(label)
                cr.move_to(kx + (kw - ext.width) / 2, h - 4)
                cr.show_text(label)
        for m, kx, kw, black in keys:
            if black:
                cr.set_source_rgb(*(SELECTED if m == self._preview_key else (0.1, 0.1, 0.12)))
                cr.rectangle(kx, 0, kw, h * BLACK_H)
                cr.fill()
        cr.set_source_rgb(0.7, 0.7, 0.7)
        cr.rectangle(0, 0, w, 1)
        cr.fill()

    def _cache_notes(self) -> None:
        """Note mostrate e teste del layout (con le note che rappresentano), per il click."""
        song = self.doc.song
        lo = song.meters.bar_start(self.first_bar)
        hi = song.meters.bar_start(song.bar_count if self.last_bar is None else self.last_bar)
        self._placed = [p for t in self.tracks for p in song.placed_notes(t) if p.start < hi and p.end > lo]
        self._by_note = {id(p.note): p for p in self._placed if p.segment.clone_of is None}
        self._by_key = {(p.segment.id, p.note.id): p for p in self._placed}
        self._heads = [it for it in self.layout.items if isinstance(it, Glyph) and it.keys]
        self.selection = [n for n in self.selection if id(n) in self._by_note]

    def insert_point(self) -> tuple[Track, Fraction, Fraction]:
        """Composer: sulla linea rossa del rigo attivo, durata = valore della palette."""
        pos = self.doc.song.tempos.position(self.transport.position)
        return self._keyboard_track(), self._insert_start(pos), self.get_length()

    def paste_target(self) -> tuple[Track, Fraction] | None:
        """Paste: nel **punto di play** (la linea rossa, agganciato allo Snap), sul rigo
        dell'ultimo click (all'inizio il primo rigo)."""
        pos = self.doc.song.tempos.position(self.transport.position)
        track = self._clicked[0] if self._clicked is not None else self.tracks[0]
        return track, max(self.snapped(pos), Fraction(0))

    # --- palette dei valori: nella misura le note seguenti fanno spazio -----------

    def set_lengths(self, length: Fraction) -> None:
        """Palette dei valori nello Score: le note selezionate prendono la durata scelta e
        le note che seguono **nella stessa misura** (stesso segmento) si spostano della
        differenza: allungando fanno spazio, accorciando restano attaccate (le pause fra
        le note restano quelle che erano). Se qualcosa uscirebbe dalla misura: messaggio,
        nulla cambia. (Ctrl + / − del tastierino non spostano nulla.)"""
        song = self.doc.song
        placed = self._selected_placed()
        if not placed:
            return
        selected = {id(p.note) for p in placed}
        changes = []
        groups: dict[tuple, list[PlacedNote]] = {}
        for p in placed:
            bar = song.meters.bar_at(p.start)
            groups.setdefault((id(p.segment), bar), []).append(p)
        for (_seg_id, bar), sel in groups.items():
            seg = sel[0].segment
            bs, be = song.meters.bar_start(bar), song.meters.bar_start(bar + 1)
            # 2026-11-20: il limite è anche la fine del segmento (se finisce a metà misura): prima
            # le note spinte oltre sparivano
            be = min(be, song.segment_span(seg)[1])
            notes = sorted((q for q in song.placed_notes(self._track_of(sel[0]))
                            if q.segment is seg and bs <= q.start < be), key=lambda q: q.start)
            shift, new = Fraction(0), []
            for onset in sorted({q.start for q in notes}):
                at = [q for q in notes if q.start == onset]
                mine = [q for q in at if id(q.note) in selected]
                for q in at:
                    new.append((q, onset + shift, length if id(q.note) in selected else q.duration))
                if mine:
                    shift += length - max(q.duration for q in mine)
            if any(start + dur > be for _q, start, dur in new):
                self.on_message("Not enough room in the bar: the notes would go past the bar or the segment.")
                return
            for q, start, dur in new:
                values = {}
                if start != q.start:
                    values["start"] = self._note_start(seg, start)
                if dur != q.duration:
                    values["duration"] = dur
                if values:
                    changes.append((self._group(seg), q.note, values))
        if changes:
            self.doc.execute(EditNotes(changes))

    # --- appunti: dopo Copy / Cut / Paste il punto di play avanza --------------

    def _advance_after(self, end: Fraction | None) -> None:
        """Punto di play al primo punto di Snap dopo `end` (fine delle note copiate,
        tagliate o incollate): così Ctrl+V di seguito accoda le copie."""
        if end is None:
            return
        pos = self.snapped(end, "ceil")
        self.transport.seek(self.doc.song.tempos.seconds(max(pos, Fraction(0))))
        self.area.queue_draw()

    def _selection_end(self) -> Fraction | None:
        placed = self._selected_placed()
        return max(p.end for p in placed) if placed else None

    def copy_selection(self) -> bool:
        end = self._selection_end()
        ok = NoteEditing.copy_selection(self)
        if ok:
            self._advance_after(end)
        return ok

    def cut_selection(self) -> None:
        end = self._selection_end()
        if NoteEditing.copy_selection(self):
            self.delete_selection()
            self._advance_after(end)

    def paste(self) -> None:
        before = list(self.selection)
        NoteEditing.paste(self)
        if self.selection and self.selection != before:  # incollato: le note incollate sono la selezione
            self._advance_after(self._selection_end())

    # --- coordinate e teste ----------------------------------------------------

    def _to_space(self, x: float, y: float) -> tuple[float, float]:
        return (x + self.hadj.get_value() - MARGIN_X) / self.space, (y + self.vadj.get_value()) / self.space

    def _staff_dy(self, staff: int) -> float:
        offs = self.staff_offsets()
        return offs[staff] if offs else 0.0

    def _at(self, x: float, y: float) -> tuple[Track, Fraction] | None:
        """Rigo (traccia) e posizione musicale nel punto (px dell'area)."""
        if not self.layout or not self.layout.systems:
            return None
        sx, sy = self._to_space(x, y)
        sys = self.layout.systems[0]
        tops = [t + self._staff_dy(k) for k, t in enumerate(sys.staff_tops)]
        k = min(range(len(tops)), key=lambda i: abs(sy - (tops[i] + 2)))
        # sopra una colonna di note (teste): proprio l'attacco di quella colonna; in
        # notazione le note sono vicine e l'interpolazione arrotondata cadrebbe sulla dopo
        near = [(abs(sx - (x + HEAD_W / 2)), pos) for pos, x in sys.x_map]
        dist, column = min(near, key=lambda d: d[0]) if near else (None, None)
        if dist is not None and dist <= HEAD_W * 0.75:
            return self.tracks[k], column
        pos = self.layout.position_at(sx, sys.top + 1)
        return (self.tracks[k], pos) if pos is not None else None

    def head_at(self, x: float, y: float) -> PlacedNote | None:
        """Nota modificabile (non di un clone) la cui testa è sotto il puntatore."""
        sx, sy = self._to_space(x, y)
        for g in reversed(self._heads):
            gy = g.y + self._staff_dy(g.staff)
            if g.x - 0.2 <= sx <= g.x + HEAD_W + 0.2 and gy - 0.6 <= sy <= gy + 0.6:
                for key in g.keys:
                    p = self._by_key.get(key)
                    if p is not None and p.segment.clone_of is None:
                        return p
        return None

    def _heads_in(self, x0: float, y0: float, x1: float, y1: float) -> list:
        (ax, ay), (bx, by) = self._to_space(x0, y0), self._to_space(x1, y1)
        ax, bx, ay, by = min(ax, bx), max(ax, bx), min(ay, by), max(ay, by)
        out = []
        for g in self._heads:
            gy = g.y + self._staff_dy(g.staff)
            if g.x + HEAD_W >= ax and g.x <= bx and gy + 0.5 >= ay and gy - 0.5 <= by:
                out += [p.note for key in g.keys if (p := self._by_key.get(key)) and p.segment.clone_of is None]
        return out

    def _track_of(self, p: PlacedNote) -> Track:
        return self._track_of_segment(p.segment) or self.tracks[0]

    # --- gesti -----------------------------------------------------------------

    def set_mode_cursor(self, mode: str) -> None:
        """Puntatore come in griglia: quattro frecce in Move, barra in Split, X in Delete."""
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
        self.area.set_cursor(cursor)

    def on_drag_begin(self, gesture: Gtk.GestureDrag, x: float, y: float) -> None:
        self.area.grab_focus()
        self._op = None
        if y <= RULER_HEIGHT * self.space:  # righello: punto di play
            self.seek_at(x, y)
            return
        shift = bool(gesture.get_current_event_state() & Gdk.ModifierType.SHIFT_MASK)
        hit, mode = self.head_at(x, y), self.get_mode()
        now, (t0, px, py) = GLib.get_monotonic_time(), self._last_press
        self._last_press = (now, x, y)
        double_ms = self.area.get_settings().get_property("gtk-double-click-time")
        if not shift and now - t0 <= double_ms * 1000 and abs(x - px) < 5 and abs(y - py) < 5:
            self._last_press = (0, 0.0, 0.0)
            if hit is not None and mode not in ("split", "delete"):
                self._open_note(hit)
                return
        self._op = {"kind": "pending", "x0": x, "y0": y, "hit": hit, "shift": shift}
        if hit is not None and mode not in ("split", "delete"):
            self._audition(hit.midi, hit.note.velocity, self._track_of(hit))

    def on_drag_update(self, _g, dx: float, dy: float) -> None:
        op = self._op
        if op is None:
            return
        if op["kind"] == "pending":
            if abs(dx) < DRAG_PX and abs(dy) < DRAG_PX:
                return
            hit = op["hit"]
            if self.get_mode() in ("select", "move") and not op["shift"] and hit is not None:
                if hit.note not in self.selection:  # la nota trascinata diventa (e resta) la selezione
                    self.selection = [hit.note]
                notes = self.selection
                op.update(kind="move", notes=[self._by_note[id(n)] for n in notes if id(n) in self._by_note],
                          dpos=Fraction(0), dmidi=0, targets={}, ok=True)
            else:
                op["kind"] = "band"
        op["x1"], op["y1"] = op["x0"] + dx, op["y0"] + dy
        if op["kind"] == "move":
            hit = op["hit"]
            a, b = self._at(op["x0"], op["y0"]), self._at(op["x1"], op["y1"])
            if a and b:
                op["dpos"] = self.snapped(hit.start + (b[1] - a[1])) - hit.start
            steps = -int(round(dy / (self.space / 2)))  # passi del rigo (riga/spazio) sotto il puntatore
            op["dmidi"] = self._midi_at_step(hit, steps, op["dpos"]) - hit.midi
            # 2026-11-20: ogni nota sale o scende di righe/spazi nella tonalità (do-re-mi → re-mi-fa);
            # prima tutte prendevano i semitoni della nota trascinata (do-re-mi → re-mi-fa♯)
            op["targets"] = {id(p.note): self._midi_at_step(p, steps, op["dpos"]) for p in op["notes"]}
            # 2026-11-20: _free controlla segmento e note della stessa altezza (come le frecce)
            op["ok"] = self._free(op["notes"], op["dpos"], op["dmidi"], op["targets"])
            self._audition(hit.midi + op["dmidi"], hit.note.velocity, self._track_of(hit))
        self.area.queue_draw()

    def on_drag_end(self, _g, _dx: float, _dy: float) -> None:
        self._audition(None)
        op, self._op = self._op, None
        if op is None:
            return
        if op["kind"] == "pending":
            self._click(op)
        elif op["kind"] == "band":
            inside = self._heads_in(op["x0"], op["y0"], op["x1"], op["y1"])
            self.selection = (self.selection + [n for n in inside if n not in self.selection]) if op["shift"] else inside
        elif op["kind"] == "move":
            if not op["ok"]:
                self.on_message("The notes would leave their segment or overlap a note of the same pitch: move canceled.")
            elif op["dpos"] or op["dmidi"]:
                self.doc.execute(EditNotes([(self._group(p.segment), p.note,
                                             self._moved(p, op["dpos"], op["targets"].get(id(p.note), p.midi)))
                                            for p in op["notes"]]))
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
        if mode == "split" and hit is not None:  # a metà, agganciata allo Snap
            cut = self.snapped(hit.start + hit.duration / 2)
            self._split(hit, cut)
            return
        if mode == "delete" and hit is not None:
            self._delete(self.selection if hit.note in self.selection else [hit.note])
            return
        if mode == "create" and hit is None:
            self._create(op["x0"], op["y0"])
            return
        self.selection = [hit.note] if hit is not None else []
        if hit is not None:  # su una nota: Paste e punto di play al suo attacco
            self._clicked = (self._track_of(hit), hit.start)
            self.keyboard.queue_draw()  # la tastiera segue il rigo attivo
            self.transport.seek(self.doc.song.tempos.seconds(max(hit.start, Fraction(0))))
            self.area.queue_draw()
        else:
            self.seek_at(op["x0"], op["y0"])

    def _staff_step_at(self, k: int, y: float) -> int:
        """Riga/spazio del rigo `k` sotto il punto (0 = prima riga in basso)."""
        _sx, sy = self._to_space(0, y)
        top = self.layout.systems[0].staff_tops[k] + self._staff_dy(k)
        return int(round((top + 4 - sy) * 2))

    def _create(self, x: float, y: float) -> None:
        """Create: nota del valore scelto nella palette, sulla riga/spazio cliccato (nota della
        tonalità), nel punto agganciato allo Snap (o al valore scelto, se è più piccolo; per
        le terzine alla loro griglia); poi il punto di play va alla fine della
        nota (si scrive una nota dopo l'altra). La nota creata non viene selezionata. Solo dentro un segmento e senza sovrapporsi
        a una nota della stessa altezza."""
        found = self._at(x, y)
        if found is None:
            return
        track, pos = found
        song = self.doc.song
        k = self.tracks.index(track)
        start = self._insert_start(pos)
        key = song.keys.at(song.meters.bar_at(start))
        diatonic = track.clef.bottom_line + self._staff_step_at(k, y)
        letter = STEPS[diatonic % 7]
        midi = Pitch(Spelling(letter, _key_alters(key.fifths)[letter]), diatonic // 7).midi
        self._insert(track, start, midi)

    def _insert_start(self, pos: Fraction) -> Fraction:
        """Punto d'inserimento: agganciato al più piccolo fra Snap e valore scelto (semicrome
        sulle semicrome); per le terzine alla griglia della terzina stessa."""
        length = self.get_length()
        step = length if length.denominator % 3 == 0 else min(self.unit, length)
        return max(self.snapped(pos, step=step), Fraction(0))

    def _insert(self, track: Track, start: Fraction, midi: int) -> None:
        """Inserisce una nota del valore scelto; la si sente; il punto di play va alla sua fine."""
        song = self.doc.song
        length = self.get_length()
        seg = self.segment_at(track, start)
        clash = any(q.midi == midi and q.start < start + length and start < q.end for q in song.placed_notes(track))
        if seg is None or not self._fits(seg, start, start + length, midi) or clash:
            self.on_message("The note doesn't fit here (outside a segment or over another note): not created.")
            self._flash_fail([(start, start + length, midi)])
            return
        note = Note(self._note_start(seg, start), length, midi - seg.transpose, velocity=track.velocity)
        self.doc.execute(AddNotes([(self._group(seg), note)]))
        self.selection = []  # niente selezione: cambiare valore vale per la nota successiva
        self._clicked = (track, start + length)
        self._audition(midi, track.velocity, track)
        GLib.timeout_add(250, lambda: self._audition(None) or False)
        self.transport.seek(song.tempos.seconds(start + length))  # avanza: la prossima nota va dopo
        self.area.queue_draw()

    def seek_at(self, x: float, y: float) -> None:
        """Sposta il punto di play nel punto (agganciato allo Snap)."""
        found = self._at(x, y)
        if found is not None:
            pos = max(self.snapped(found[1]), Fraction(0))
            if y > RULER_HEIGHT * self.space:  # sul rigo: anche punto di Paste
                self._clicked = (found[0], pos)
                self.keyboard.queue_draw()  # la tastiera segue il rigo attivo
            self.transport.seek(self.doc.song.tempos.seconds(pos))
            self.area.queue_draw()

    def on_right_click(self, _g, _n: int, x: float, y: float) -> None:
        """Tasto destro: sempre gli strumenti e, con note selezionate, sotto i comandi Notes."""
        if self.menu is None or y <= RULER_HEIGHT * self.space:
            return
        hit = self.head_at(x, y)
        if hit is not None and hit.note not in self.selection:
            self.selection = [hit.note]
            self.area.queue_draw()
        model = self.menu
        if self.selection and self.notes_menu is not None:
            model = Gio.Menu()
            model.append_section(None, self.menu)
            model.append_section(None, self.notes_menu)
        popup_menu(model, self, x, y)  # l'area riempie la scheda in alto: stesse coordinate

    def on_doc_changed(self, _doc: Document) -> None:
        """Modifica del documento. 2026-11-20: impaginare costa (0,4–1,6 s su long.mid), quindi
        niente per Mute/Solo e Save (il contenuto non cambia: doc.version uguale) e, se la
        scheda non è in vista, solo quando torna visibile (segnale "map")."""
        if getattr(self, "_layout_version", None) == self.doc.version or self._tracks_gone():
            return  # contenuto uguale, o traccia cancellata (la finestra chiude la scheda)
        if not self.get_mapped():
            self._stale = True
            return
        self.relayout()

    def _on_map(self, *_args) -> None:
        """Scheda di nuovo in vista: si impagina se nel frattempo il brano è cambiato."""
        if getattr(self, "_stale", False):
            self.relayout()

    def on_scroll(self, ctrl: Gtk.EventControllerScroll, dx: float, dy: float) -> bool:
        state = ctrl.get_current_event_state()
        if state & PRIMARY_MASK:
            self.zoom_step(1 if dy < 0 else -1)
        elif state & Gdk.ModifierType.SHIFT_MASK:
            self.vadj.set_value(self.vadj.get_value() + (dy or dx) * SCROLL_STEP)
        else:
            self.hadj.set_value(self.hadj.get_value() + (dy + dx) * SCROLL_STEP)
        return True

    def staff_offsets(self) -> list[float] | None:
        """Spostamento verticale (spazi) di ogni rigo per distribuirli "space-evenly"
        sotto il righello. None se lo spartito non ci sta: allora si scorre."""
        if not self.layout or len(self.layout.systems) != 1:
            return None
        boxes = self.layout.systems[0].staff_boxes
        avail = self.area.get_height() / self.space - RULER_HEIGHT
        total = sum(b - a for a, b in boxes)
        gap = (avail - total) / (len(boxes) + 1)
        if gap < 0:
            return None
        offsets, y = [], RULER_HEIGHT + gap
        for a, b in boxes:
            offsets.append(y - a)
            y += (b - a) + gap
        return offsets

    def on_tick(self) -> bool:
        if bool(self.selection) != self._had_selection:  # menu Notes: attivo solo con note selezionate
            self._had_selection = bool(self.selection)
            self.on_selection()
        pos = self.transport.position
        if pos != self._last_pos:
            self._last_pos = pos
            self.area.queue_draw()
            if self.transport.state.value == "playing":
                self._follow()
        return GLib.SOURCE_CONTINUE

    def shutdown(self) -> None:
        """Da chiamare quando la scheda viene chiusa."""
        self._audition(None)
        GLib.source_remove(self._timer)
        self.doc.unsubscribe(self.on_doc_changed)

    # --- cursore -------------------------------------------------------------

    def _playhead(self) -> tuple[float, float, float] | None:
        """Cursore in coordinate del contenuto (px): x, y alto, y basso."""
        if not self.layout:
            return None
        pos: Fraction = self.doc.song.tempos.position(self.transport.position)
        found = self.layout.locate(pos)
        if not found:
            return None
        sys, x = found
        s = self.space
        offs = self.staff_offsets() or [0.0] * len(sys.staff_tops)
        return MARGIN_X + x * s, (sys.top - 2 + offs[0]) * s, (sys.bottom + 2 + offs[-1]) * s

    def _initial_scroll(self) -> bool:
        """All'apertura: aspetta che lo scorrimento sia configurato, poi va al punto di play."""
        if self.area.get_width() <= 1:
            return GLib.SOURCE_CONTINUE
        self.update_adjustments()
        self.scroll_to_playhead()
        return GLib.SOURCE_REMOVE

    def scroll_to_playhead(self) -> None:
        """Porta in vista la misura del punto di play (se è dentro le misure mostrate)."""
        ph = self._playhead()
        x = ph[0] if ph else 0.0
        page = self.hadj.get_page_size()
        self.hadj.set_value(min(max(x - page * 0.15, 0), max(self.hadj.get_upper() - page, 0)))

    def _follow(self) -> None:
        """Durante il play tiene il cursore nella parte visibile (scorre in orizzontale)."""
        ph = self._playhead()
        if not ph:
            return
        follow_center(self.hadj, ph[0])

    # --- disegno -------------------------------------------------------------

    def _draw_op(self, cr) -> None:
        """Anteprima del gesto: rettangolo di selezione o note in spostamento (teste fantasma,
        rosse se non è possibile)."""
        op = self._op
        if op is None or "x1" not in op:
            return
        if op["kind"] == "band":
            x0, x1 = sorted((op["x0"], op["x1"]))
            y0, y1 = sorted((op["y0"], op["y1"]))
            cr.rectangle(x0 + 0.5, y0 + 0.5, x1 - x0, y1 - y0)
            cr.set_source_rgba(*SELECTED, 0.15)
            cr.fill_preserve()
            cr.set_source_rgba(*SELECTED, 0.9)
            cr.set_line_width(1)
            cr.stroke()
        elif op["kind"] == "move" and self.layout:
            s = self.space
            moving = {(p.segment.id, p.note.id) for p in op["notes"]}
            rgb = SELECTED if op["ok"] else (0.95, 0.2, 0.2)
            for g in self._heads:
                if not any(k in moving for k in g.keys):
                    continue
                p = next(self._by_key[k] for k in g.keys if k in moving)
                found = self.layout.locate(p.start + op["dpos"])
                gx = found[1] if found else g.x
                dmidi = op["targets"].get(id(p.note), p.midi) - p.midi
                gy = g.y + self._staff_dy(g.staff) + self._step_shift(p, dmidi, op["dpos"]) / 2
                cr.arc(MARGIN_X + (gx + HEAD_W / 2) * s - self.hadj.get_value(), gy * s - self.vadj.get_value(),
                       s * 0.55, 0, 6.2832)
                cr.set_source_rgba(*rgb, 0.55)
                cr.fill()

    def _midi_at_step(self, p: PlacedNote, steps: int, dpos: Fraction) -> int:
        """Altezza della nota portata di `steps` righe/spazi del rigo: la nota della tonalità
        su quella riga o spazio (alterazioni dell'armatura). Zero passi: resta com'è."""
        if steps == 0:
            return p.midi
        song = self.doc.song
        diatonic = song.spell(p).diatonic + steps
        key = song.keys.at(song.meters.bar_at(max(p.start + dpos, Fraction(0))))
        letter = STEPS[diatonic % 7]
        midi = Pitch(Spelling(letter, _key_alters(key.fifths)[letter]), diatonic // 7).midi
        return min(max(midi, 0), 127)

    def _step_shift(self, p: PlacedNote, dmidi: int, dpos: Fraction) -> float:
        """Di quanti passi del rigo (mezzi spazi, positivo = verso il basso) si sposta la testa
        cambiando l'altezza di `dmidi` semitoni: con la grafia che avrà davvero (tonalità
        nella nuova posizione), così la testa fantasma sta dove finirà la nota."""
        song, clef = self.doc.song, self._track_of(p).clef
        old = clef.staff_step(song.spell(p))
        key = song.keys.at(song.meters.bar_at(max(p.start + dpos, Fraction(0))))
        new = clef.staff_step(song.speller.spell(p.midi + dmidi, key)) if 0 <= p.midi + dmidi <= 127 else old
        return old - new

    def _draw_rulers(self, cr, x_off: float, width: int) -> None:
        """Righello misure/movimenti, fisso in alto, dal bordo sinistro, come in Griglia e Tracce."""
        s, song = self.space, self.doc.song
        for sys in self.layout.systems:
            if not sys.bars:
                continue

            def x_of(pos, sys=sys):
                x = sys.x_at(pos)
                return None if x is None else MARGIN_X + x * s - x_off

            # solo le misure visibili
            first, last = sys.bars_between((x_off - MARGIN_X) / s, (x_off + width - MARGIN_X) / s)
            ruler.draw_ruler(
                cr, 0, 0, width, RULER_HEIGHT * s, song,
                first, last, x_of, dark=False, font_size=1.15 * s,
                bar_x=[(b, MARGIN_X + x * s - x_off) for b, x in sys.bars if first <= b < last],
            )

    def draw(self, _area, cr, w: int, h: int) -> None:
        cr.set_source_rgb(1, 1, 1)
        cr.paint()
        if not self.layout:
            return
        hx, vy, s = self.hadj.get_value(), self.vadj.get_value(), self.space
        cr.save()
        cr.translate(MARGIN_X - hx, -vy)
        # solo la parte visibile (in spazi del rigo), righi distribuiti in verticale
        selected = {(p.segment.id, p.note.id) for n in self.selection if (p := self._by_note.get(id(n)))}
        draw_score(cr, self.layout, s, x_range=((hx - MARGIN_X) / s, (hx + w - MARGIN_X) / s),
                   staff_dy=self.staff_offsets(), selected=selected)
        cr.restore()
        self._draw_op(cr)
        ph = self._playhead()
        if ph:
            x, y0, y1 = ph
            cr.set_source_rgba(*PLAYHEAD, 0.8)
            cr.rectangle(x - hx - 1, y0 - vy, 2, y1 - y0)
            cr.fill()
        self._draw_rulers(cr, hx, w)
