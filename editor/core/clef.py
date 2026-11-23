"""Chiavi musicali, modellate come (segno, linea, ottava)."""

from __future__ import annotations

from dataclasses import dataclass

from .pitch import STEPS, Pitch

# Altezza diatonica (C0 = 0) indicata da ciascun segno di chiave.
_SIGN_DIATONIC = {"G": 4 * 7 + 4, "F": 3 * 7 + 3, "C": 4 * 7 + 0}


@dataclass(frozen=True)
class Clef:
    name: str
    sign: str  # "G", "F", "C", "percussion"
    line: int  # linea del rigo su cui sta il segno, 1 = la più bassa
    octave: int = 0  # es. -1 per violino 8vb

    @property
    def bottom_line(self) -> int:
        """Indice diatonico della nota sulla prima linea (in basso)."""
        sign = self.sign if self.sign in _SIGN_DIATONIC else "G"
        line = self.line if self.sign in _SIGN_DIATONIC else 2
        return _SIGN_DIATONIC[sign] - 2 * (line - 1) + 7 * self.octave

    def staff_step(self, pitch: Pitch) -> int:
        """Posizione sul rigo: 0 = prima linea, 1 = primo spazio, … 8 = quinta linea."""
        return pitch.diatonic - self.bottom_line

    def step_name(self, staff_step: int) -> str:
        return STEPS[(self.bottom_line + staff_step) % 7]


TREBLE = Clef("treble", "G", 2)
BASS = Clef("bass", "F", 4)
ALTO = Clef("alto", "C", 3)
TENOR = Clef("tenor", "C", 4)
TREBLE_8VB = Clef("treble_8vb", "G", 2, -1)
PERCUSSION = Clef("percussion", "percussion", 3)

CLEFS = {c.name: c for c in (TREBLE, BASS, ALTO, TENOR, TREBLE_8VB, PERCUSSION)}

# Chiavi selezionabili nell'MVP, con etichetta per la UI.
MVP_CLEFS = [(TREBLE, "Treble"), (BASS, "Bass")]
