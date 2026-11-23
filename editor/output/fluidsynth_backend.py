"""Synth interno: FluidSynth nel processo (audio verso PipeWire/Pulse/ALSA/JACK su Linux,
WASAPI/DirectSound su Windows, CoreAudio su macOS)."""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Sequence

from ..system import auto_drivers, soundfont_dirs
from .backend import GM_DEFAULT_CONTROLS, MidiChannelMap, OutputBackend
from .performance import Control, Event, NoteOff, NoteOn, PitchBend, PolyPressure, Pressure, TrackSetup

log = logging.getLogger(__name__)

SOUNDFONT_ENV = "TODO_MUSICIAN_SF2"
DRIVER_ENV = "TODO_MUSICIAN_AUDIO_DRIVER"
SOUNDFONT_NAMES = ["FluidR3_GM.sf2", "default-GM.sf2", "default.sf2", "TimGM6mb.sf2", "GeneralUser GS.sf2",
                   "FluidR3_GM.sf3", "MuseScore_General.sf3"]


def soundfont_files() -> list[Path]:
    """SoundFont trovati nelle cartelle del sistema (prima quelli GM noti)."""
    found: list[Path] = []
    for d in soundfont_dirs():
        try:
            found += sorted(p for p in d.iterdir() if p.suffix.lower() in (".sf2", ".sf3") and p.is_file())
        except OSError:
            continue
    rank = {n.lower(): i for i, n in enumerate(SOUNDFONT_NAMES)}
    return sorted(dict.fromkeys(found), key=lambda p: rank.get(p.name.lower(), len(rank)))


def find_soundfont() -> str | None:
    """SoundFont automatico: $TODO_MUSICIAN_SF2, altrimenti il primo trovato nel sistema."""
    env = os.environ.get(SOUNDFONT_ENV)
    if env and Path(env).is_file():
        return env
    files = soundfont_files()
    return str(files[0]) if files else None


class FluidSynthBackend(OutputBackend):
    name = "fluidsynth"

    def __init__(self, soundfont: str | None = None, driver: str | None = None, gain: float = 0.5) -> None:
        self.soundfont = soundfont or find_soundfont()
        self.driver = driver or os.environ.get(DRIVER_ENV)
        self.gain = gain
        self.active_driver: str | None = None
        self._synth = None
        self._sfid: int | None = None
        self._channels = MidiChannelMap([])
        self._extra: dict[str, int] = {}  # altri SoundFont caricati (suoni per traccia): file → sfid

    def open(self) -> None:
        import fluidsynth  # import qui: il resto dell'app funziona anche senza

        if not self.soundfont:
            raise RuntimeError(f"no SoundFont found (set {SOUNDFONT_ENV})")
        synth = fluidsynth.Synth(gain=self.gain)
        # Niente Synth.start(): aprirebbe anche un ingresso MIDI ALSA che non serve.
        for drv in [self.driver] if self.driver else auto_drivers():
            synth.setting("audio.driver", drv)
            handle = fluidsynth.new_fluid_audio_driver(synth.settings, synth.synth)
            if handle:
                synth.audio_driver = handle
                self.active_driver = drv
                break
        if self.active_driver is None:
            synth.delete()
            raise RuntimeError("no audio driver available for FluidSynth")
        sfid = synth.sfload(self.soundfont)
        if sfid < 0:
            synth.delete()
            raise RuntimeError(f"cannot load SoundFont: {self.soundfont}")
        self._synth, self._sfid = synth, sfid
        log.info("FluidSynth: driver %s, SoundFont %s", self.active_driver, self.soundfont)

    def describe(self) -> str:
        sf = Path(self.soundfont).name if self.soundfont else "?"
        return f"internal · {self.active_driver or '?'} · {sf}"

    def close(self) -> None:
        if self._synth is not None:
            self.panic()
            self._synth.delete()
            self._synth = None

    def setup(self, tracks: Sequence[TrackSetup]) -> None:
        self._channels = MidiChannelMap(tracks)
        if self._synth is None:
            return
        for ch, (program, percussion) in self._channels.program.items():
            sound = self._channels.sound(ch, self.output_name)
            if sound is not None:  # suono scelto per la traccia su questa uscita
                sfid = self._soundfont_id(sound.soundfont)
                if sfid is not None and self._synth.program_select(ch, sfid, sound.bank, sound.program) == 0:
                    log.info("canale %d: %s banco %d programma %d", ch + 1, sound.soundfont or "SoundFont dell'uscita",
                             sound.bank, sound.program)
                    continue
                log.warning("canale %d: suono non disponibile (%s %d:%d), uso il GM", ch + 1, sound.soundfont,
                            sound.bank, sound.program)
            result = self._synth.program_select(ch, self._sfid, 128 if percussion else 0, program)
            log.info("canale %d: banco %d programma %d (esito %s, ora %s)", ch + 1, 128 if percussion else 0,
                     program, result, self._synth.program_info(ch))

    def _soundfont_id(self, path: str) -> int | None:
        """sfid del SoundFont ("" = quello dell'uscita), caricandolo la prima volta."""
        if not path or path == self.soundfont:
            return self._sfid
        if path not in self._extra:
            sfid = self._synth.sfload(path) if Path(path).is_file() else -1
            if sfid < 0:
                return None
            self._extra[path] = sfid
        return self._extra[path]

    def send(self, event: Event) -> None:
        if self._synth is None:
            return
        ch = self._channels.channel.get(event.track_id)
        if ch is None:
            return
        if isinstance(event, NoteOn):
            self._synth.noteon(ch, event.key, event.velocity)
        elif isinstance(event, NoteOff):
            self._synth.noteoff(ch, event.key)
        elif isinstance(event, Control):
            self._synth.cc(ch, event.control, event.value)
        elif isinstance(event, PitchBend):
            self._synth.pitch_bend(ch, event.value)
        elif isinstance(event, Pressure):
            _channel_pressure(self._synth.synth, ch, event.value)
        elif isinstance(event, PolyPressure):
            _key_pressure(self._synth.synth, ch, event.key, event.value)

    def panic(self) -> None:
        if self._synth is None:
            return
        for ch in range(16):
            self._synth.cc(ch, 121, 0)  # Reset All Controllers: pitch bend al centro, sustain su…
            self._synth.all_notes_off(ch)
            self._synth.all_sounds_off(ch)

    def reset(self) -> None:
        """Pulsante Panic: panic + controlli ai valori GM su tutti i canali."""
        self.panic()
        if self._synth is None:
            return
        for ch in range(16):
            for control, value in GM_DEFAULT_CONTROLS:
                self._synth.cc(ch, control, value)


