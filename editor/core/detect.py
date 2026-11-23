"""Rilevamenti sulle note importate (nessuna quantizzazione: gli inizi non si spostano).

Staccato: nel MIDI lo staccato non esiste; alcuni programmi (es. MuseScore) lo
esportano accorciando la nota a metà. Una nota che dura esattamente metà dello
spazio fino all'attacco successivo (a meno dell'arrotondamento dei tick) viene
riportata alla durata scritta (fino all'attacco successivo) e prende il segno di
staccato: al play suona come prima.
"""

from __future__ import annotations

from bisect import bisect_right
from fractions import Fraction

from .model import Note

TICK_TOLERANCE = Fraction(1, 960)  # semibreve: ~2 tick a 480 per semiminima (arrotondamenti dell'export)


def detect_staccato(notes: list[Note], only: list[Note] | None = None,
                    end: Fraction | None = None) -> list[tuple[Note, Fraction]]:
    """Note (fra `only`, o tutte) che risultano staccate, con la loro durata scritta.
    `notes` sono le note di un gruppo: l'attacco successivo si cerca fra tutte; `end`
    (fine del segmento, nelle stesse coordinate) vale come attacco dopo l'ultima nota."""
    onsets = sorted({n.start for n in notes} | ({end} if end is not None else set()))
    found = []
    for n in only if only is not None else notes:
        if n.staccato or n.tie_next:
            continue
        i = bisect_right(onsets, n.start)
        if i >= len(onsets):
            continue  # ultima nota, senza fine del segmento: non c'è un attacco dopo
        gap = onsets[i] - n.start
        if abs(2 * n.duration - gap) <= TICK_TOLERANCE:
            found.append((n, gap))
    return found


def tie_targets(placed: list, selected_ids: set[int]) -> list[tuple[object, object]]:
    """Legature di valore possibili: (nota selezionata, nota successiva) dove la successiva ha
    la stessa altezza e inizia esattamente dove finisce la selezionata. `placed`: note
    posizionate della traccia (PlacedNote), `selected_ids`: id() delle note selezionate."""
    by_start = {(p.start, p.midi): p for p in placed}
    pairs = []
    for p in placed:
        if id(p.note) in selected_ids and (nxt := by_start.get((p.end, p.midi))) is not None:
            pairs.append((p, nxt))
    return pairs
