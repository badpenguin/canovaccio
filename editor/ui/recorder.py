"""Registrazione dall'ingresso MIDI (pulsante Rec accanto al player).

Rec acceso: traccia attiva (quella del thru), partenza dall'inizio della misura del punto
di play (mai dentro un segmento della traccia: messaggio), metronomo (Side Stick, canale
10 dell'uscita in uso) solo durante la registrazione, poi il conteggio scelto nella
tendina accanto: 2 misure, 1 misura, o "Attendi nota" (play e registrazione partono alla
prima nota suonata, che cade all'inizio della misura). Fine: Rec spento o Stop, o da sola
all'inizio del segmento successivo della traccia (il play continua). Le note diventano un
segmento nuovo (core.recording.build_take): un passo di Ctrl+Z. Sulla traccia accordi
(riga evidenziata nella scheda Tracce) le note diventano accordi agganciati a 1/8.
"""

from __future__ import annotations

import time
from fractions import Fraction
from typing import Callable

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import GLib, Gtk  # noqa: E402

from ..core.document import AddSegments  # noqa: E402
from ..core.chord_track import CHORDS, chords_from_notes  # noqa: E402
from ..core.recording import build_take, record_window  # noqa: E402
from ..output.performance import (CLICK_ACCENT, CLICK_KEY, CLICK_VELOCITY, METRONOME, NoteOff, NoteOn,  # noqa: E402
                                  build_performance, with_metronome)
from ..output.transport import State  # noqa: E402
from . import preferences  # noqa: E402

def _control(status: int, data: list[int]) -> tuple[str, int, int]:
    """(tipo, numero, valore) di un messaggio di controllo (model.ControlEvent)."""
    from ..core.model import BEND, CC, POLY_PRESSURE, PRESSURE

    if status == 0xB0:
        return CC, data[0], data[1]
    if status == 0xE0:
        return BEND, 0, ((data[1] << 7) | data[0]) - 8192
    if status == 0xD0:
        return PRESSURE, 0, data[0]
    return POLY_PRESSURE, data[0], data[1]


COUNT_IN = [("2 bars", 2), ("1 bar", 1), ("Wait for note", 0)]  # nelle preferenze si salva il numero, non il testo
FAR = 256  # misure di metronomo oltre l'inizio quando non c'è un limite


