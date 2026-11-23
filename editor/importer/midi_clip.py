"""Segment > Import MIDI…: un tratto di un file MIDI (un canale, da battuta a battuta)
diventa un segmento di una traccia del brano aperto. Niente GTK: la logica sta qui.

Decisioni dell'utente (2026-11-20):
- solo file MIDI; se il file ha più canali se ne sceglie uno, altrimenti si prende tutto;
- il segmento parte all'inizio di una misura (snap alla misura);
- se si sovrappone a un segmento della traccia, l'import si rifiuta (errore);
- la preview suona con il suono della traccia di destinazione e al tempo del brano.

Il file passa dall'import MIDI normale (tick → Fraction esatte, kit Yamaha → GM,
staccato riconosciuto), poi si ritaglia il tratto scelto.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from fractions import Fraction
from pathlib import Path

from ..core.model import ControlEvent, Group, Note, Segment, Song, Track, new_id
from ..core.timeline import MeterTable, TempoTable
from .midi import import_midi

MIDI_SUFFIXES = (".mid", ".midi")


@dataclass(frozen=True)
class ClipChannel:
    channel: int  # 1–16
    name: str  # nome della traccia importata ("10 Drums", "2 Electric Bass (finger)")
    notes: int


@dataclass
class ClipSource:
    """File MIDI letto, pronto per ritagliarne un tratto."""

    path: Path
    song: Song
    channels: list[ClipChannel]

    @property
    def bars(self) -> int:
        return self.song.bar_count

    def summary(self) -> str:
        """Riga informativa del file: battute, metro, tempo, canali (testo in inglese per la UI)."""
        n = len(self.channels)
        return (f"{self.bars} {'bar' if self.bars == 1 else 'bars'} · {self.song.meters.initial} · "
                f"{self.song.tempos.initial:g} BPM · {n} {'channel' if n == 1 else 'channels'}")


def is_midi_file(path: str | Path) -> bool:
    return Path(path).suffix.lower() in MIDI_SUFFIXES


def read_clip_source(path: str | Path) -> ClipSource:
    """Legge il file e ne elenca i canali con note (in ordine di canale)."""
    song = import_midi(path)
    counts: dict[int, list] = {}
    for track in song.tracks:
        notes = sum(len(song.groups[s.group_id].notes) for s in track.segments)
        if notes:
            # più tracce MIDI sullo stesso canale: un solo canale, nome della prima
            entry = counts.setdefault(track.channel, [track.name, 0])
            entry[1] += notes
    channels = [ClipChannel(ch, name, n) for ch, (name, n) in sorted(counts.items())]
    if not channels:
        raise ValueError("no notes in this MIDI file")
    return ClipSource(Path(path), song, channels)


def clip_group(src: ClipSource, channel: int, first_bar: int, last_bar: int) -> tuple[Group, Fraction]:
    """Gruppo con le note e gli eventi di controllo del canale dalla battuta `first_bar` alla
    `last_bar` compresa (0-based, metro del file) e la sua durata in semibrevi. Entrano le
    note che iniziano nel tratto; quelle che vanno oltre la fine si accorciano."""
    if not 0 <= first_bar <= last_bar:
        raise ValueError("invalid bar range")
    meters = src.song.meters
    start, end = meters.bar_start(first_bar), meters.bar_start(last_bar + 1)
    notes: list[Note] = []
    events: list[ControlEvent] = []
    for track in src.song.tracks:
        if track.channel != channel:
            continue
        for p in src.song.placed_notes(track):
            if start <= p.start < end:
                notes.append(Note(start=p.start - start, duration=min(p.duration, end - p.start), midi=p.midi,
                                  velocity=p.note.velocity, staccato=p.note.staccato))
        for pos, e in src.song.placed_events(track):
            if start <= pos < end:
                events.append(replace(e, start=pos - start, id=new_id()))
    notes.sort(key=lambda n: (n.start, n.midi))
    events.sort(key=lambda e: e.start)
    group = Group(name=src.path.stem, length_bars=last_bar - first_bar + 1, notes=notes, events=events)
    return group, end - start


def clip_segment(group: Group, length: Fraction, bar: int) -> Segment:
    """Segmento del tratto, all'inizio della misura `bar` (0-based) del brano; etichetta =
    nome del file, per riconoscere il groove fra migliaia."""
    return Segment(group_id=group.id, start_bar=bar, length=length, label=group.name)


def preview_song(group: Group, length: Fraction, track: Track, song: Song, bar: int) -> Song:
    """Brano usa e getta per la preview: solo il tratto, sul suono della traccia di
    destinazione (strumento, canale, uscita, suoni per uscita) e al tempo del brano nella
    misura di destinazione. La traccia ha un id nuovo: suona anche se quella vera è in Mute."""
    pos = song.meters.bar_start(bar)
    meter = song.meters.at(bar)
    preview = Song(title="preview", tempos=TempoTable(song.tempos.at(pos)), meters=MeterTable(meter))
    preview.groups[group.id] = group
    preview.tracks.append(Track(name=track.name, instrument=track.instrument, clef=track.clef,
                                segments=[Segment(group.id, 0, length)], output=track.output,
                                channel=track.channel, sounds=dict(track.sounds)))
    return preview
