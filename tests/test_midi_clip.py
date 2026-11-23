"""Segment > Import MIDI…: ritaglio di un tratto di un file MIDI in un segmento."""

from fractions import Fraction as F

import mido
import pytest

from editor.core.document import AddSegments
from editor.core.model import CC, Group, Segment, Song, Track
from editor.importer.midi_clip import clip_group, clip_segment, preview_song, read_clip_source
from editor.output.performance import NoteOn, build_performance

TPQ = 480
BAR = 4 * TPQ


def _write_groove(path) -> None:
    """4 battute 4/4 a 96 BPM: cassa (36) su ogni battito sul canale 10, basso (40) una
    semibreve per battuta sul canale 2, un CC 1 sul canale 10 insieme al 7° colpo (terzo
    battito della seconda battuta)."""
    mid = mido.MidiFile(type=1, ticks_per_beat=TPQ)
    conductor = mido.MidiTrack([
        mido.MetaMessage("set_tempo", tempo=mido.bpm2tempo(96), time=0),
        mido.MetaMessage("time_signature", numerator=4, denominator=4, time=0),
    ])
    drums = mido.MidiTrack()
    for i in range(16):
        drums.append(mido.Message("note_on", channel=9, note=36, velocity=100, time=0 if i == 0 else TPQ - 60))
        if i == 6:  # insieme al 7° colpo
            drums.append(mido.Message("control_change", channel=9, control=1, value=64, time=0))
        drums.append(mido.Message("note_off", channel=9, note=36, time=60))
    bass = mido.MidiTrack()
    for i in range(4):
        bass.append(mido.Message("note_on", channel=1, note=40, velocity=90, time=0))
        bass.append(mido.Message("note_off", channel=1, note=40, time=BAR))
    mid.tracks += [conductor, drums, bass]
    mid.save(path)


@pytest.fixture
def groove(tmp_path):
    path = tmp_path / "Funk 02 Groove B.mid"
    _write_groove(path)
    return read_clip_source(path)


def test_source_lists_channels_and_summary(groove):
    assert [(c.channel, c.notes) for c in groove.channels] == [(2, 4), (10, 16)]
    assert groove.bars == 4
    assert groove.summary() == "4 bars · 4/4 · 96 BPM · 2 channels"


def test_clip_takes_channel_and_bar_range(groove):
    # battute 2–3 (0-based 1..2) del canale 10: 8 colpi, posizioni relative al tratto
    group, length = clip_group(groove, 10, 1, 2)
    assert length == F(2)
    assert group.name == "Funk 02 Groove B" and group.length_bars == 2
    assert [n.start for n in group.notes] == [F(i, 4) for i in range(8)]
    assert {n.midi for n in group.notes} == {36}
    # il CC 1 sta al terzo battito della seconda battuta del file = 1/2 nel tratto
    assert [(e.kind, e.number, e.value, e.start) for e in group.events] == [(CC, 1, 64, F(1, 2))]


def test_clip_cuts_notes_at_the_end(groove):
    # basso: semibreve per battuta; tratto di una battuta → una nota lunga quanto la battuta
    group, length = clip_group(groove, 2, 3, 3)
    assert length == F(1)
    assert [(n.start, n.duration, n.midi) for n in group.notes] == [(F(0), F(1), 40)]


def test_invalid_range(groove):
    with pytest.raises(ValueError):
        clip_group(groove, 10, 2, 1)


def test_segment_snaps_to_bar_and_refuses_overlap(groove):
    song = Song()
    existing = Group("old", 1)
    song.groups[existing.id] = existing
    track = Track("DRUM", segments=[Segment(existing.id, 4, F(1))], channel=10)
    song.tracks.append(track)
    group, length = clip_group(groove, 10, 0, 1)
    seg = clip_segment(group, length, 2)
    assert (seg.start_bar, seg.offset, seg.length, seg.label) == (2, F(0), F(2), "Funk 02 Groove B")
    assert AddSegments.fits(song, [(track, seg, group)])
    assert not AddSegments.fits(song, [(track, clip_segment(group, length, 3), group)])  # 3–5 tocca la 4


def test_preview_plays_on_target_sound_at_song_tempo(groove):
    song = Song()
    song.tempos.set(F(8), 140.0)
    track = Track("DRUM", channel=10, mute=True)
    song.tracks.append(track)
    group, length = clip_group(groove, 10, 0, 0)
    preview = preview_song(group, length, track, song, 8)
    assert preview.tempos.initial == 140.0
    ptrack = preview.tracks[0]
    assert ptrack.id != track.id and ptrack.channel == 10 and not ptrack.mute
    perf = build_performance(preview)
    ons = [e for e in perf.events if isinstance(e.event, NoteOn)]
    assert len(ons) == 4
    assert ons[1].time == pytest.approx(60 / 140)  # un battito a 140 BPM
