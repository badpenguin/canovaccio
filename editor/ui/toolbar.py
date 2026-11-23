"""Toolbar a palette. La prima palette è il player."""

from __future__ import annotations

from fractions import Fraction

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import GLib, Gtk  # noqa: E402

from ..engraving.smufl import FONT_FAMILY  # noqa: E402
from ..output.transport import State  # noqa: E402
from ..session import Session  # noqa: E402
from .icons import themed, tool_icon  # noqa: E402


class Toolbar(Gtk.Box):
    """Fila di palette separate da un divisore."""

    def __init__(self) -> None:
        super().__init__(spacing=6, margin_start=6, margin_end=6, margin_top=4, margin_bottom=4)
        self.add_css_class("toolbar")
        self._palettes: list[Gtk.Widget] = []

    def add_palette(self, palette: Gtk.Widget) -> None:
        if self._palettes:
            self.append(Gtk.Separator(orientation=Gtk.Orientation.VERTICAL))  # stile in app.TOOLBAR_CSS
        self._palettes.append(palette)
        self.append(palette)


class PlayerPalette(Gtk.Box):
    """Inizio, play/pausa, stop e, a destra, il riquadro della posizione."""

    def __init__(self, session: Session) -> None:
        super().__init__(spacing=2)
        self.session = session
        self.btn_start = Gtk.Button(icon_name=themed("media-skip-backward"), tooltip_text="Go to Start")
        self.btn_play = Gtk.Button(icon_name=themed("media-playback-start"), tooltip_text="Play/Pause (Space)")
        self.btn_stop = Gtk.Button(icon_name=themed("media-playback-stop"), tooltip_text="Stop")
        self.btn_next = Gtk.Button(icon_name=themed("media-skip-forward"), tooltip_text="Go to Next Segment")
        # 2026-11-20: Panic dopo Stop (deciso dall'utente): note appese e controlli rimasti nel synth
        self.btn_panic = Gtk.Button(icon_name=themed("dialog-warning"),
                                    tooltip_text="Panic: stop, silence all notes and reset volume, pan, "
                                                 "reverb and the other controllers on every channel")
        self.btn_loop = Gtk.Button(icon_name=themed("media-playlist-repeat"),
                                   tooltip_text="Loop play: repeats the bars of the grid or score, "
                                                "or the segments selected in the Tracks tab (again or pause: stop)")
        self.loop_range = lambda: None  # (inizio, fine) in secondi del tratto da ripetere (lo dà la finestra)
        self.looping = False
        self.btn_loop.connect("clicked", lambda *_: self.loop_play())
        self.btn_start.connect("clicked", lambda *_: session.transport.seek(0))
        self.btn_play.connect("clicked", lambda *_: self.play_pause())
        self.btn_stop.connect("clicked", lambda *_: session.transport.stop())
        self.btn_next.connect("clicked", lambda *_: self.next_segment())
        self.btn_panic.connect("clicked", lambda *_: session.transport.reset())
        # Panic resta attivo anche senza brano (note appese dal thru dell'ingresso MIDI)
        for b in (self.btn_start, self.btn_play, self.btn_loop, self.btn_stop, self.btn_panic, self.btn_next):
            b.add_css_class("flat")
            b.set_focus_on_click(False)  # lo Spazio resta al player, non al pulsante
            self.append(b)
        self.position = Gtk.Label(xalign=0)
        self.position.add_css_class("position")  # riquadro, cifre a larghezza fissa
        self.position.set_margin_start(8)
        self.append(self.position)
        self.set_enabled(False)

        session.transport.add_listener(lambda st: GLib.idle_add(self._on_state, st))
        self._timer = GLib.timeout_add(40, self._on_tick)

    def fit_position(self, song) -> None:
        """Larghezza del riquadro = il testo più lungo possibile per questo brano
        (così non cambia mentre il play avanza)."""
        beats = max(e.value.numerator for e in song.meters)
        longest = self._text(song.bar_count, beats, song.duration_seconds, song.duration_seconds)
        width, _h = self.position.create_pango_layout(longest).get_pixel_size()
        self.position.set_size_request(width + 6, -1)  # larghezza del testo misurato

    @staticmethod
    def _text(bar: int, beat: int, secs: float, total: float) -> str:
        return f"Bar {bar}.{beat}   {_fmt(secs)} / {_fmt(total)}"

    def set_enabled(self, on: bool) -> None:
        for b in (self.btn_start, self.btn_play, self.btn_loop, self.btn_stop, self.btn_next):
            b.set_sensitive(on)
        if not on:
            self.position.set_label("")

    def next_segment(self) -> None:
        """Punto di play all'inizio del primo segmento (di qualsiasi traccia) che comincia
        dopo la posizione attuale; se non ce ne sono, resta dov'è."""
        doc = self.session.document
        if doc is None:
            return
        song, t = doc.song, self.session.transport
        here = song.tempos.position(t.position)
        starts = sorted({song.segment_span(seg)[0] for tr in song.tracks for seg in tr.segments})
        later = [s for s in starts if s > here + Fraction(1, 1920)]
        if later:
            t.seek(song.tempos.seconds(later[0]))

    def loop_play(self) -> None:
        """Play in loop: ripete il tratto della scheda in primo piano finché non si preme di
        nuovo questo pulsante, o pausa/stop."""
        t = self.session.transport
        if self.session.document is None:
            return
        if t.state is State.PLAYING and self.looping:
            t.pause()
            return
        self.looping = True
        loop = self.loop_range()
        t.set_loop(loop)
        if t.state is not State.PLAYING:
            t.play()  # parte dall'inizio del tratto se il punto di play è fuori
        elif loop is not None and not loop[0] <= t.position < loop[1]:
            t.seek(loop[0])
        self._loop_icon()

    def update_loop(self) -> None:
        """Cambiando scheda durante il play in loop: il tratto segue la scheda nuova."""
        if self.looping:
            self.session.transport.set_loop(self.loop_range())

    def play_pause(self) -> None:
        """Play / pausa normale (senza loop)."""
        t = self.session.transport
        if self.session.document is None:
            return
        if t.state is State.PLAYING:
            t.pause()
        else:
            self.looping = False
            t.set_loop(None)
            t.play()

    def _loop_icon(self) -> None:
        self.btn_loop.set_icon_name(themed("media-playback-pause" if self.looping else "media-playlist-repeat"))

    def shutdown(self) -> None:
        GLib.source_remove(self._timer)

    def _on_state(self, state: State) -> bool:
        playing = state is State.PLAYING
        if not playing and self.looping:  # pausa o stop: il loop finisce
            self.looping = False
            self.session.transport.set_loop(None)
            self._loop_icon()
        self.btn_play.set_icon_name(themed("media-playback-pause" if playing else "media-playback-start"))
        return False

    def _on_tick(self) -> bool:
        doc = self.session.document
        if doc:
            song = doc.song
            secs = self.session.transport.position
            pos = song.tempos.position(secs)
            bar = song.meters.bar_at(pos)
            beat = int((pos - song.meters.bar_start(bar)) / song.meters.at(bar).beat) + 1
            self.position.set_label(self._text(bar + 1, beat, secs, song.duration_seconds))
        return GLib.SOURCE_CONTINUE


