"""Ingresso MIDI (File > Input Device…): una porta del sistema (python-rtmidi: ALSA/JACK,
WinMM, CoreMIDI) e il thru, cioè quello che si suona arriva subito all'uscita in uso con
canale e suono della traccia selezionata.

`Thru` è separato dalla porta (si prova senza hardware): riceve i messaggi grezzi e
manda gli eventi della performance. Il rilascio di una nota va alla traccia su cui è
partita (cambiando traccia con un tasto premuto non restano note appese); il pedale di
sustain rilasciato va a tutte le tracce su cui era stato premuto.
"""

from __future__ import annotations

import logging
import time
from typing import Callable

from .midi_port_backend import CLIENT_NAME, port_name
from .performance import Control, NoteOff, NoteOn, PitchBend

log = logging.getLogger(__name__)
SUSTAIN = 64


def list_input_ports() -> list[str]:
    """Porte MIDI d'ingresso presenti ora nel sistema (vuoto se rtmidi o il MIDI mancano)."""
    try:
        import rtmidi

        midi_in = rtmidi.MidiIn(name=CLIENT_NAME)
    except Exception as exc:  # noqa: BLE001
        log.warning("porte MIDI d'ingresso non disponibili: %s", exc)
        return []
    try:
        return [p for p in dict.fromkeys(port_name(p) for p in midi_in.get_ports()) if CLIENT_NAME not in p]
    finally:
        midi_in.delete()


class MidiInput:
    """Porta d'ingresso aperta; `on_message(bytes, istante)` è chiamata dal thread di rtmidi
    (istante = time.monotonic() all'arrivo, per collocare le note registrate)."""

    def __init__(self, on_message: Callable[[list[int], float], None]) -> None:
        self.on_message = on_message
        self.port = ""
        self._in = None

    def open(self, port: str) -> None:
        import rtmidi

        self.close()
        midi_in = rtmidi.MidiIn(name=CLIENT_NAME)
        ports = [port_name(p) for p in midi_in.get_ports()]
        if port not in ports:
            midi_in.delete()
            raise RuntimeError(f"MIDI port «{port}» not found (connected and switched on?)")
        midi_in.open_port(ports.index(port), name="in")
        midi_in.ignore_types(sysex=True, timing=True, active_sense=True)
        midi_in.set_callback(lambda message, _data: self.on_message(list(message[0]), time.monotonic()))
        self._in, self.port = midi_in, port
        log.info("ingresso MIDI: %s", port)

    def close(self) -> None:
        if self._in is not None:
            self._in.cancel_callback()
            self._in.close_port()
            self._in.delete()
            self._in, self.port = None, ""


class Thru:
    def __init__(self, send: Callable[[object], None], track_id: Callable[[], str | None]) -> None:
        self.send, self.track_id = send, track_id
        self._held: dict[int, str] = {}  # tasto premuto → traccia su cui suona
        self._sustained: set[str] = set()  # tracce col pedale abbassato

    def handle(self, message: list[int]) -> None:
        if not message:
            return
        status = message[0] & 0xF0
        data = message[1:] + [0, 0]
        if status == 0x90 and data[1] > 0:
            track = self.track_id()
            if track is not None:
                self._held[data[0]] = track
                self.send(NoteOn(track, data[0], data[1]))
        elif status in (0x80, 0x90):
            track = self._held.pop(data[0], None) or self.track_id()
            if track is not None:
                self.send(NoteOff(track, data[0]))
        elif status == 0xB0:
            if data[0] == SUSTAIN and data[1] < 64:  # pedale su: a tutte le tracce dove era giù
                targets = self._sustained | ({self.track_id()} - {None})
                self._sustained.clear()
            else:
                targets = {self.track_id()} - {None}
                if data[0] == SUSTAIN:
                    self._sustained |= targets
            for track in targets:
                self.send(Control(track, data[0], data[1]))
        elif status == 0xE0:
            track = self.track_id()
            if track is not None:
                self.send(PitchBend(track, ((data[1] << 7) | data[0]) - 8192))