_presets_cache: dict[str, list[tuple[int, int, str]]] = {}


def list_presets(path: str) -> list[tuple[int, int, str]]:
    """Suoni di un SoundFont: (banco, programma, nome), ordinati. Vuoto se il file non si
    legge o FluidSynth manca. Il synth usato per leggerli non apre l'audio."""
    if path in _presets_cache:
        return _presets_cache[path]
    presets: list[tuple[int, int, str]] = []
    try:
        import fluidsynth

        synth = fluidsynth.Synth()
        try:
            sfid = synth.sfload(path)
            if sfid >= 0:
                for bank in range(129):
                    for program in range(128):
                        name = synth.sfpreset_name(sfid, bank, program)
                        if name:
                            presets.append((bank, program, name))
        finally:
            synth.delete()
    except Exception as exc:  # noqa: BLE001 — niente elenco
        log.warning("suoni di %s non leggibili: %s", path, exc)
    _presets_cache[path] = presets
    return presets


def _channel_pressure(synth, chan: int, value: int) -> None:
    """Aftertouch di canale e polifonico: pyfluidsynth non li espone, si chiamano dalla libreria."""
    import ctypes

    import fluidsynth

    global _CP
    if "_CP" not in globals():
        _CP = fluidsynth.cfunc("fluid_synth_channel_pressure", ctypes.c_int, ("synth", ctypes.c_void_p, 1),
                               ("chan", ctypes.c_int, 1), ("val", ctypes.c_int, 1))
    if _CP is not None:
        _CP(synth, chan, value)


def _key_pressure(synth, chan: int, key: int, value: int) -> None:
    import ctypes

    import fluidsynth

    global _KP
    if "_KP" not in globals():
        _KP = fluidsynth.cfunc("fluid_synth_key_pressure", ctypes.c_int, ("synth", ctypes.c_void_p, 1),
                               ("chan", ctypes.c_int, 1), ("key", ctypes.c_int, 1), ("val", ctypes.c_int, 1))
    if _KP is not None:
        _KP(synth, chan, key, value)