def _fmt(secs: float) -> str:
    m, s = divmod(max(secs, 0.0), 60)
    return f"{int(m)}:{s:04.1f}"


# Strumenti di modifica della scheda Tracce: (nome, etichetta, scorciatoia, attivo).
# Stesse voci, stesse azioni e stesse scorciatoie del menu "Segment".
TOOLS = [
    ("select", "_Select", "F2", True),
    ("create", "_Create", "F3", True),
    ("move", "_Move", "F4", True),
    ("split", "S_plit", "F5", True),
    ("resize", "_Resize", "F6", True),
    ("delete", "_Delete", "F7", True),
]  # "_" = lettera sottolineata nel menu (accesso da tastiera)


def tool_action(name: str) -> str:
    """Azione della finestra per uno strumento. Select è una voce dell'azione a stato
    "win.tool"; gli strumenti non ancora implementati hanno un'azione propria disattivata."""
    enabled = next(e for n, _l, _a, e in TOOLS if n == name)
    return f"win.tool::{name}" if enabled else f"win.tool-{name}"


class ToolsPalette(Gtk.Box):
    """Pulsanti degli strumenti: solo icona, il nome compare al passaggio del mouse.
    Lo stato sta nelle azioni della finestra: il pulsante attivo e quelli disattivati
    seguono il menu "Segment" automaticamente."""

    def __init__(self) -> None:
        super().__init__(spacing=2)
        for name, label, accel, enabled in TOOLS:
            title = label.replace("_", "")
            tip = f"{title} ({accel})" + ("" if enabled else " — not available yet")
            b = Gtk.ToggleButton(icon_name=tool_icon(name), tooltip_text=tip)
            b.add_css_class("flat")
            b.update_property([Gtk.AccessibleProperty.LABEL], [title])  # nome per lettori di schermo
            b.set_focus_on_click(False)
            b.set_detailed_action_name(tool_action(name))
            self.append(b)


# Valori di nota della palette: (tasto, nome, durata in semibrevi, glifi SMuFL del metronomo).
NOTE_VALUES = [
    ("8", "Whole note", Fraction(1), "\uECA2"),
    ("7", "Dotted half note", Fraction(3, 4), "\uECA3\uECB7"),
    ("6", "Half note", Fraction(1, 2), "\uECA3"),
    ("5", "Dotted quarter note", Fraction(3, 8), "\uECA5\uECB7"),
    ("4", "Quarter note", Fraction(1, 4), "\uECA5"),
    ("3", "Eighth note", Fraction(1, 8), "\uECA7"),
    ("2", "Sixteenth note", Fraction(1, 16), "\uECA9"),
    ("1", "Thirty-second note", Fraction(1, 32), "\uECAB"),
]
TRIPLET_GLYPH = "\uE883"  # tuplet3


