"""View > Mixer…: il mixer del brano, finestra non modale, una sola (2026-11-24, bozza approvata dall'utente).

Una colonna per traccia (non la traccia accordi): numero del canale, fader del volume (CC7) con una colonna
di LED e la tacca del 100, valore, Mute e Solo (gli stessi della scheda Tracks), modalità, X, nome in
basso con il colore della traccia (2026-11-24, aspetto approvato dall'utente). Il cursore si muove sempre
(2026-11-24, correzione dell'utente: prima solo con Lock).

2026-11-25, decisioni dell'utente: un mixer solo, con le modalità delle DAW (Reaper, Cubase, Logic) per colonna,
al posto di Lock + Apply e della finestra "Volume at bar X" (due interfacce ambigue, tolte):
- **Read** (R): il cursore segue il CC7 del brano nel punto di play; mosso, il volume si sente subito,
  finché il brano non manda un suo CC7 su quella traccia (Transport.try_volume). Nel brano non si scrive niente.
- **Hold** (H, l'ex Lock): tiene fisso il valore del cursore e i CC7 del brano di quella traccia si ignorano
  (Transport.set_volume_lock). Nel brano non si scrive niente.
- **Write** (W): muovendo il cursore si scrive nel brano il CC7 **all'inizio della misura dove sta il play**
  (anche da fermi; durante il play, ogni misura attraversata trascinando prende il valore di quel momento);
  al rilascio, un passo di Ctrl+Z (core.mixer.write_volumes). Durante il trascinamento si sente il cursore.
  Dove la traccia non ha segmenti nasce un segmento "Volume" con solo il CC7 (2026-11-25, scelta dell'utente).
  Premendo W, il valore del cursore si scrive subito se è diverso dal volume del brano in quel punto (provato in
  Read o tenuto in Hold; 2026-11-25, richiesta dell'utente).
- **X**: c'è solo se la traccia ha un CC7 proprio all'inizio della misura dove sta il play; lo toglie (con il
  segmento "Volume", se resta vuoto).
- **LED colorati** (2026-11-25, scelta dell'utente): sulle tracce con un CC7 all'inizio della misura del play
  (le stesse con la X), grigi sulle altre in ogni modalità; la modalità si legge dai pulsanti R H W.
- **Play/Pause** e la barra spaziatrice (2026-11-25, segnalazione dell'utente: col mixer in primo piano i controlli
  del play non funzionavano); accanto, la misura dove sta il play (dove scrive Write e agisce la X).
- Close (o la chiusura della finestra): le modalità Hold e le prove in Read finiscono, torna il volume del brano.
I CC7 altrove (non sul primo movimento) si cambiano nell'Event Editor; i fade vanno in CC11 (dev/TODO.md).
"""

from __future__ import annotations

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import GLib, Gtk  # noqa: E402

from ..core.chord_track import CHORDS  # noqa: E402
from ..core.mixer import DEFAULT_VOLUME, bar_volume_events, delete_volume_event, volume_at, write_volumes  # noqa: E402
from ..output.transport import State  # noqa: E402
from .icons import themed  # noqa: E402

LEDS = 24  # LED per colonna
FADER_H = 160  # altezza del fader (px)
LED_W, RAIL_W, CAP_W, CAP_H, GAP = 8, 4, 22, 10, 7  # px: colonna di LED, guida, cappuccio, spazio fra LED e guida
STRIP_W = 66  # larghezza di una colonna
TICK_MS = 100  # aggiornamento di LED e Mute/Solo
MARK = DEFAULT_VOLUME  # tacca del 100 (volume di default del GM), richiesta dell'utente


