"""Strato 4 — backend d'uscita (interfaccia + implementazioni di base).

Un backend riceve eventi neutri (per traccia) e li fa suonare. L'assegnazione
dei canali MIDI è un dettaglio dei backend MIDI (`MidiChannelMap`).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Sequence

from .devices import NAME_ALIASES
from .performance import Event, TrackSetup


class OutputBackend(ABC):
    name: str = "?"
    output_name: str = ""  # nome dell'uscita configurata: sceglie i suoni per traccia (Track.sounds)

    def open(self) -> None:
        """Acquisisce le risorse (synth, porte, driver audio)."""

    def close(self) -> None:
        """Rilascia le risorse."""

    @abstractmethod
    def setup(self, tracks: Sequence[TrackSetup]) -> None:
        """Configura tracce e strumenti (chiamato prima del play e a ogni cambio)."""

    @abstractmethod
    def send(self, event: Event) -> None:
        """Invia subito un evento."""

    @abstractmethod
    def panic(self) -> None:
        """Ferma tutti i suoni."""

    def reset(self) -> None:
        """Pulsante Panic: ferma tutti i suoni e rimette ai valori GM i controlli che il
        Reset All Controllers del panic non tocca (volume, pan, riverbero…), su tutti i canali.
        2026-11-20: deciso dall'utente al posto di mandarli a ogni play (con un'uscita
        esterna sovrascriverebbero i valori impostati sullo strumento)."""
        self.panic()

    def start(self) -> float:
        """All'avvio del play (es. porta MIDI: rimanda gli strumenti). Ritorna i secondi da
        aspettare prima delle prime note (il dispositivo sta cambiando suono)."""
        return 0.0

    def describe(self) -> str:
        """Testo per la barra di stato."""
        return self.name


# controlli rimessi dal pulsante Panic: valori di default GM (volume, pan, expression,
# riverbero, chorus); pitch bend e sustain li azzera già il Reset All Controllers (CC121)
GM_DEFAULT_CONTROLS = ((7, 100), (10, 64), (11, 127), (91, 40), (93, 0))


class NullBackend(OutputBackend):
    """Non suona nulla: per quando non c'è audio disponibile."""

    name = "null"

    def setup(self, tracks: Sequence[TrackSetup]) -> None:
        pass

    def send(self, event: Event) -> None:
        pass

    def panic(self) -> None:
        pass


class RecordingBackend(OutputBackend):
    """Registra gli eventi ricevuti (test)."""

    name = "recording"

    def __init__(self) -> None:
        self.tracks: list[TrackSetup] = []
        self.sent: list[Event] = []
        self.panics = 0
        self.starts = 0

    def setup(self, tracks: Sequence[TrackSetup]) -> None:
        self.tracks = list(tracks)

    def send(self, event: Event) -> None:
        self.sent.append(event)

    def panic(self) -> None:
        self.panics += 1

    def start(self) -> float:
        self.starts += 1
        return 0.0


PERCUSSION_CHANNEL = 9


class MidiChannelMap:
    """Ogni traccia suona sul suo canale MIDI (1–16 → 0–15). Sul canale 10 o con uno
    strumento a percussione si usa il banco batteria. Tracce sullo stesso canale lo
    condividono: lo strumento è quello della prima traccia."""

    def __init__(self, tracks: Sequence[TrackSetup]) -> None:
        self.channel: dict[str, int] = {}
        self.program: dict[int, tuple[int, bool]] = {}  # canale → (programma, percussioni)
        self.setup: dict[int, TrackSetup] = {}  # canale → la prima traccia (ne decide il suono)
        for t in tracks:
            inst = t.instrument
            ch = min(max(int(getattr(t, "channel", 1)), 1), 16) - 1
            self.channel[t.track_id] = ch
            self.program.setdefault(ch, (getattr(inst, "program", 0), inst.is_percussion or ch == PERCUSSION_CHANNEL))
            self.setup.setdefault(ch, t)

    def sound(self, ch: int, output_name: str):
        """Suono della traccia per quell'uscita (model.Sound), o None = strumento GM."""
        t = self.setup.get(ch)
        if t is None or not output_name:
            return None
        sounds = getattr(t, "sounds", {})
        # 2026-11-20: l'uscita interna di default si chiama "Internal" (prima "Interno"):
        # se non c'è un suono con il nome esatto si prova l'altro, per i brani salvati prima
        found = sounds.get(output_name)
        return found if found is not None else sounds.get(NAME_ALIASES.get(output_name, ""))