class _MusicGlyph(Gtk.DrawingArea):
    """Simbolo musicale disegnato con il font Bravura (Cairo, come lo spartito), nel colore
    del testo del tema."""

    def __init__(self, text: str, size: float = 20.0) -> None:
        super().__init__(content_width=30, content_height=30, can_target=False)
        self.text, self.size = text, size
        self.set_draw_func(self._draw)

    def _draw(self, _a, cr, w: int, h: int) -> None:
        fg = self.get_color()
        cr.set_source_rgba(fg.red, fg.green, fg.blue, fg.alpha)
        cr.select_font_face(FONT_FAMILY)
        cr.set_font_size(self.size)
        ext = cr.text_extents(self.text)
        cr.move_to((w - ext.width) / 2 - ext.x_bearing, (h - ext.height) / 2 - ext.y_bearing)
        cr.show_text(self.text)


class _TripletIcon(Gtk.DrawingArea):
    """Icona Terzina: parentesi con il 3 di Bravura al centro (proposta A), nel colore
    del testo del tema."""

    def __init__(self) -> None:
        super().__init__(content_width=30, content_height=30, can_target=False)
        self.set_draw_func(self._draw)

    def _draw(self, _a, cr, w: int, h: int) -> None:
        fg = self.get_color()
        cr.set_source_rgba(fg.red, fg.green, fg.blue, fg.alpha)
        s = min(w, h) / 32  # coordinate della proposta (32×32)
        cr.save()
        cr.translate((w - 32 * s) / 2, (h - 32 * s) / 2)
        cr.scale(s, s)
        cr.set_line_width(1.6)
        for path in (((5, 21), (5, 14), (12, 14)), ((20, 14), (27, 14), (27, 21))):
            cr.move_to(*path[0])
            for point in path[1:]:
                cr.line_to(*point)
            cr.stroke()
        cr.select_font_face(FONT_FAMILY)
        cr.set_font_size(16)
        ext = cr.text_extents(TRIPLET_GLYPH)
        cr.move_to(16 - ext.width / 2 - ext.x_bearing, 19.5)
        cr.show_text(TRIPLET_GLYPH)
        cr.restore()


class NoteValuesPalette(Gtk.Box):
    """Valori di nota (win.note-value) e interruttore Terzina (win.triplet): numero del
    tasto piccolo in alto a sinistra. Il valore scelto cambia la durata delle note
    selezionate ed è quello delle note create in notazione."""

    def __init__(self) -> None:
        super().__init__(spacing=2)
        for key, name, _value, glyph in NOTE_VALUES:
            b = Gtk.ToggleButton(tooltip_text=f"{name} ({key})")
            b.add_css_class("flat")
            b.set_focus_on_click(False)
            b.update_property([Gtk.AccessibleProperty.LABEL], [name])
            overlay = Gtk.Overlay()
            overlay.set_child(_MusicGlyph(glyph))
            number = Gtk.Label(label=key, halign=Gtk.Align.START, valign=Gtk.Align.START, css_classes=["note-key"])
            overlay.add_overlay(number)
            b.set_child(overlay)
            b.set_action_name("win.note-value")
            b.set_action_target_value(GLib.Variant("s", key))
            self.append(b)
        self.append(Gtk.Separator(orientation=Gtk.Orientation.VERTICAL))
        t = Gtk.ToggleButton(tooltip_text="Triplet: values count as 2/3")
        t.add_css_class("flat")
        t.set_focus_on_click(False)
        t.set_child(_TripletIcon())
        t.set_action_name("win.triplet")
        self.append(t)


# Snap a pulsanti: (glifo SMuFL o None per "Off", valore, nome)
SNAP_BUTTONS = [("\uECA2", Fraction(1), "1/1"), ("\uECA3", Fraction(1, 2), "1/2"),
                ("\uECA5", Fraction(1, 4), "1/4"), ("\uECA7", Fraction(1, 8), "1/8"),
                ("\uECA9", Fraction(1, 16), "1/16"), ("\uECAB", Fraction(1, 32), "1/32"), (None, None, "Off")]


def snap_buttons(default: Fraction | None, on_change) -> Gtk.Box:
    """Snap di un editor (barra in basso): "Snap" + pulsanti con i simboli delle note e
    "Off"; uno solo premuto. `on_change(valore)` a ogni scelta."""
    box = Gtk.Box(spacing=2)
    box.append(Gtk.Label(label="Snap", margin_end=4))
    first = None
    for glyph, value, name in SNAP_BUTTONS:
        b = Gtk.ToggleButton(tooltip_text=f"Snap {name}")
        b.add_css_class("flat")
        b.set_focus_on_click(False)
        if glyph:
            b.set_child(_MusicGlyph(glyph, 24.0))  # simbolo grande: riempie il pulsante
        else:
            b.set_label("Off")
        b.update_property([Gtk.AccessibleProperty.LABEL], [f"Snap {name}"])
        if first is None:
            first = b
        else:
            b.set_group(first)
        b.set_active(value == default)
        b.connect("toggled", lambda btn, v=value: btn.get_active() and on_change(v))
        box.append(b)
    return box
