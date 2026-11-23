"""Registrazione dall'ingresso MIDI (punch-in nello spazio libero di una traccia).

- Si parte dall'inizio della misura del punto di play, solo fuori dai segmenti della
  traccia; il limite è l'inizio del segmento successivo (o nessuno).
- Le note catturate (posizione esatta nel brano, velocity) diventano un segmento nuovo
  dalla misura di partenza all'ultima suonata, senza superare il limite: le note oltre
  il limite si tagliano (quelle che iniziano dopo si perdono). Nessuna quantizzazione.
  Controller (sustain compreso), pitch bend e aftertouch si registrano come eventi;
  l'ultimo valore arrivato dopo la fine (es. pedale su) va in fondo al segmento.
"""

from __future__ import annotations

from fractions import Fraction

from .model import ControlEvent, Group, Note, Segment, Song, Track


def record_window(song: Song, track: Track, pos: Fraction) -> tuple[Fraction, Fraction | None] | None:
    """(inizio della registrazione, limite) per il punto di play `pos`: inizio della sua
    misura; limite = inizio del primo segmento dopo. None se `pos` (o l'inizio della sua
    misura) cade dentro un segmento della traccia."""
    start = song.meters.bar_start(song.meters.bar_at(pos))
    limit = None
    for seg in track.segments:
        a, b = song.segment_span(seg)
        if a <= pos < b or a <= start < b:
            return None
        if a >= start and (limit is None or a < limit):
            limit = a
    return start, limit


def build_take(song: Song, start: Fraction, limit: Fraction | None, stop: Fraction,
               events: list[tuple[Fraction, int, int | None]], name: str = "Rec",
               controls: list[tuple[Fraction, str, int, int]] | None = None) -> tuple[Segment, Group] | None:
    """Segmento registrato. `events`: (posizione, tasto, velocity; None = rilascio) in
    ordine di arrivo; `stop`: dove la registrazione è finita (note ancora tenute: fino lì).
    `controls`: (posizione, tipo, numero, valore) di controller, pitch bend, aftertouch."""
    end_cap = min(stop, limit) if limit is not None else stop
    held: dict[int, tuple[Fraction, int]] = {}
    notes: list[Note] = []

    def close(key: int, at: Fraction) -> None:
        on, vel = held.pop(key)
        off = min(at, end_cap)
        if on < end_cap and off > on:
            notes.append(Note(on - start, off - on, key, vel))

    for pos, key, vel in events:
        pos = max(pos, start)
        if vel is None:
            if key in held:
                close(key, pos)
        else:
            if key in held:  # ribattuta senza rilascio: chiude la precedente
                close(key, pos)
            held[key] = (pos, vel)
    for key in list(held):
        close(key, end_cap)
    if not notes:
        return None
    notes.sort(key=lambda n: (n.start, n.midi))
    last = max(start + n.end for n in notes)
    end = song.meters.bar_start(song.meters.bar_ceil(last))
    if limit is not None:
        end = min(end, limit)
    first_bar = song.meters.bar_at(start)
    inside = [ControlEvent(max(pos, start) - start, kind, number, value)
              for pos, kind, number, value in controls or [] if pos < end]
    after: dict[tuple[str, int], tuple[str, int, int]] = {}  # ultimo valore arrivato dopo la fine
    for pos, kind, number, value in controls or []:
        if pos >= end:
            after[(kind, number)] = (kind, number, value)
    tail = end - start - Fraction(1, 1920)  # in fondo al segmento: pedale su, pitch bend al centro…
    inside += [ControlEvent(tail, kind, number, value) for kind, number, value in after.values()]
    group = Group(name, max(song.meters.bar_ceil(end) - first_bar, 1), notes, events=inside)
    return Segment(group.id, first_bar, end - start, label=name), group
