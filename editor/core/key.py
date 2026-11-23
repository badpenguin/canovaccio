"""Tonalità e grafia delle alterazioni.

`Key` = tonica + modo. Il modo è un oggetto (non un bool maggiore/minore) per
poter aggiungere in seguito modi ecclesiastici o scale non diatoniche.
`Speller` è il punto di estensione per strategie di grafia più furbe
(es. basate sul contesto melodico/armonico).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass

from .pitch import Pitch, Spelling


@dataclass(frozen=True)
class Mode:
    name: str
    # Quinte dalla tonica all'armatura (maggiore 0, minore -3: La min = Do magg).
    signature_offset: int
    # Inizio della finestra di 12 quinte usata per scegliere la grafia,
    # relativo alla tonica. Maggiore (C): Ab Eb Bb … F# C#.
    # Minore (Am): Bb … F# C# G# D# (sensibile e #4 col diesis).
    window_start: int
    suffix: str


MAJOR = Mode("major", 0, -4, "")
MINOR = Mode("minor", -3, -5, "m")
DORIAN = Mode("dorian", -2, -5, " dorian")
PHRYGIAN = Mode("phrygian", -4, -5, " phrygian")
LYDIAN = Mode("lydian", 1, -4, " lydian")
MIXOLYDIAN = Mode("mixolydian", -1, -4, " mixolydian")
LOCRIAN = Mode("locrian", -5, -5, " locrian")

MODES = {m.name: m for m in (MAJOR, MINOR, DORIAN, PHRYGIAN, LYDIAN, MIXOLYDIAN, LOCRIAN)}


@dataclass(frozen=True)
class Key:
    tonic: Spelling
    mode: Mode = MAJOR

    @property
    def fifths(self) -> int:
        """Armatura: >0 diesis, <0 bemolle."""
        return self.tonic.fifths + self.mode.signature_offset

    @classmethod
    def parse(cls, text: str) -> "Key":
        """'C', 'Bm', 'F#m', 'Eb' (stesso formato dei meta-eventi di mido)."""
        text = text.strip()
        if text.endswith("m"):
            return cls(Spelling.parse(text[:-1]), MINOR)
        return cls(Spelling.parse(text), MAJOR)

    @classmethod
    def from_fifths(cls, fifths: int, mode: Mode = MAJOR) -> "Key":
        return cls(Spelling.from_fifths(fifths - mode.signature_offset), mode)

    def __str__(self) -> str:
        return f"{self.tonic}{self.mode.suffix}"


C_MAJOR = Key(Spelling("C"), MAJOR)


class Speller(ABC):
    @abstractmethod
    def spell(self, midi: int, key: Key) -> Pitch: ...


class KeySpeller(Speller):
    """Sceglie, tra le grafie possibili, quella che cade nella finestra
    di 12 quinte associata alla tonalità (nessun contesto melodico).
    Evita i doppi diesis/bemolle quando esiste un'alternativa semplice."""

    def spell(self, midi: int, key: Key) -> Pitch:
        start = key.tonic.fifths + key.mode.window_start
        pc = midi % 12
        f = next(f for f in range(start, start + 12) if (f * 7) % 12 == pc)
        # Linea delle quinte: bemolle -8..-2, naturali -1..5, diesis 6..12.
        if f < -8:  # doppio bemolle → enarmonico semplice
            f += 12
        elif f > 12:  # doppio diesis → enarmonico semplice
            f -= 12
        return Pitch.from_midi(midi, Spelling.from_fifths(f))


DEFAULT_SPELLER: Speller = KeySpeller()


def diatonic_shift(midi: int, key: "Key", steps: int) -> int:
    """Sposta `midi` di `steps` gradi della scala di `key` (le 7 note della sua armatura).
    Una nota fuori scala tiene la sua distanza dal grado sotto di lei (es. un Fa# in Do
    maggiore sale di un grado → Sol#)."""
    pcs = {Spelling.from_fifths(f).pitch_class for f in range(key.fifths - 1, key.fifths + 6)}
    scale = [m for m in range(-36, 164) if m % 12 in pcs]
    below = max(i for i, m in enumerate(scale) if m <= midi)
    # 2026-11-20: oltre i bordi della scala calcolata il risultato è comunque fuori dall'estensione
    # MIDI: si restituisce -1 (il chiamante lo rifiuta) invece di un IndexError o, con un indice
    # negativo, di una nota presa dalla fine della lista (es. 0 meno 48 gradi → 119)
    if not 0 <= below + steps < len(scale):
        return -1
    return scale[below + steps] + (midi - scale[below])
