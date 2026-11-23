"""Altezze: numero MIDI come valore sonoro, nome di nota + alterazione come grafia.

La grafia (Do# vs Re♭) non è memorizzata di default: la calcola uno `Speller`
a partire dalla tonalità. Una nota può avere una grafia forzata a mano.
"""

from __future__ import annotations

from dataclasses import dataclass

STEPS = "CDEFGAB"
STEP_SEMITONES = {"C": 0, "D": 2, "E": 4, "F": 5, "G": 7, "A": 9, "B": 11}
# Posizione sulla linea delle quinte per le note naturali (C = 0).
STEP_FIFTHS = {"F": -1, "C": 0, "G": 1, "D": 2, "A": 3, "E": 4, "B": 5}
_FIFTHS_STEPS = {v: k for k, v in STEP_FIFTHS.items()}

ALTER_SYMBOLS = {-2: "bb", -1: "b", 0: "", 1: "#", 2: "##"}


@dataclass(frozen=True)
class Spelling:
    """Nome di nota senza ottava: step C..B + alterazione -2..+2."""

    step: str
    alter: int = 0

    def __post_init__(self) -> None:
        if self.step not in STEP_SEMITONES:
            raise ValueError(f"invalid step: {self.step!r}")
        if not -2 <= self.alter <= 2:
            raise ValueError(f"alteration out of range: {self.alter}")

    @property
    def pitch_class(self) -> int:
        return (STEP_SEMITONES[self.step] + self.alter) % 12

    @property
    def fifths(self) -> int:
        """Posizione sulla linea delle quinte (Fb=-8 … C=0 … B#=12)."""
        return STEP_FIFTHS[self.step] + 7 * self.alter

    @classmethod
    def from_fifths(cls, fifths: int) -> "Spelling":
        alter, rest = divmod(fifths + 1, 7)
        return cls(_FIFTHS_STEPS[rest - 1], alter)

    @classmethod
    def parse(cls, text: str) -> "Spelling":
        """'C', 'F#', 'Bb', 'Ebb', 'G##'."""
        step, acc = text[0].upper(), text[1:]
        alter = {"": 0, "#": 1, "##": 2, "b": -1, "bb": -2}[acc]
        return cls(step, alter)

    def __str__(self) -> str:
        return self.step + ALTER_SYMBOLS[self.alter]


@dataclass(frozen=True)
class Pitch:
    """Altezza scritta: grafia + ottava (notazione scientifica, C4 = MIDI 60)."""

    spelling: Spelling
    octave: int

    @property
    def midi(self) -> int:
        s = self.spelling
        return 12 * (self.octave + 1) + STEP_SEMITONES[s.step] + s.alter

    @property
    def diatonic(self) -> int:
        """Indice diatonico assoluto (C0 = 0): serve per la posizione sul rigo."""
        return self.octave * 7 + STEPS.index(self.spelling.step)

    @classmethod
    def from_midi(cls, midi: int, spelling: Spelling) -> "Pitch":
        if spelling.pitch_class != midi % 12:
            raise ValueError(f"{spelling} does not match MIDI {midi}")
        octave = (midi - STEP_SEMITONES[spelling.step] - spelling.alter) // 12 - 1
        return cls(spelling, octave)

    def __str__(self) -> str:
        return f"{self.spelling}{self.octave}"