class LedFader(Gtk.DrawingArea):
    """Fader verticale (0–127), si trascina sempre: a sinistra una colonna stretta di LED, a destra la
    guida col cappuccio; una tacca orizzontale segna il 100 (default del GM). Con `colored` i LED sono
    colorati (verde, poi arancio, poi rosso in alto); senza, sono grigi.
    2026-11-25, scelta dell'utente: colorati se la traccia ha un CC7 all'inizio della misura del play
    (prima secondo la modalità, che si legge già dai pulsanti R H W).
    2026-11-24: nuovo aspetto approvato dall'utente (prima solo una colonna larga di LED)."""

    def __init__(self, on_change, on_release=None) -> None:
        super().__init__(content_width=LED_W + GAP + CAP_W, content_height=FADER_H, tooltip_text="Volume (CC7)")
        self.value, self.colored, self.on_change = 100, False, on_change
        self.on_release = on_release  # fine del trascinamento (Write: lì si scrive nel brano)
        self.dragging = False  # durante il trascinamento il valore non si aggiorna dal brano
        self.set_draw_func(self._draw)
        drag = Gtk.GestureDrag()
        drag.connect("drag-begin", lambda _g, _x, y: self._begin(y))
        drag.connect("drag-update", lambda g, _dx, dy: self._set_from(g.get_start_point()[2] + dy))
        drag.connect("drag-end", lambda *_: self._end())
        self.add_controller(drag)

    def _begin(self, y: float) -> None:
        self.dragging = True
        self._set_from(y)

    def _end(self) -> None:
        self.dragging = False
        if self.on_release is not None:
            self.on_release(self.value)

    @staticmethod
    def _y_of(value: float, h: int) -> float:
        """Altezza (px dall'alto) del valore: 127 in cima, 0 in fondo, metà cappuccio di margine."""
        return CAP_H / 2 + (h - CAP_H) * (1 - value / 127)

    def _set_from(self, y: float) -> None:
        """Valore dal punto verticale del mouse."""
        h = self.get_height() or FADER_H
        value = max(0, min(127, round((1 - (y - CAP_H / 2) / max(h - CAP_H, 1)) * 127)))
        if value != self.value:
            self.value = value
            self.queue_draw()
            self.on_change(value)

    def show(self, value: int, colored: bool) -> None:
        """Valore e colore dei LED da mostrare (senza chiamare on_change)."""
        if (value, colored) != (self.value, self.colored):
            self.value, self.colored = value, colored
            self.queue_draw()

    def _draw(self, _area, cr, w: int, h: int) -> None:
        fg = self.get_color()
        top, bottom = self._y_of(127, h), self._y_of(0, h)
        led_h = (bottom - top - (LEDS - 1)) / LEDS  # LED separati da 1 px
        lit = round(self.value / 127 * LEDS)
        for i in range(LEDS):  # dal basso verso l'alto
            y = bottom - (i + 1) * led_h - i
            if i >= lit:
                cr.set_source_rgba(fg.red, fg.green, fg.blue, 0.12)  # spento
            elif not self.colored:
                cr.set_source_rgba(fg.red, fg.green, fg.blue, 0.45)  # segue il brano: grigio
            elif i >= LEDS - 3:
                cr.set_source_rgb(0.89, 0.29, 0.29)  # rosso
            elif i >= LEDS - 8:
                cr.set_source_rgb(0.94, 0.62, 0.15)  # arancio
            else:
                cr.set_source_rgb(0.11, 0.62, 0.46)  # verde
            cr.rectangle(0, y, LED_W, led_h)
            cr.fill()
        rail_x = LED_W + GAP + (CAP_W - RAIL_W) / 2
        cr.set_source_rgba(fg.red, fg.green, fg.blue, 0.35)  # guida
        cr.rectangle(rail_x, top, RAIL_W, bottom - top)
        cr.fill()
        mark = round(self._y_of(MARK, h)) + 0.5  # tacca del 100, sopra LED e guida
        cr.set_source_rgba(fg.red, fg.green, fg.blue, 0.8)
        cr.set_line_width(1)
        cr.move_to(0, mark)
        cr.line_to(w, mark)
        cr.stroke()
        cap_y = self._y_of(self.value, h) - CAP_H / 2  # cappuccio: rettangolo col colore del tema e una riga in mezzo
        cr.rectangle(LED_W + GAP + 0.5, cap_y + 0.5, CAP_W - 1, CAP_H - 1)
        bg = (0.97, 0.97, 0.97) if fg.red < 0.5 else (0.25, 0.25, 0.28)  # tema chiaro o scuro
        cr.set_source_rgb(*bg)
        cr.fill_preserve()
        cr.set_source_rgba(fg.red, fg.green, fg.blue, 0.7)
        cr.stroke()
        cr.move_to(LED_W + GAP + 3, cap_y + CAP_H / 2)
        cr.line_to(LED_W + GAP + CAP_W - 3, cap_y + CAP_H / 2)
        cr.stroke()


MODES = (("read", "R", "Read: follow the song's volume changes; moving the fader is only heard, not written"),
         ("hold", "H", "Hold: keep this volume and ignore the song's volume changes on this track"),
         ("write", "W", "Write: write the volume at the start of the bar where the play is "
                   "(at once if you changed it, then while you move the fader)"))


