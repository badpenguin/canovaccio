"""Tabelle globali di eventi del brano: tempo, metro, tonalità.

Posizioni musicali in `Fraction` di semibreve (semiminima = 1/4).
Metro e tonalità cambiano solo sulle stanghette (indice di misura, 0 = prima).
Ogni tabella ha sempre un evento iniziale.
"""

from __future__ import annotations

from bisect import bisect_left, bisect_right
from dataclasses import dataclass
from fractions import Fraction
from typing import Generic, Iterator, TypeVar

from .key import C_MAJOR, Key

T = TypeVar("T")


@dataclass(frozen=True)
class TimeSignature:
    numerator: int = 4
    denominator: int = 4

    @property
    def bar_length(self) -> Fraction:
        return Fraction(self.numerator, self.denominator)

    @property
    def beat(self) -> Fraction:
        return Fraction(1, self.denominator)

    def __str__(self) -> str:
        return f"{self.numerator}/{self.denominator}"


@dataclass(frozen=True)
class TableEvent(Generic[T]):
    at: Fraction | int  # posizione (tempo) o misura (metro, tonalità)
    value: T


class EventTable(Generic[T]):
    """Lista ordinata di eventi; il primo sta sempre a 0."""

    def __init__(self, initial: T, events: list[tuple] | None = None) -> None:
        self._events: list[TableEvent[T]] = [TableEvent(0, initial)]
        # 2026-11-20: posizioni degli eventi tenute in una lista a parte (aggiornata in set): prima
        # at() e set() la ricostruivano a ogni chiamata, O(eventi) per ogni nota impaginata o suonata
        self._keys: list = [0]
        for at, value in events or []:
            self.set(at, value)

    def set(self, at, value: T) -> None:
        if at < 0:
            raise ValueError("negative position")
        i = bisect_right(self._keys, at)
        if i and self._keys[i - 1] == at:
            self._events[i - 1] = TableEvent(at, value)
        else:
            self._events.insert(i, TableEvent(at, value))
            self._keys.insert(i, at)
        self._changed()

    def _changed(self) -> None:
        """Dopo una modifica: le sottoclassi azzerano qui i loro dati precalcolati."""

    def at(self, where) -> T:
        return self._events[max(bisect_right(self._keys, where) - 1, 0)].value

    @property
    def initial(self) -> T:
        return self._events[0].value

    def __iter__(self) -> Iterator[TableEvent[T]]:
        return iter(self._events)

    def __len__(self) -> int:
        return len(self._events)


class TempoTable(EventTable[float]):
    """BPM (semiminime al minuto) per posizione; conversione posizione ↔ secondi."""

    def __init__(self, initial: float = 120.0, events=None) -> None:
        super().__init__(initial, events)

    def _changed(self) -> None:
        self._starts: list[float] | None = None  # secondi all'inizio di ogni evento (calcolati quando servono)

    def _cumulative(self) -> list[float]:
        """Secondi all'inizio di ogni cambio di tempo, sommati nello stesso ordine di prima
        (stessi valori in virgola mobile). 2026-11-20: precalcolati una volta per tabella;
        prima seconds() e position() scorrevano tutta la tabella a ogni chiamata, e con
        centinaia di cambi di tempo l'esecuzione di un brano richiedeva secondi."""
        if getattr(self, "_starts", None) is None:
            starts, total = [0.0], 0.0
            for ev, nxt in zip(self._events, self._events[1:]):
                total += float(nxt.at - ev.at) * 240.0 / ev.value
                starts.append(total)
            self._starts = starts
        return self._starts

    def seconds(self, pos: Fraction) -> float:
        """Secondi dall'inizio del brano alla posizione `pos`."""
        starts = self._cumulative()
        i = max(bisect_right(self._keys, pos) - 1, 0)
        if i and self._keys[i] == pos:  # esattamente su un cambio: come prima, fine del tratto precedente
            i -= 1
        ev = self._events[i]
        return starts[i] + float(pos - ev.at) * 240.0 / ev.value

    def position(self, seconds: float) -> Fraction:
        """Inverso approssimato (per il cursore): posizione alla data ora."""
        starts = self._cumulative()
        i = max(bisect_left(starts, seconds, 1) - 1, 0)  # primo tratto che finisce dopo `seconds`
        ev = self._events[i]
        return ev.at + Fraction((seconds - starts[i]) * ev.value / 240.0).limit_denominator(1 << 20)


class MeterTable(EventTable[TimeSignature]):
    """Metro per misura; conversione misura ↔ posizione."""

    def __init__(self, initial: TimeSignature = TimeSignature(), events=None) -> None:
        super().__init__(initial, events)

    def bar_start(self, bar: int) -> Fraction:
        pos, events = Fraction(0), list(self)
        for i, ev in enumerate(events):
            end = events[i + 1].at if i + 1 < len(events) else None
            if end is not None and bar > end:
                pos += (end - ev.at) * ev.value.bar_length
            else:
                return pos + (bar - ev.at) * ev.value.bar_length
        return pos

    def bar_at(self, pos: Fraction) -> int:
        """Misura che contiene la posizione."""
        start, events = Fraction(0), list(self)
        for i, ev in enumerate(events):
            end = events[i + 1].at if i + 1 < len(events) else None
            length = ev.value.bar_length
            if end is None or pos < start + (end - ev.at) * length:
                return ev.at + int((pos - start) // length)
            start += (end - ev.at) * length
        raise AssertionError("unreachable")

    def bar_ceil(self, pos: Fraction) -> int:
        """Prima stanghetta a partire da `pos` (compresa)."""
        bar = self.bar_at(pos)
        return bar if self.bar_start(bar) == pos else bar + 1


class KeyTable(EventTable[Key]):
    def __init__(self, initial: Key = C_MAJOR, events=None) -> None:
        super().__init__(initial, events)
