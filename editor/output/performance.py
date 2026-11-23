"""Strato 2 — esecuzione: brano → eventi in secondi, indirizzati per traccia.

Formato neutro: nessun canale MIDI, nessun dettaglio di backend.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from bisect import bisect_right
from fractions import Fraction

from ..core.instrument import Instrument
from ..core.chord_track import preview_notes
from ..core.model import PlacedNote, Song, Track


@dataclass(frozen=True)
class NoteOn:
    track_id: str
    key: int
    velocity: int


@dataclass(frozen=True)
class NoteOff:
    track_id: str
    key: int


@dataclass(frozen=True)
class Control:
    """Controller (es. 64 = pedale di sustain), per l'ingresso MIDI in thru."""

    track_id: str
    control: int
    value: int


@dataclass(frozen=True)
class PitchBend:
    track_id: str
    value: int  # -8192 … 8191


@dataclass(frozen=True)
class Pressure:
    """Aftertouch di canale."""

    track_id: str
    value: int


@dataclass(frozen=True)
class PolyPressure:
    """Aftertouch polifonico (per tasto)."""

    track_id: str
    key: int
    value: int


Event = NoteOn | NoteOff | Control | PitchBend | Pressure | PolyPressure
CONTROL_EVENTS = (Control, PitchBend, Pressure, PolyPressure)


def control_event(track_id: str, e) -> Event:
    """Evento della performance per un model.ControlEvent."""
    from ..core.model import BEND, CC, PRESSURE

    if e.kind == CC:
        return Control(track_id, e.number, e.value)
    if e.kind == BEND:
        return PitchBend(track_id, e.value)
    if e.kind == PRESSURE:
        return Pressure(track_id, e.value)
    return PolyPressure(track_id, e.number, e.value)


@dataclass(frozen=True)
class TimedEvent:
    time: float  # secondi dall'inizio del brano
    event: Event


@dataclass(frozen=True)
class TrackSetup:
    track_id: str
    instrument: Instrument
    output: str
    channel: int = 1  # canale MIDI della traccia, 1–16
    sounds: dict = field(default_factory=dict)  # nome dell'uscita → model.Sound (al posto del GM)


@dataclass
class Performance:
    tracks: list[TrackSetup]
    events: list[TimedEvent]  # ordinati per tempo; a parità, NoteOff prima di NoteOn
    duration: float


STRUM_DELAY = 0.02  # secondi fra una corda e la successiva nella pennata (arpeggiato)


def strum_delays(song: Song, track: Track, placed: list[PlacedNote] | None = None) -> dict[tuple[Fraction, int], float]:
    """Ritardo d'attacco (secondi) delle note di un accordo con la pennata: (inizio, altezza)
    → ritardo. Giù: dalla nota più grave alla più acuta; su: il contrario. Finiscono tutte
    insieme (la fine non cambia). `placed`: le note della traccia se già calcolate."""
    chords: dict[tuple[int, Fraction], list[PlacedNote]] = {}
    for p in placed if placed is not None else song.placed_notes(track):
        if p.note.strum in ("down", "up"):
            chords.setdefault((id(p.segment), p.start), []).append(p)
    delays = {}
    for notes in chords.values():
        order = sorted(notes, key=lambda p: p.midi, reverse=notes[0].note.strum == "up")
        for i, p in enumerate(order):
            delays[(p.start, p.midi)] = i * STRUM_DELAY
    return delays


def build_performance(song: Song) -> Performance:
    raw: list[tuple[float, int, Event]] = []
    for track in song.tracks:
        # 2026-11-20: le note della traccia si calcolano una volta sola (prima due: pennata e
        # suono), ed era circa metà del tempo di ogni ricostruzione dell'esecuzione
        placed = list(song.placed_notes(track))
        delays = strum_delays(song, track, placed)
        sounding = preview_notes(song, track) if track.kind == "chords" else _sounding_notes(song, track, placed)
        for start, end, midi, vel in sounding:
            if not 0 <= midi <= 127:
                continue
            off = song.tempos.seconds(end)
            on = song.tempos.seconds(start) + delays.get((start, midi), 0.0)
            raw.append((min(on, off - 0.001), 2, NoteOn(track.id, midi, vel)))
            raw.append((off, 0, NoteOff(track.id, midi)))
        if track.kind != "chords":  # controller, pitch bend, aftertouch (prima delle note nello stesso istante)
            for pos, e in song.placed_events(track):
                raw.append((song.tempos.seconds(pos), 1, control_event(track.id, e)))
    raw.sort(key=lambda r: (r[0], r[1]))
    return Performance(
        tracks=[TrackSetup(t.id, t.instrument, t.output, t.channel, dict(t.sounds)) for t in song.tracks],
        events=[TimedEvent(t, e) for t, _, e in raw],
        duration=song.duration_seconds,
    )


STACCATO = Fraction(1, 2)  # parte della durata scritta che suona una nota staccata


SLUR_OVERLAP = Fraction(1, 2)  # sotto slur: ogni nota suona fino a metà della nota successiva