class MixerStrip(Gtk.Box):
    """Colonna di una traccia: canale, fader, valore, Mute e Solo, modalità (R H W), X, nome in basso con la
    striscia del colore della traccia (quello del suo primo segmento)."""

    def __init__(self, mixer: "MixerWindow", track) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=6, width_request=STRIP_W,
                         css_classes=["mixer-strip"])
        self.mixer, self.track = mixer, track
        self.mode = "read"
        self.pending: dict[int, int] = {}  # Write: misura → valore, durante un trascinamento (si scrive al rilascio)
        channel = Gtk.Label(label=str(track.channel), css_classes=["mixer-channel"], tooltip_text="MIDI channel")
        self.fader = LedFader(self._fader_moved, self._fader_released)
        self.fader.set_halign(Gtk.Align.CENTER)
        self.value = Gtk.Label(css_classes=["mixer-value"])
        listen = Gtk.Box(spacing=3, halign=Gtk.Align.CENTER)
        self.mute = self._toggle("M", "Mute", "mixer-mute", lambda on: mixer.tracks.set_listen(track, mute=on))
        self.solo = self._toggle("S", "Solo", "mixer-solo", lambda on: mixer.tracks.set_listen(track, solo=on))
        listen.append(self.mute)
        listen.append(self.solo)
        # modalità: tre pulsanti a scelta unica (R H W), come i selettori di automazione delle DAW
        modes = Gtk.Box(spacing=1, halign=Gtk.Align.CENTER)
        self.mode_buttons: dict[str, Gtk.ToggleButton] = {}
        first = None
        for name, letter, tip in MODES:
            button = Gtk.ToggleButton(label=letter, tooltip_text=tip, focus_on_click=False,
                                      css_classes=["mixer-listen", "mixer-mode", f"mixer-{name}"])
            if first is not None:
                button.set_group(first)
            first = first or button
            button.set_active(name == "read")
            button.connect("toggled", self._mode_toggled, name)
            self.mode_buttons[name] = button
            modes.append(button)
        # X: toglie il CC7 all'inizio della misura dove sta il play; nascosta se lì non c'è (lo spazio resta)
        self.delete = Gtk.Button(icon_name=themed("window-close"), focus_on_click=False, halign=Gtk.Align.CENTER,
                                 css_classes=["mixer-delete", "flat"], visible=False,
                                 tooltip_text="Delete the volume change at the start of this bar")
        self.delete.connect("clicked", lambda *_: mixer.delete(self))
        delete_box = Gtk.Box(height_request=24, halign=Gtk.Align.CENTER)
        delete_box.append(self.delete)
        # nome in basso: senza il prefisso "Style · " (resta nel tooltip), sotto la striscia del colore della traccia
        short = track.name.removeprefix("Style · ")
        name = Gtk.Label(label=short, ellipsize=3, max_width_chars=8, tooltip_text=track.name, css_classes=["mixer-name"])
        stripe = Gtk.DrawingArea(content_height=3, hexpand=True)
        stripe.set_draw_func(self._draw_stripe)
        name_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, css_classes=["mixer-name-box"])
        name_box.append(stripe)
        name_box.append(name)
        for widget in (channel, self.fader, self.value, listen, modes, delete_box, name_box):
            self.append(widget)

    def _mode_toggled(self, button: Gtk.ToggleButton, name: str) -> None:
        """Cambio di modalità (arriva per il pulsante acceso e per quello spento): Hold acceso tiene il valore del
        cursore; uscendo da Hold torna il volume del brano. Write acceso scrive subito il valore del cursore se è
        diverso dal volume del brano nel punto di play (provato in Read o tenuto in Hold)."""
        if not button.get_active():
            return
        transport = self.mixer.transport
        # 2026-11-25, richiesta dell'utente: provato un volume in Read (o tenuto in Hold), premere W lo deve fissare
        # nel brano; prima W scriveva solo muovendo il cursore e il valore provato si perdeva. Solo se cambia: armare
        # W senza aver toccato niente non mette nel brano un CC7 doppione (o un segmento "Volume" inutile).
        value = self.fader.value
        song = self.mixer.doc.song
        changed = name == "write" and value != volume_at(song, self.track, song.tempos.position(transport.position))
        if name == "hold":
            transport.set_volume_lock(self.track.id, value)
        elif self.mode == "hold":
            transport.set_volume_lock(self.track.id, None)
        self.mode = name
        if changed:  # come un trascinamento in Write: si sente il valore, si scrive, poi torna il volume del brano
            transport.set_volume_lock(self.track.id, value)  # toglie anche la prova di Read
            self.mixer.write(self.track, {self.mixer.play_bar(): value})
            transport.set_volume_lock(self.track.id, None)
        self.sync()

    def set_mode(self, name: str) -> None:
        """Modalità da codice (anche dalle prove): come un click sul pulsante."""
        self.mode_buttons[name].set_active(True)

    def _fader_moved(self, value: int) -> None:
        """Cursore mosso. Read: si sente subito (Transport.try_volume). Hold: sposta il valore tenuto. Write: si sente
        il cursore (i CC7 del brano si ignorano finché si trascina) e si annota per la misura dove sta il play."""
        transport = self.mixer.transport
        if self.mode == "read":
            transport.try_volume(self.track.id, value)
            return
        transport.set_volume_lock(self.track.id, value)
        if self.mode == "write":
            self.pending[self.mixer.play_bar()] = value

    def _fader_released(self, _value: int) -> None:
        """Fine del trascinamento. Write: i valori annotati vanno nel brano (un passo di Ctrl+Z), poi torna il volume
        del brano (che ora è quello scritto)."""
        if self.mode != "write":
            return
        pending, self.pending = self.pending, {}
        if pending:
            self.mixer.write(self.track, pending)
        self.mixer.transport.set_volume_lock(self.track.id, None)

    def _draw_stripe(self, _area, cr, w: int, h: int) -> None:
        """Striscia del colore della traccia (quello del suo primo segmento; grigio se non ne ha)."""
        from .theme import group_color

        song = self.mixer.doc.song
        color = group_color(song, self.track.segments[0].group_id) if self.track.segments else (0.6, 0.6, 0.6)
        cr.set_source_rgb(*color)
        cr.rectangle(0, 0, w, h)
        cr.fill()

    @staticmethod
    def _toggle(letter: str, tip: str, css: str, set_) -> Gtk.ToggleButton:
        b = Gtk.ToggleButton(label=letter, tooltip_text=tip, css_classes=["mixer-listen", css], focus_on_click=False)
        b._set = set_  # azione del click (non chiamata da sync)
        b._handler = b.connect("toggled", lambda btn: btn._set(btn.get_active()))
        return b

    def sync(self) -> None:
        """Fader, valore, X, Mute e Solo allineati al brano e alla modalità."""
        transport = self.mixer.transport
        held = self.track.id in transport.volume_locks
        if self.fader.dragging:
            value = self.fader.value  # mentre si trascina comanda il mouse
        elif held:
            value = transport.volume_locks[self.track.id]
        elif self.track.id in transport.volume_tries:  # mosso in Read: finché il brano non manda un CC7
            value = transport.volume_tries[self.track.id]
        else:
            song = self.mixer.doc.song
            value = volume_at(song, self.track, song.tempos.position(transport.position))
        # 2026-11-25, scelta dell'utente: LED colorati se la traccia ha un CC7 all'inizio della misura del play
        # (si vede subito quali tracce cambiano volume lì), grigi altrimenti; prima colorati in Hold e Write
        here = self.track.id in self.mixer.here
        self.fader.show(value, here)
        self.value.set_label(str(value))
        self.delete.set_visible(here)
        self.sync_listen()

    def sync_listen(self) -> None:
        """Mute e Solo come nella scheda Tracks (cambiati anche altrove), senza rieseguire l'azione."""
        for button, on in ((self.mute, self.track.mute), (self.solo, self.track.solo)):
            if button.get_active() != on:
                with button.handler_block(button._handler):
                    button.set_active(on)


