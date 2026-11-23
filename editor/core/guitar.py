"""Accordi di chitarra da libretto (Composer > Guitar Chord…), note reali (nessuna
trasposizione d'ottava come nella scrittura per chitarra).

Accordatura standard Mi La Re Sol Si Mi. Tipi: maggiore, minore, 7, m7, maj7, per le
12 fondamentali. Si usa la posizione aperta classica dove esiste; altrimenti il barré
a forma di Mi (fondamentale sulla 6ª corda) o di La (sulla 5ª), sul tasto più basso.
Le corde segnate "x" non si suonano: un accordo ha 4, 5 o 6 note.
"""

from __future__ import annotations

from dataclasses import dataclass

TUNING = [40, 45, 50, 55, 59, 64]  # Mi2 La2 Re3 Sol3 Si3 Mi4 (dalla 6ª alla 1ª corda)
KINDS = [("maj", "major"), ("min", "minor"), ("7", "7"), ("m7", "m7"), ("maj7", "maj7")]
ROOTS = ["C", "C#", "D", "Eb", "E", "F", "F#", "G", "Ab", "A", "Bb", "B"]  # per classe d'altezza
X = None  # corda non suonata

# posizioni aperte del libretto: (classe d'altezza della fondamentale, tipo) → tasti dalla 6ª alla 1ª corda
OPEN = {
    (0, "maj"): [X, 3, 2, 0, 1, 0], (2, "maj"): [X, X, 0, 2, 3, 2], (4, "maj"): [0, 2, 2, 1, 0, 0],
    (7, "maj"): [3, 2, 0, 0, 0, 3], (9, "maj"): [X, 0, 2, 2, 2, 0],
    (9, "min"): [X, 0, 2, 2, 1, 0], (2, "min"): [X, X, 0, 2, 3, 1], (4, "min"): [0, 2, 2, 0, 0, 0],
    (9, "7"): [X, 0, 2, 0, 2, 0], (11, "7"): [X, 2, 1, 2, 0, 2], (0, "7"): [X, 3, 2, 3, 1, 0],
    (2, "7"): [X, X, 0, 2, 1, 2], (4, "7"): [0, 2, 0, 1, 0, 0], (7, "7"): [3, 2, 0, 0, 0, 1],
    (9, "m7"): [X, 0, 2, 0, 1, 0], (2, "m7"): [X, X, 0, 2, 1, 1], (4, "m7"): [0, 2, 0, 0, 0, 0],
    (11, "m7"): [X, 2, 0, 2, 0, 2],
    (0, "maj7"): [X, 3, 2, 0, 0, 0], (2, "maj7"): [X, X, 0, 2, 2, 2], (4, "maj7"): [0, 2, 1, 1, 0, 0],
    (5, "maj7"): [X, X, 3, 2, 1, 0], (7, "maj7"): [3, 2, 0, 0, 0, 2], (9, "maj7"): [X, 0, 2, 1, 2, 0],
}

# forme barré: scostamenti dal tasto del barré
E_SHAPE = {"maj": [0, 2, 2, 1, 0, 0], "min": [0, 2, 2, 0, 0, 0], "7": [0, 2, 0, 1, 0, 0],
           "m7": [0, 2, 0, 0, 0, 0], "maj7": [0, X, 1, 1, 0, X]}
A_SHAPE = {"maj": [X, 0, 2, 2, 2, 0], "min": [X, 0, 2, 2, 1, 0], "7": [X, 0, 2, 0, 2, 0],
           "m7": [X, 0, 2, 0, 1, 0], "maj7": [X, 0, 2, 1, 2, 0]}


@dataclass(frozen=True)
class GuitarChord:
    frets: list  # dalla 6ª alla 1ª corda; None = non suonata
    description: str  # "aperto" o "barré 3° tasto (forma di Mi)"; 2026-11-20: in inglese, "open" o "barre at fret 3 (E shape)"

    @property
    def shape(self) -> str:
        """Come sui libretti: "x32010" (tasti oltre il 9 separati da trattini)."""
        parts = ["x" if f is None else str(f) for f in self.frets]
        return ("-" if any(len(p) > 1 for p in parts) else "").join(parts)

    @property
    def midi(self) -> list[int]:
        """Note reali dalla corda più grave alla più acuta (solo le corde suonate)."""
        return [TUNING[i] + f for i, f in enumerate(self.frets) if f is not None]


def guitar_chord(pitch_class: int, kind: str) -> GuitarChord:
    """Forma del libretto per la fondamentale (classe d'altezza 0–11) e il tipo."""
    if (pitch_class, kind) in OPEN:
        return GuitarChord(OPEN[(pitch_class, kind)], "open")
    e_fret = (pitch_class - 4) % 12 or 12  # fondamentale sulla 6ª corda (Mi)
    a_fret = (pitch_class - 9) % 12 or 12  # sulla 5ª corda (La)
    if e_fret <= a_fret:
        fret, shape, name = e_fret, E_SHAPE[kind], "E"
    else:
        fret, shape, name = a_fret, A_SHAPE[kind], "A"
    frets = [None if d is None else fret + d for d in shape]
    return GuitarChord(frets, f"barre at fret {fret} ({name} shape)")