def _slur_ends(song: Song, track: Track, placed: list[PlacedNote]) -> dict[int, Fraction]:
    """Fine al play delle note sotto slur (tranne l'ultima di ogni arco): fino all'attacco
    successivo + SLUR_OVERLAP della nota che lo segue; se la nota dopo ha la stessa
    altezza, solo fino al suo attacco (in MIDI la sovrapposizione la troncherebbe)."""
    ends: dict[int, Fraction] = {}
    if track.instrument.is_percussion:
        return ends
    by_segment: dict[int, list[PlacedNote]] = {}
    for p in placed:
        by_segment.setdefault(id(p.segment), []).append(p)
    for notes in by_segment.values():
        group = song.groups[notes[0].segment.group_id]
        by_id = {p.note.id: p for p in notes}
        for first, last in group.slurs:
            a, b = by_id.get(first), by_id.get(last)
            if a is None or b is None:
                continue
            inside = sorted((p for p in notes if a.start <= p.start <= b.start), key=lambda p: p.start)
            onsets = sorted({p.start for p in inside})
            for p in inside:
                later = [o for o in onsets if o > p.start]
                if not later:
                    continue  # ultima nota dell'arco: suona normale
                following = [q for q in inside if q.start == later[0]]
                if any(q.midi == p.midi for q in following):
                    ends[id(p.note)] = later[0]
                else:
                    ends[id(p.note)] = later[0] + min(q.duration for q in following) * SLUR_OVERLAP
    return ends


DRUM_RING = 3.0  # secondi: sulla batteria un colpo suona fino a quando il campione si spegne da solo


def _drum_end(song: Song, start: Fraction, next_same: Fraction | None) -> Fraction:
    """Fine al play di un colpo di batteria: ignora la durata scritta (i piatti non vengono
    troncati); si ferma prima solo se lo stesso suono viene ricolpito."""
    ring = song.tempos.position(song.tempos.seconds(start) + DRUM_RING)
    return min(ring, next_same) if next_same is not None else ring


def _sounding_notes(song: Song, track: Track, placed: list[PlacedNote] | None = None):
    """Note come suonano: le catene di note legate diventano un solo suono; lo staccato
    suona a metà; sotto slur le note si sovrappongono un po' alla successiva.
    `placed`: le note della traccia se già calcolate."""
    placed = list(song.placed_notes(track)) if placed is None else placed
    slur_ends = _slur_ends(song, track, placed)
    drums = track.instrument.is_percussion or track.channel == 10
    hits: dict[int, list[Fraction]] = {}  # batteria: attacchi per suono, per trovare il colpo successivo
    if drums:
        for q in placed:
            hits.setdefault(q.midi, []).append(q.start)
        for starts in hits.values():
            starts.sort()
    by_start: dict[tuple[Fraction, int], PlacedNote] = {(p.start, p.midi): p for p in placed}
    absorbed: set[int] = set()
    for p in placed:
        if id(p) in absorbed:
            continue
        end, cur = p.end, p
        while cur.note.tie_next and (nxt := by_start.get((cur.end, cur.midi))) is not None:
            absorbed.add(id(nxt))
            end, cur = nxt.end, nxt
        if cur.note.staccato:  # staccato: l'ultima nota (della catena) suona metà della sua durata
            end = cur.start + cur.duration * STACCATO
        elif id(cur.note) in slur_ends:  # sotto slur: fino a metà della nota dopo
            end = max(end, slur_ends[id(cur.note)])
        if drums:
            starts = hits[p.midi]
            i = bisect_right(starts, p.start)
            end = _drum_end(song, p.start, starts[i] if i < len(starts) else None)
        yield p.start, end, p.midi, p.note.velocity


METRONOME = "metronome"  # traccia del metronomo della registrazione (canale 10)
CLICK_KEY = 37  # Side Stick
CLICK_ACCENT, CLICK_VELOCITY = 127, 80


def metronome_setup() -> TrackSetup:
    from ..core.instrument import GMInstrument

    return TrackSetup(METRONOME, GMInstrument(0, percussion=True), "default", 10)


def with_metronome(song: Song, start: Fraction, end: Fraction) -> Performance:
    """Esecuzione del brano più i colpi del metronomo (Side Stick, accento sul primo
    movimento) da `start` a `end`; la durata arriva almeno a `end` (si registra anche
    oltre la fine del brano)."""
    perf = build_performance(song)
    clicks = []
    pos = start
    while pos < end:
        bar, off = song.split_position(pos)
        beat = song.meters.at(bar).beat
        on = song.tempos.seconds(pos)
        clicks.append((on, 1, NoteOn(METRONOME, CLICK_KEY, CLICK_ACCENT if off == 0 else CLICK_VELOCITY)))
        clicks.append((song.tempos.seconds(pos + beat / 4), 0, NoteOff(METRONOME, CLICK_KEY)))
        pos = song.meters.bar_start(bar) + (int(off // beat) + 1) * beat
    order = {NoteOff: 0, NoteOn: 2}
    raw = [(e.time, order.get(type(e.event), 1), e.event) for e in perf.events] + [(t, 2 * k, e) for t, k, e in clicks]
    raw.sort(key=lambda r: (r[0], r[1]))
    return Performance(perf.tracks + [metronome_setup()], [TimedEvent(t, e) for t, _, e in raw],
                       max(perf.duration, song.tempos.seconds(end)))
