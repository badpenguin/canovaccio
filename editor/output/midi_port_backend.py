"""Uscita su una porta MIDI del sistema (tastiera o expander esterno, synth software):
python-rtmidi, che usa ALSA/JACK su Linux, WinMM su Windows, CoreMIDI su macOS.

Ogni traccia suona sul suo canale (1–16) della porta. Con `program_change`, al play
(e quando cambia) si manda a ogni canale il suono della traccia: quello scelto per
questa uscita (Bank Select MSB/LSB + Program Change, anche sul canale 10) o lo
strumento GM (non al canale 10: lì il suono lo decide il canale). Allo stop: All Notes Off e All Sound Off.
"""

from __future__ import annotations

import logging
import re
from typing import Sequence

from .. import APP_NAME
from .backend import GM_DEFAULT_CONTROLS, PERCUSSION_CHANNEL, MidiChannelMap, OutputBackend
from .performance import Control, Event, NoteOff, NoteOn, PitchBend, PolyPressure, Pressure, TrackSetup

log = logging.getLogger(__name__)

# 2026-11-23: nome del client MIDI = nome visibile del programma (si vede nelle porte degli altri programmi)
CLIENT_NAME = APP_NAME
PROGRAM_SETTLE = 0.2  # secondi fra i Program Change dell'avvio e le prime note
_NUMBERS = re.compile(r"\s+\d+(:\d+)?$")  # "… 20:0" (ALSA) o "… 1" (Windows): cambiano da una volta all'altra


def port_name(raw: str) -> str:
    """Nome stabile di una porta, senza i numeri che il sistema le assegna. ALSA dà
    "client:porta": se la porta ripete il nome del client resta solo la porta
    ("FluidSynth:FluidSynth" → "FluidSynth")."""
    name = _NUMBERS.sub("", raw).strip()
    client, sep, port = name.partition(":")
    return port if sep and port.startswith(client) else name


def list_ports() -> list[str]:
    """Porte MIDI d'uscita presenti ora nel sistema (vuoto se rtmidi o il MIDI mancano)."""
    try:
        import rtmidi

        out = rtmidi.MidiOut(name=CLIENT_NAME)
    except Exception as exc:  # noqa: BLE001 — niente MIDI: nessuna porta
        log.warning("porte MIDI non disponibili: %s", exc)
        return []
    try:
        return list(dict.fromkeys(port_name(p) for p in out.get_ports()))
    finally:
        out.delete()


class MidiPortBackend(OutputBackend):
    name = "midi"

    def __init__(self, port: str, program_change: bool = True) -> None:
        self.port = port
        self.program_change = program_change
        self._out = None
        self._channels = MidiChannelMap([])
        self._sent: dict[int, tuple] = {}  # canale → suono già mandato

    def open(self) -> None:
        import rtmidi  # import qui: il resto dell'app funziona anche senza

        if not self.port:
            raise RuntimeError("no MIDI port selected")
        out = rtmidi.MidiOut(name=CLIENT_NAME)
        ports = [port_name(p) for p in out.get_ports()]
        if self.port not in ports:
            out.delete()
            raise RuntimeError(f"MIDI port «{self.port}» not found (connected and switched on?)")
        out.open_port(ports.index(self.port), name="out")
        self._out = out
        log.info("porta MIDI: %s", self.port)

    def close(self) -> None:
        if self._out is not None:
            self.panic()
            self._out.close_port()
            self._out.delete()
            self._out = None

    def describe(self) -> str:
        return f"MIDI · {self.port}"

    def setup(self, tracks: Sequence[TrackSetup]) -> None:
        self._channels = MidiChannelMap(tracks)
        self._programs(only_changed=True)

    def start(self) -> float:
        """Rimanda i suoni; se ne ha mandati, il play aspetta PROGRAM_SETTLE secondi: le note
        che arrivano mentre il dispositivo cambia suono si perdono o escono troncate."""
        return PROGRAM_SETTLE if self._programs(only_changed=False) else 0.0

    def _programs(self, only_changed: bool) -> bool:
        """Program Change per canale. Durante l'editing solo se cambiato: alcuni device,
        a ogni Program Change, ricaricano il suono e fanno un buco."""
        if self._out is None or not self.program_change:
            return False
        sent = False
        for ch, (program, percussion) in self._channels.program.items():
            sound = self._channels.sound(ch, self.output_name)
            # 2026-11-20: se sul canale era stato mandato un Bank Select (suono personalizzato),
            # tornando al GM si rimanda anche il banco 0/0: prima il device restava sul banco vecchio
            prev = self._sent.get(ch)
            had_bank = bool(prev) and prev[0][0] == 0xB0 | ch and prev[0][1] == 0
            reset_bank = [[0xB0 | ch, 0, 0], [0xB0 | ch, 32, 0]] if had_bank else []
            if sound is not None:
                messages = [[0xB0 | ch, 0, sound.bank & 0x7F], [0xB0 | ch, 32, sound.lsb & 0x7F],
                            [0xC0 | ch, sound.program & 0x7F]]
            elif percussion or ch == PERCUSSION_CHANNEL:
                if not reset_bank:
                    continue  # batteria GM: niente Program Change (come prima)
                messages = reset_bank + [[0xC0 | ch, 0]]  # dal kit personalizzato al kit standard
            else:
                messages = reset_bank + [[0xC0 | ch, program & 0x7F]]
            key = tuple(map(tuple, messages))
            if only_changed and self._sent.get(ch) == key:
                continue
            for m in messages:
                self._out.send_message(m)
            self._sent[ch] = key
            sent = True
        return sent

    def send(self, event: Event) -> None:
        if self._out is None:
            return
        ch = self._channels.channel.get(event.track_id)
        if ch is None:
            return
        if isinstance(event, NoteOn):
            self._out.send_message([0x90 | ch, event.key & 0x7F, max(1, min(event.velocity, 127))])
        elif isinstance(event, NoteOff):
            self._out.send_message([0x80 | ch, event.key & 0x7F, 0])
        elif isinstance(event, Control):
            self._out.send_message([0xB0 | ch, event.control & 0x7F, event.value & 0x7F])
        elif isinstance(event, PitchBend):
            v = max(0, min(event.value + 8192, 16383))
            self._out.send_message([0xE0 | ch, v & 0x7F, v >> 7])
        elif isinstance(event, Pressure):
            self._out.send_message([0xD0 | ch, event.value & 0x7F])
        elif isinstance(event, PolyPressure):
            self._out.send_message([0xA0 | ch, event.key & 0x7F, event.value & 0x7F])

    def panic(self) -> None:
        if self._out is None:
            return
        for ch in range(16):
            self._out.send_message([0xB0 | ch, 121, 0])  # Reset All Controllers (pitch bend, sustain…)
            self._out.send_message([0xB0 | ch, 123, 0])  # All Notes Off
            self._out.send_message([0xB0 | ch, 120, 0])  # All Sound Off

    def reset(self) -> None:
        """Pulsante Panic: panic + controlli ai valori GM su tutti i canali."""
        self.panic()
        if self._out is None:
            return
        for ch in range(16):
            for control, value in GM_DEFAULT_CONTROLS:
                self._out.send_message([0xB0 | ch, control, value])
