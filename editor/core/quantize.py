"""Quantizzazione: allinea inizio e/o fine (o la durata) delle note a una griglia.

La griglia si conta dall'inizio di ogni misura (come lo Snap della griglia).
Posizioni assolute in semibrevi (Fraction).
"""

from __future__ import annotations

from fractions import Fraction

from .timeline import MeterTable

START, START_LENGTH, START_END, END, LENGTH = "start", "start+length", "start+end", "end", "length"
MODES = [
    (START_LENGTH, "Start and length"),
    (START, "Start"),
    (START_END, "Start and end"),
    (END, "End"),
    (LENGTH, "Length only"),
]
GRIDS = [
    ("1/4", Fraction(1, 4)), ("1/8", Fraction(1, 8)), ("1/16", Fraction(1, 16)), ("1/32", Fraction(1, 32)),
    ("1/8 triplet", Fraction(1, 12)), ("1/16 triplet", Fraction(1, 24)),
    ("1/4 dotted", Fraction(3, 8)), ("1/8 dotted", Fraction(3, 16)), ("1/16 dotted", Fraction(3, 32)),
]


def round_half_up(x: Fraction) -> int:
    """Intero più vicino, a metà sempre verso l'alto.

    2026-11-20: round() di Python arrotonda le metà al pari, quindi punti a metà fra due tacche
    andavano a volte giù e a volte su (1/16 → 0, 3/16 → 1/4, 5/16 → 1/4 con passo 1/8) e due
    note diverse finivano nello stesso punto."""
    return (x + Fraction(1, 2)).__floor__()


def snap(pos: Fraction, step: Fraction, meters: MeterTable, how: str = "round") -> Fraction:
    """Punto di griglia (passo `step` dall'inizio della misura) più vicino / sotto / sopra."""
    start = meters.bar_start(meters.bar_at(max(pos, Fraction(0))))
    steps = (pos - start) / step
    n = {"round": round_half_up(steps), "floor": steps.__floor__(), "ceil": steps.__ceil__()}[how]
    return start + n * step


def quantize_span(start: Fraction, end: Fraction, step: Fraction, meters: MeterTable,
                  mode: str = START_LENGTH) -> tuple[Fraction, Fraction]:
    """Nuovi (inizio, fine) della nota; la durata resta almeno di un passo."""
    length = end - start
    if mode == START:
        s = snap(start, step, meters)
        return s, s + length
    if mode == START_LENGTH:
        s = snap(start, step, meters)
        return s, s + max(round_half_up(length / step), 1) * step
    if mode == START_END:
        s, e = snap(start, step, meters), snap(end, step, meters)
        return s, max(e, s + step)
    if mode == END:
        e = snap(end, step, meters)
        return start, e if e > start else snap(start, step, meters, "floor") + step
    if mode == LENGTH:
        return start, start + max(round_half_up(length / step), 1) * step
    raise ValueError(mode)