class MixerWindow(Gtk.Window):
    """La finestra del mixer (una sola per brano). `play_pause`: lo stesso Play/Pause del menu Player."""

    def __init__(self, parent: Gtk.Window, doc, transport, tracks, play_pause) -> None:
        super().__init__(title="Mixer", transient_for=parent, resizable=True)
        self.doc, self.transport, self.tracks = doc, transport, tracks
        self.here: dict[str, tuple] = {}  # id traccia → (gruppo, CC7) all'inizio della misura dove sta il play
        self._here_key = None  # (misura, versione del documento) di `here`
        self.strips_box = Gtk.Box(margin_start=6, margin_end=6, css_classes=["mixer-strips"])
        scroller = Gtk.ScrolledWindow(vscrollbar_policy=Gtk.PolicyType.NEVER, propagate_natural_width=True,
                                      propagate_natural_height=True)
        scroller.set_child(self.strips_box)
        self.play = Gtk.Button(icon_name=themed("media-playback-start"), tooltip_text="Play/Pause (Space)",
                               focus_on_click=False)
        self.play.connect("clicked", lambda *_: play_pause())
        self.bar_label = Gtk.Label(css_classes=["mixer-value"], tooltip_text="Bar of the play position: "
                                   "Write and the X act on its start")
        close = Gtk.Button(label="Close")
        close.connect("clicked", lambda *_: self.close())
        buttons = Gtk.Box(spacing=8, margin_start=10, margin_end=10, margin_top=8, margin_bottom=10)
        buttons.append(self.play)  # a sinistra, dove prima c'era Apply
        buttons.append(self.bar_label)
        buttons.append(Gtk.Box(hexpand=True))
        buttons.append(close)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        box.append(scroller)
        box.append(Gtk.Separator())
        box.append(buttons)
        self.set_child(box)
        keys = Gtk.ShortcutController()
        keys.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)  # prima dei pulsanti (lo spazio li premerebbe)
        keys.add_shortcut(Gtk.Shortcut.new(Gtk.ShortcutTrigger.parse_string("Escape"), Gtk.NamedAction.new("window.close")))
        keys.add_shortcut(Gtk.Shortcut.new(Gtk.ShortcutTrigger.parse_string("space"),
                                           Gtk.CallbackAction.new(lambda *_: play_pause() or True)))
        self.add_controller(keys)
        self.connect("close-request", self._on_close)
        self.strips: list[MixerStrip] = []
        self._rebuild()
        doc.subscribe(self._on_doc)
        self._timer = GLib.timeout_add(TICK_MS, self._tick)

    def play_bar(self) -> int:
        """Misura (da 0) dove sta il play: lì scrive Write e agisce la X."""
        song = self.doc.song
        return song.meters.bar_at(song.tempos.position(self.transport.position))

    def _rebuild(self) -> None:
        """Una colonna per traccia di note, nell'ordine della scheda Tracks (non la traccia accordi)."""
        while (child := self.strips_box.get_first_child()) is not None:
            self.strips_box.remove(child)
        self.strips = [MixerStrip(self, t) for t in self.doc.song.tracks if t.kind != CHORDS]
        for strip in self.strips:
            self.strips_box.append(strip)
        self._here_key = None
        self._tick()

    def _on_doc(self, _doc) -> None:
        """Tracce aggiunte, tolte o riordinate: si rifanno le colonne (gli Hold delle tracce sparite si tolgono)."""
        ids = [t.id for t in self.doc.song.tracks if t.kind != CHORDS]
        if ids != [s.track.id for s in self.strips]:
            for track_id in list(self.transport.volume_locks):
                if track_id not in ids:
                    self.transport.set_volume_lock(track_id, None)
            self._rebuild()
        self._here_key = None  # i CC7 della misura vanno riletti

    def _tick(self) -> bool:
        """Segue il punto di play: misura, CC7 all'inizio della misura (per la X), cursori, Mute e Solo, Play/Pause."""
        bar = self.play_bar()
        key = (bar, self.doc.version)
        if key != self._here_key:
            self._here_key = key
            self.here = {t.id: (g, e) for t, g, e in bar_volume_events(self.doc.song, bar)}
            self.bar_label.set_label(f"Bar {bar + 1}")
        for strip in self.strips:
            strip.sync()
        playing = self.transport.state is State.PLAYING
        self.play.set_icon_name(themed("media-playback-pause" if playing else "media-playback-start"))
        return GLib.SOURCE_CONTINUE

    def write(self, track, pending: dict[int, int]) -> None:
        """Write: i CC7 all'inizio delle misure annotate (un passo di Ctrl+Z)."""
        command = write_volumes(self.doc.song, {track.id: pending})
        if command is not None:
            self.doc.execute(command)

    def delete(self, strip: MixerStrip) -> None:
        """X: il CC7 all'inizio della misura dove sta il play esce dal brano (un passo di Ctrl+Z)."""
        found = self.here.get(strip.track.id)
        if found is not None:
            self.doc.execute(delete_volume_event(self.doc.song, *found))

    def _on_close(self, *_a) -> bool:
        """Chiusura (Close, Esc, finestra): gli Hold e le prove in Read finiscono, torna il volume del brano."""
        GLib.source_remove(self._timer)
        self.doc.unsubscribe(self._on_doc)
        for track_id in list(self.transport.volume_locks):
            self.transport.set_volume_lock(track_id, None)
        self.transport.forget_tries()  # anche le prove in Read: torna il volume del brano
        return False