class Recorder:
    def __init__(self, session, get_track: Callable[[], object], message: Callable[..., None]) -> None:
        self.session, self.get_track, self.message = session, get_track, message
        self.button = Gtk.ToggleButton(icon_name="media-record", tooltip_text="Record from the MIDI input onto the "
                                       "active track (in free space, from the start of the bar)")
        self.button.add_css_class("flat")
        self.button.set_focus_on_click(False)
        self._handler = self.button.connect("toggled", lambda b: self.start() if b.get_active() else self.finish(True))
        self.count = Gtk.DropDown.new_from_strings([label for label, _n in COUNT_IN])
        self.count.set_tooltip_text("Count-in before recording")
        self.count.set_focus_on_click(False)
        saved = preferences.load().get("count_in", 1)
        self.count.set_selected(next((i for i, (_l, n) in enumerate(COUNT_IN) if n == saved), 1))
        self.count.connect("notify::selected", lambda *_: preferences.save(count_in=COUNT_IN[self.count.get_selected()][1]))
        self.state = "idle"  # idle / counting / waiting / recording
        # 2026-11-20: numero della presa; i timer del conteggio (_click, _go) valgono solo per la
        # presa in cui sono nati (Rec spento e riacceso durante il conteggio non fa partire prima)
        self._take = 0
        self.track = None
        self.events: list[tuple[Fraction, int, int | None]] = []
        GLib.timeout_add(30, self._tick)

    # --- avvio ---------------------------------------------------------------

    def _set_button(self, on: bool) -> None:
        with self.button.handler_block(self._handler):
            self.button.set_active(on)

    def start(self) -> None:
        doc = self.session.document
        track = self.get_track()
        if doc is None or track is None:
            self.message("Nothing to record: open a song and choose a track.", error=True)
            self._set_button(False)
            return
        song, transport = doc.song, self.session.transport
        window = record_window(song, track, song.tempos.position(transport.position))
        if window is None:
            self.message("The play position is inside a segment of the track: move it to a free area.",
                         error=True)
            self._set_button(False)
            return
        self.track, (self.start_pos, self.limit) = track, window
        self._take += 1
        self.events = []
        self.controls: list[tuple[Fraction, str, int, int]] = []
        self._last_pos = self.start_pos
        transport.stop()
        end = self.limit if self.limit is not None else self.start_pos + FAR
        transport.load(with_metronome(song, self.start_pos, end))
        transport.seek(song.tempos.seconds(self.start_pos))
        transport.backend.start()  # suoni (Program Change) adesso, così al via si parte subito
        bars = COUNT_IN[self.count.get_selected()][1]
        if bars == 0:
            self.state = "waiting"
            self.message(f"Recording on “{track.name}”: starts at the first note.", error=False)
            return
        self.state = "counting"
        meter = song.meters.at(song.meters.bar_at(self.start_pos))
        beat_s = song.tempos.seconds(self.start_pos + meter.beat) - song.tempos.seconds(self.start_pos)
        clicks = [(k * beat_s, k % meter.numerator == 0) for k in range(bars * meter.numerator)]
        t0 = time.monotonic()
        self.message(f"Recording on “{track.name}”…", error=False)
        for at, accent in clicks:
            GLib.timeout_add(max(int((t0 + at - time.monotonic()) * 1000), 0), self._click, accent, self._take)
        self._go_at, self._early = t0 + len(clicks) * beat_s, beat_s / 2  # attacchi in anticipo: sul battere
        GLib.timeout_add(max(int((self._go_at - time.monotonic()) * 1000), 0), self._go, self._take)

    def _click(self, accent: bool, take: int) -> bool:
        """Colpo del metronomo durante il conteggio (solo per la presa `take` ancora in corso)."""
        if self.state == "counting" and take == self._take:
            live = self.session.transport.live
            live(NoteOn(METRONOME, CLICK_KEY, CLICK_ACCENT if accent else CLICK_VELOCITY))
            GLib.timeout_add(60, lambda: live(NoteOff(METRONOME, CLICK_KEY)) or False)
        return False

    def _go(self, take: int) -> bool:
        """Fine del conteggio: parte il play e la registrazione (solo per la presa `take`)."""
        if self.state == "counting" and take == self._take:
            self.state = "recording"
            self.session.transport.play(resend=False)
        return False

    # --- cattura ---------------------------------------------------------------

    def capture(self, message: list[int], when: float) -> None:
        """Messaggio dall'ingresso (nel main loop), `when` = istante d'arrivo."""
        if not message or self.state not in ("waiting", "recording", "counting"):
            return
        status, data = message[0] & 0xF0, message[1:] + [0, 0]
        is_on = status == 0x90 and data[1] > 0
        if status in (0xA0, 0xB0, 0xD0, 0xE0):  # controller, pitch bend, aftertouch (non il Bank Select)
            if self.state == "recording" and not (status == 0xB0 and data[0] in (0, 32)):
                pos = self.session.document.song.tempos.position(
                    max(self.session.transport.position - (time.monotonic() - when), 0.0))
                self.controls.append((Fraction(pos).limit_denominator(1920 * 4), *_control(status, data)))
            return
        if status not in (0x80, 0x90):
            return
        transport, song = self.session.transport, self.session.document.song
        if self.state == "counting":  # prima della partenza: vale solo nell'ultimo mezzo movimento
            if not (is_on and when >= self._go_at - self._early):
                return
            pos = self.start_pos  # suonata sul battere (un attimo prima che il timer faccia partire il play)
        elif self.state == "waiting":
            if not is_on:
                return
            self.state = "recording"
            transport.play(resend=False)
            pos = self.start_pos
        else:
            pos = song.tempos.position(max(transport.position - (time.monotonic() - when), 0.0))
        self.events.append((Fraction(pos).limit_denominator(1920 * 4), data[0], data[1] if is_on else None))

    # --- fine --------------------------------------------------------------------

    def _tick(self) -> bool:
        """Ogni 30 ms: chiude la registrazione a Stop o al segmento successivo."""
        if self.state in ("counting", "waiting"):
            # 2026-11-20: durante il conteggio (o l'attesa della prima nota) Stop, Go to Start o un
            # click sul righello spostano il punto di play: la registrazione si annulla. Prima
            # il play ripartiva dalla misura 1 con Rec acceso e la presa finiva ammucchiata.
            transport, song = self.session.transport, self.session.document.song
            if transport.state is State.PLAYING:  # Play premuto durante il conteggio: si parte subito
                self.state = "recording"
            elif abs(transport.position - song.tempos.seconds(self.start_pos)) > 0.001:
                self.finish(False)
                self.message("Recording cancelled.", error=False)
                return GLib.SOURCE_CONTINUE
        if self.state == "recording":
            transport, song = self.session.transport, self.session.document.song
            if transport.state is State.PLAYING:
                self._last_pos = song.tempos.position(transport.position)  # per lo Stop (che azzera la posizione)
            if transport.state is not State.PLAYING:
                self.finish(False)
            elif self.limit is not None and transport.position >= song.tempos.seconds(self.limit):
                self.finish(False)  # segmento successivo: si chiude da sola, il play continua
        return GLib.SOURCE_CONTINUE

    def finish(self, stop: bool) -> None:
        """Chiude la registrazione: segmento nuovo con le note; il metronomo si toglie."""
        state, self.state = self.state, "idle"
        self._set_button(False)
        doc = self.session.document
        if doc is None or state == "idle":
            return
        transport, song = self.session.transport, doc.song
        if stop:
            transport.stop()
        here = song.tempos.position(transport.position) if transport.state is State.PLAYING else None
        stop_pos = here if here is not None else getattr(self, "_last_pos", self.start_pos)
        transport.load(build_performance(song))  # via il metronomo (posizione e play restano)
        transport.set_silenced(song.silent_track_ids())
        if state != "recording" or self.track not in song.tracks:
            return
        n = sum(1 for t in song.tracks for s in t.segments if s.label.startswith("Rec ")) + 1
        take = build_take(song, self.start_pos, self.limit, max(Fraction(stop_pos).limit_denominator(7680),
                                                                 self.start_pos), self.events, f"Rec {n}",
                           self.controls)
        if take is None:
            self.message("Empty recording: no notes.", error=False)
            return
        seg, group = take
        if self.track.kind == CHORDS:
            # 2026-11-20 (richiesta dell'utente): sulla traccia accordi le note non si salvano,
            # diventano accordi agganciati a 1/8 (core.chord_track.chords_from_notes)
            group.chord_events = chords_from_notes(song, self.start_pos, group.notes, seg.length)
            group.notes, group.events = [], []
            if not group.chord_events:
                self.message("No chords recognized: nothing recorded.", error=False)
                return
            doc.execute(AddSegments([(self.track, seg, group)]))
            n = len(group.chord_events)
            self.message(f"Recorded {n} {'chord' if n == 1 else 'chords'} on “{self.track.name}”.", error=False)
            return
        doc.execute(AddSegments([(self.track, seg, group)]))
        self.message(f"Recorded {len(group.notes)} {'note' if len(group.notes) == 1 else 'notes'} "
                     f"on “{self.track.name}”.", error=False)
