from fractions import Fraction as F

import mido
import pytest

from editor.core.clef import BASS, TREBLE
from editor.core.model import Note
from editor.engraving.layout import Figure, Glyph, decompose, display_span, engrave
from editor.importer.midi import import_midi
from editor.output.backend import MidiChannelMap, RecordingBackend
from editor.output.performance import NoteOff, NoteOn, build_performance
from editor.output.transport import State, Transport

TPQ = 480


def make_midi() -> mido.MidiFile:
    mid = mido.MidiFile(type=1, ticks_per_beat=TPQ)
    conductor = mido.MidiTrack([
        mido.MetaMessage("set_tempo", tempo=mido.bpm2tempo(60), time=0),
        mido.MetaMessage("time_signature", numerator=3, denominator=4, time=0),
        mido.MetaMessage("key_signature", key="Bm", time=0),
        mido.MetaMessage("set_tempo", tempo=mido.bpm2tempo(120), time=3 * TPQ),
    ])
    melody = mido.MidiTrack([
        mido.MetaMessage("track_name", name="Flauto", time=0),
        mido.Message("program_change", program=73, time=0),
        mido.Message("note_on", note=70, velocity=90, time=0),
        mido.Message("note_off", note=70, time=TPQ // 3),  # croma di terzina
        mido.Message("note_on", note=71, velocity=80, time=0),
        mido.Message("note_on", note=71, velocity=0, time=TPQ + 7),  # durata "suonata"
    ])
    bass = mido.MidiTrack([
        mido.Message("note_on", channel=1, note=40, velocity=90, time=3 * TPQ),
        mido.Message("note_off", channel=1, note=40, time=3 * TPQ),
    ])
    drums = mido.MidiTrack([
        mido.Message("note_on", channel=9, note=36, velocity=100, time=0),
        mido.Message("note_off", channel=9, note=36, time=TPQ // 4),
    ])
    mid.tracks += [conductor, melody, bass, drums]
    return mid


def test_import_tables_tracks_and_exact_times():
    song = import_midi(make_midi())
    assert [(e.at, e.value) for e in song.tempos] == [(0, 60.0), (F(3, 4), 120.0)]
    assert str(song.meters.initial) == "3/4" and str(song.keys.initial) == "Bm"
    flute, bass, drums = song.tracks
    assert flute.name == "1 Flute" and flute.instrument.program == 73 and flute.clef is TREBLE
    assert flute.channel == 1 and bass.channel == 2 and drums.name == "10 Drums"
    assert bass.name == "Track 2"  # nessun program change sul canale 2
    assert bass.clef is BASS and bass.segments[0].start_bar == 1
    assert drums.instrument.is_percussion
    notes = song.groups[flute.segments[0].group_id].notes
    assert notes[0].duration == F(1, 12)  # terzina esatta, nessuna quantizzazione
    assert notes[1].duration == F(TPQ + 7, TPQ * 4)
    assert str(song.spell(next(song.placed_notes(flute)))) == "A#4"


def test_performance_uses_tempo_map_and_merges_ties():
    song = import_midi(make_midi())
    flute = song.tracks[0]
    perf = build_performance(song)
    ons = [e for e in perf.events if isinstance(e.event, NoteOn)]
    assert ons[0].time == 0 and ons[0].event.track_id == flute.id
    bass_on = next(e for e in ons if e.event.key == 40)
    assert bass_on.time == pytest.approx(3.0)  # 3 semiminime a 60 BPM
    # due note legate suonano come una sola
    group = song.groups[flute.segments[0].group_id]
    group.notes = [Note(F(0), F(1, 4), 60, tie_next=True), Note(F(1, 4), F(1, 4), 60)]
    evs = [e for e in build_performance(song).events if e.event.track_id == flute.id]
    assert [type(e.event) for e in evs] == [NoteOn, NoteOff]


def test_channel_map_percussion_on_channel_10():
    song = import_midi(make_midi())
    perf = build_performance(song)
    cmap = MidiChannelMap(perf.tracks)
    assert cmap.channel[song.tracks[2].id] == 9 and cmap.program[9][1]  # batteria
    assert [cmap.channel[t.id] for t in song.tracks[:2]] == [0, 1]  # il canale della traccia
    song.tracks[1].channel = 1  # stesso canale della prima: lo condividono
    cmap = MidiChannelMap(build_performance(song).tracks)
    assert cmap.channel[song.tracks[1].id] == 0 and cmap.program[0] == (73, False)


class FakeClock:
    def __init__(self) -> None:
        self.t = 0.0

    def now(self) -> float:
        return self.t


def test_transport_dispatch_pause_seek_end():
    song = import_midi(make_midi())
    backend, clock = RecordingBackend(), FakeClock()
    tr = Transport(backend, clock, threaded=False)
    tr.load(build_performance(song))
    tr.play()
    tr.tick()
    assert {e.key for e in backend.sent if isinstance(e, NoteOn)} == {70, 36}
    clock.t = 1.0
    tr.pause()
    assert tr.state is State.PAUSED and tr.position == pytest.approx(1.0) and backend.panics >= 1
    clock.t = 50.0
    tr.play()
    assert tr.position == pytest.approx(1.0)
    tr.seek(2.9)
    clock.t = 50.2
    tr.tick()
    assert any(isinstance(e, NoteOn) and e.key == 40 for e in backend.sent)
    clock.t = 100.0
    assert tr.tick() is None and tr.state is State.STOPPED


def test_mute_solo_silence_tracks_during_playback():
    song = import_midi(make_midi())
    flute, bass, drums = song.tracks
    bass.solo = True
    assert song.silent_track_ids() == {flute.id, drums.id}
    bass.mute = flute.mute = True  # Solo vince sempre
    assert song.silent_track_ids() == {flute.id, drums.id}
    flute.mute = False
    bass.mute = bass.solo = False
    backend, clock = RecordingBackend(), FakeClock()
    tr = Transport(backend, clock, threaded=False)
    tr.load(build_performance(song))
    tr.play()
    tr.tick()  # partono flauto (70) e batteria (36)
    flute.mute = True
    tr.set_silenced(song.silent_track_ids())  # durante il playback: la nota del flauto si ferma subito
    assert any(isinstance(e, NoteOff) and e.track_id == flute.id and e.key == 70 for e in backend.sent)
    clock.t = 0.5  # oltre l'inizio della seconda nota del flauto (71)
    tr.tick()
    assert not any(isinstance(e, NoteOn) and e.track_id == flute.id and e.key == 71 for e in backend.sent)
    flute.mute = False
    tr.set_silenced(song.silent_track_ids())
    tr.seek(0.0)
    clock.t = 0.6
    tr.tick()
    assert sum(isinstance(e, NoteOn) and e.key == 70 for e in backend.sent) == 2  # risuona


def test_display_rounding_and_decomposition():
    assert display_span(F(1, 12), F(1, 4)) == (F(1, 12), F(1, 4), False)
    s, e, red = display_span(F(0), F(TPQ + 7, TPQ * 4))
    assert red and (s, e) == (0, F(1, 4))
    assert decompose(F(0), F(3, 8), F(1, 4)) == [(Figure(F(1, 4), 1), F(3, 8))]
    assert decompose(F(1, 8), F(1, 4), F(1, 4)) == [(Figure(F(1, 8)), F(1, 8)), (Figure(F(1, 8)), F(1, 8))]
    assert decompose(F(0), F(1, 12), F(1, 4)) == [(Figure(F(1, 8), 0, True), F(1, 12))]


def test_engrave_marks_overlapping_notes_red():
    song = import_midi(make_midi())
    flute = song.tracks[0]
    group = song.groups[flute.segments[0].group_id]
    # La lunga (accorciata a video dall'attacco del Do) + Do: solo il La è rosso
    group.notes = [Note(F(0), F(1, 2), 69), Note(F(1, 4), F(1, 4), 72)]
    heads = [it for it in engrave(song, flute, width=80).items
             if isinstance(it, Glyph) and it.name.startswith("notehead")]
    assert [h.red for h in heads[:2]] == [True, False]


def test_engrave_marks_rounded_notes_red():
    song = import_midi(make_midi())
    layout = engrave(song, song.tracks[0], width=80)
    heads = [it for it in layout.items if isinstance(it, Glyph) and it.name.startswith("notehead")]
    assert heads and not heads[0].red and any(h.red for h in heads)
    assert any(isinstance(it, Glyph) and it.name == "accidentalSharp" for it in layout.items)
    assert layout.locate(F(0)) is not None


def test_layout_bar_numbers_and_click_position():
    song = import_midi(make_midi())
    layout = engrave(song, song.tracks[0], width=80)
    assert [b for sys in layout.systems for b, _x in sys.bars] == list(range(song.bar_count))
    sys = layout.systems[0]
    assert sys.x_at(sys.x_map[1][0]) == sys.x_map[1][1]
    (p0, x0), (p1, x1) = sys.x_map[0], sys.x_map[1]
    assert layout.position_at(x0 - 5, sys.top + 2) == p0
    mid = layout.position_at((x0 + x1) / 2, sys.top + 2)
    assert p0 < mid < p1


def test_engrave_multiple_staves_aligned_and_bar_range():
    from editor.importer.midi import import_midi as imp

    song = imp("examples/demo.mid")
    melody, chords, bass = song.tracks[:3]
    layout = engrave(song, [melody, bass], first_bar=1, last_bar=3)
    assert len(layout.systems) == 1  # rigo continuo
    sys = layout.systems[0]
    assert len(sys.staff_tops) == 2 and sys.first_bar == 1 and sys.last_bar == 3
    assert [b for b, _x in sys.bars] == [1, 2]
    # a ogni posizione corrisponde una sola x per tutti i righi
    positions = [p for p, _x in sys.x_map]
    assert positions == sorted(positions)
    heads = [it for it in layout.items if isinstance(it, Glyph) and it.name.startswith("notehead")]
    top_heads = [h for h in heads if h.y < sys.staff_tops[1] - 2]
    bottom_heads = [h for h in heads if h.y >= sys.staff_tops[1] - 2]
    assert top_heads and bottom_heads
    # la semibreve del basso a inizio misura sta sulla stessa colonna della prima nota della melodia
    bar_start_x = dict(sys.x_map)[song.meters.bar_start(1)]
    assert any(abs(h.x - bar_start_x) < 1e-6 for h in bottom_heads)
    assert any(abs(h.x - bar_start_x) < 1e-6 for h in top_heads)




def test_import_splits_segments_at_empty_bars():
    # 4/4: note nelle misure 1–2, misura 3 vuota, misura 4, misura 5 vuota,
    # nota tenuta dalla 6 alla 7 e nota nella 8 → tre segmenti
    bar = 4 * TPQ
    mid = mido.MidiFile(type=1, ticks_per_beat=TPQ)
    events = [(0, bar * 2, 60), (bar * 3, bar * 4, 62), (bar * 5, bar * 7, 64), (bar * 7, bar * 8, 65)]
    track, last = mido.MidiTrack(), 0
    for on, off, n in events:
        track.append(mido.Message("note_on", note=n, velocity=80, time=on - last))
        track.append(mido.Message("note_off", note=n, time=off - on))
        last = off
    mid.tracks.append(track)
    song = import_midi(mid)
    (t,) = song.tracks
    assert [song.segment_bars(s) for s in t.segments] == [(0, 2), (3, 4), (5, 8)]
    assert len({s.group_id for s in t.segments}) == 3
    # le note restano alla stessa posizione assoluta
    assert [(p.start, p.midi) for p in song.placed_notes(t)] == [(0, 60), (3, 62), (5, 64), (7, 65)]


def test_staccato_plays_half_and_keeps_written_length():
    song = import_midi(make_midi())
    flute = song.tracks[0]
    note = song.groups[flute.segments[0].group_id].notes[1]
    written = note.duration
    before = [e for e in build_performance(song).events if e.event.track_id == flute.id]
    note.staccato = True
    after = [e for e in build_performance(song).events if e.event.track_id == flute.id]
    assert note.duration == written  # la durata scritta non cambia
    on = next(e.time for e in after if isinstance(e.event, NoteOn) and e.event.key == 71)
    off = next(e.time for e in after if isinstance(e.event, NoteOff) and e.event.key == 71)
    off_before = next(e.time for e in before if isinstance(e.event, NoteOff) and e.event.key == 71)
    assert off - on == pytest.approx((off_before - on) / 2)
    copy = note.copy(start=F(0))
    assert copy.staccato and copy.id != note.id


def test_staccato_dot_in_notation():
    from editor.engraving.layout import Glyph, engrave

    song = import_midi(make_midi())
    flute = song.tracks[0]
    names = lambda: [it.name for it in engrave(song, flute).items if isinstance(it, Glyph)]  # noqa: E731
    assert not any(n.startswith("articStaccato") for n in names())
    song.groups[flute.segments[0].group_id].notes[0].staccato = True
    assert sum(n.startswith("articStaccato") for n in names()) == 1


def test_import_detects_musescore_staccato():
    # come l'export di MuseScore: 4 semiminime staccate = note da 239 tick ogni 480
    mid = mido.MidiFile(type=1, ticks_per_beat=480)
    events, t = [], 0
    for _ in range(4):
        events += [mido.Message("note_on", note=65, velocity=80, time=0),
                   mido.Message("note_on", note=65, velocity=0, time=239)]
        events[-2].time = 0 if not t else 241
        t += 1
    mid.tracks.append(mido.MidiTrack(events))
    song = import_midi(mid)
    notes = song.groups[song.tracks[0].segments[0].group_id].notes
    assert [(n.staccato, n.duration) for n in notes] == [(True, F(1, 4))] * 4  # anche l'ultima (fine misura)


def test_project_roundtrip(tmp_path):
    from editor.core.model import Segment
    from editor.core.project import load_project, save_project, song_to_dict

    song = import_midi("examples/demo.mid")
    t = song.tracks[0]
    seg = t.segments[0]
    note = song.groups[seg.group_id].notes[0]
    note.staccato, note.spelling = True, note.spelling
    t.mute, t.solo, t.channel, t.velocity = True, True, 5, 90
    seg.label = "Tema"
    t.segments.append(Segment(seg.group_id, 20, seg.length, clone_of=seg.id))  # un clone
    path = tmp_path / "brano.music"
    save_project(song, path)
    again = load_project(path)
    assert song_to_dict(again) == song_to_dict(song)
    assert again.tracks[0].segments[-1].clone_of == seg.id


def test_export_midi_plays_like_the_song(tmp_path):
    from editor.exporter.midi import export_midi

    song = import_midi("examples/small.mid")
    bass = song.tracks[2]
    notes = song.groups[bass.segments[0].group_id].notes
    for n in notes:
        n.staccato = True
    path = tmp_path / "out.mid"
    export_midi(song, path)
    again = import_midi(path)
    placed = lambda s, t: [(p.start, p.midi) for p in s.placed_notes(t)]  # noqa: E731
    for a, b in zip(song.tracks, again.tracks):
        assert placed(song, a) == placed(again, b) and a.name == b.name and a.channel == b.channel
    back = again.groups[again.tracks[2].segments[0].group_id].notes
    assert all(n.staccato for n in back) and [n.duration for n in back] == [n.duration for n in notes]
    assert again.tracks[0].segments[0].label == "Quantize 1/8"
    assert [(e.at, e.value) for e in again.tempos] == [(e.at, e.value) for e in song.tempos]


def test_model_tie_drawn_in_notation():
    from editor.engraving.layout import Tie, engrave

    song = import_midi("examples/small.mid")
    bass = song.tracks[2]
    notes = song.groups[bass.segments[0].group_id].notes
    ties = lambda: sum(isinstance(it, Tie) for it in engrave(song, bass).items)  # noqa: E731
    before = ties()
    notes[1].midi = notes[2].midi  # due note uguali che si toccano
    notes[1].tie_next = True
    assert ties() == before + 1


def test_auto_beam_groups_half_bar_in_4_4():
    from editor.engraving.layout import Line, beam_groups, engrave

    song = import_midi("examples/small.mid")
    bass = song.tracks[2]  # misura 9: 8 crome in 4/4
    layout = engrave(song, bass, first_bar=8, last_bar=9)
    flags = [it for it in layout.items if getattr(it, "name", "").startswith("flag")]
    assert flags == []  # tutte con travatura
    from editor.engraving.layout import _bar_pieces, _chords
    pieces, _ = _bar_pieces(song, _chords(song, bass), 8, 0)
    assert [len(g) for g in beam_groups(song, 8, pieces)] == [4, 4]
    beams = [it for it in layout.items if isinstance(it, Line) and abs(it.y1 - it.y2) < 1e-9 and it.thickness == 0.5]
    assert len(beams) == 2


def test_slur_overlaps_half_of_next_note():
    from editor.output.performance import _sounding_notes

    song = import_midi("examples/small.mid")
    t = song.tracks[0]
    g = song.groups[t.segments[0].group_id]
    a, b, c = g.notes[0], g.notes[1], g.notes[2]
    g.slurs = [(a.id, c.id)]
    by = {(s, m): e for s, e, m, _v in _sounding_notes(song, t)}
    pa = next(p for p in song.placed_notes(t) if p.note is a)
    pb = next(p for p in song.placed_notes(t) if p.note is b)
    pc = next(p for p in song.placed_notes(t) if p.note is c)
    assert by[(pa.start, pa.midi)] == pb.start + pb.duration / 2  # fino a metà della dopo
    assert by[(pc.start, pc.midi)] == pc.end  # l'ultima suona normale


def test_slur_drawn_in_notation():
    from editor.core.project import load_project
    from editor.engraving.layout import Slur, engrave

    song = load_project("examples/slur-test.music")
    flute = song.tracks[1]
    slurs = [it for it in engrave(song, flute, first_bar=1, last_bar=2).items if isinstance(it, Slur)]
    assert len(slurs) == 1 and slurs[0].x2 > slurs[0].x1


def test_noteheads_know_their_notes():
    from editor.engraving.layout import Glyph, engrave

    song = import_midi("examples/small.mid")
    bass = song.tracks[2]
    heads = [it for it in engrave(song, bass, first_bar=8, last_bar=9).items if isinstance(it, Glyph) and it.keys]
    seg = bass.segments[0]
    ids = {n.id for n in song.groups[seg.group_id].notes}
    assert len(heads) == 8 and all(k[0] == seg.id and k[1] in ids for h in heads for k in h.keys)


def test_long_triplets_are_not_split_at_the_beat():
    from editor.engraving.layout import decompose

    q = F(1, 4)
    for offset in (F(0), F(1, 6), F(1, 3)):  # terzine di semiminima (gruppo di 2/4)
        (fig, v), = decompose(offset, F(1, 6), q)
        assert fig.triplet and fig.base == F(1, 4) and v == F(1, 6)
    for offset in (F(0), F(1, 3), F(2, 3)):  # terzine di minima (gruppo di una misura 4/4)
        (fig, v), = decompose(offset, F(1, 3), q)
        assert fig.triplet and fig.base == F(1, 2) and v == F(1, 3)


def test_strum_delays_playback_and_arpeggio_sign():
    from editor.core.model import Group, Note, Segment, Song, Track
    from editor.engraving.layout import Glyph, engrave
    from editor.output.performance import STRUM_DELAY, build_performance

    notes = [Note(F(0), F(1, 4), m, strum="down") for m in (52, 48, 55)]
    g = Group("g", 1, notes)
    t = Track("t", segments=[Segment(g.id, 0, F(1))])
    song = Song(tracks=[t], groups={g.id: g})
    ons = {e.event.key: e.time for e in build_performance(song).events if isinstance(e.event, NoteOn)}
    assert ons[48] == 0 and ons[52] == pytest.approx(STRUM_DELAY) and ons[55] == pytest.approx(2 * STRUM_DELAY)
    offs = {e.time for e in build_performance(song).events if isinstance(e.event, NoteOff)}
    assert len(offs) == 1  # finiscono insieme
    assert any(isinstance(it, Glyph) and it.name == "stringsDownBow" for it in engrave(song, t).items)


def test_drum_hits_ring_until_next_same_hit():
    from editor.core.instrument import GMInstrument
    from editor.core.model import Group, Note, Segment, Song, Track
    from editor.output.performance import DRUM_RING, build_performance

    g = Group("g", 2, [Note(F(0), F(1, 4), 49), Note(F(0), F(1, 4), 42), Note(F(1, 8), F(1, 4), 42)])
    t = Track("d", GMInstrument(0, percussion=True), segments=[Segment(g.id, 0, F(2))], channel=10)
    song = Song(tracks=[t], groups={g.id: g})  # 120 BPM: semibreve = 2 s
    ev = build_performance(song).events
    off = {}
    for e in ev:
        if isinstance(e.event, NoteOff):
            off.setdefault(e.event.key, []).append(e.time)
    assert off[49] == [pytest.approx(DRUM_RING)]  # il crash risuona (non si ferma a 1/4 = 0.5 s)
    assert off[42][0] == pytest.approx(0.25)  # il charleston si ferma al colpo successivo dello stesso suono


def test_transport_loop_jumps_back():
    song = import_midi(make_midi())
    backend, clock = RecordingBackend(), FakeClock()
    tr = Transport(backend, clock, threaded=False)
    tr.load(build_performance(song))
    tr.set_loop((1.0, 2.0))
    tr.play()
    assert tr.position == pytest.approx(1.0)  # parte dall'inizio del loop
    clock.t = 1.2
    tr.tick()
    assert tr.position == pytest.approx(1.0)  # arrivato alla fine, torna all'inizio
    assert tr.state is State.PLAYING


def test_chord_symbols_roundtrip_through_midi(tmp_path):
    from editor.exporter.midi import export_midi

    song = import_midi("examples/small.mid")
    bass = song.tracks[2]
    g = song.groups[bass.segments[0].group_id]
    g.chords = [(F(0), "Em"), (F(1, 2), "C#m7")]
    path = tmp_path / "chords.mid"
    export_midi(song, path)
    again = import_midi(path)
    g2 = again.groups[again.tracks[2].segments[0].group_id]
    assert g2.chords == [(F(0), "Em"), (F(1, 2), "C#m7")]
    assert again.tracks[2].segments[0].label == "Staccato"  # il marker resta etichetta


def test_import_yamaha_style_sections_and_drums(tmp_path):
    import struct

    from editor.importer.style import import_style

    tpq = 480
    bar = 4 * tpq
    mid = mido.MidiFile(type=0, ticks_per_beat=tpq)
    tr = mido.MidiTrack()
    mid.tracks.append(tr)
    tr += [mido.MetaMessage("marker", text="SFF1"), mido.MetaMessage("marker", text="SInt"),
           mido.Message("control_change", channel=8, control=0, value=127),  # canale 9: kit Yamaha
           mido.Message("program_change", channel=8, program=84),
           mido.Message("control_change", channel=10, control=0, value=8),  # canale 11: basso
           mido.Message("program_change", channel=10, program=33)]
    events = []  # (tick, messaggio)
    for i, name in enumerate(["Main A", "Main B"]):  # sezioni attaccate, una misura ciascuna
        t0 = bar * (1 + i)
        events.append((t0, mido.MetaMessage("marker", text=name)))
        for ch, note in ((8, 33 if i else 39), (10, 36 + i)) + (((10, 100),) if i == 0 else ()):
            events += [(t0, mido.Message("note_on", channel=ch, note=note, velocity=90)),
                       (t0 + tpq, mido.Message("note_off", channel=ch, note=note, velocity=0))]
    events.append((bar * 3, mido.MetaMessage("end_of_track")))
    now = 0
    for tick, msg in sorted(events, key=lambda e: (e[0], e[1].type != "marker")):
        tr.append(msg.copy(time=tick - now))
        now = tick
    path = tmp_path / "test.sty"
    mid.save(path)

    def ctab(ch, name):
        body = bytes([ch]) + name.ljust(8).encode() + bytes([ch, 1, 0x0F, 0xFF, 7, 255, 255, 255, 255, 0, 2, 1, 0, 6, 0, 127, 1, 0])
        return b"Ctab" + struct.pack(">I", len(body)) + body

    sdec = b"Main A,Main B"
    cseg = b"Sdec" + struct.pack(">I", len(sdec)) + sdec + ctab(8, "add-drum") + ctab(10, "Bass")
    casm = b"CSEG" + struct.pack(">I", len(cseg)) + cseg
    path.write_bytes(path.read_bytes() + b"CASM" + struct.pack(">I", len(casm)) + casm)

    song = import_style(path)
    by_ch = {t.channel: t for t in song.tracks}
    assert by_ch[9].name == "9 add-drum" and by_ch[9].instrument.is_percussion  # percussioni del canale 9
    assert sorted({p.midi for p in song.placed_notes(by_ch[9])}) == [35, 39]  # Kick Soft Yamaha (33) → GM 35
    noise = next(t for t in song.tracks if t.name == "11 Bass · noises")  # Mega Voice: rumori a parte, in Mute
    assert noise.mute and [p.midi for p in song.placed_notes(noise)] == [100]
    by_ch[11] = next(t for t in song.tracks if t.name == "11 Bass")
    assert all(p.midi < 96 for p in song.placed_notes(by_ch[11]))
    assert by_ch[11].name == "11 Bass" and not by_ch[11].instrument.is_percussion
    assert [(s.start_bar, s.label) for s in by_ch[11].segments] == [(0, "Main A"), (1, "Main B")]  # senza SInt
    rule = song.groups[by_ch[11].segments[0].group_id].style  # regole CASM come dati
    assert rule.part == "Bass" and rule.source_chord == "CMaj7" and rule.zones[0].ntr == 1
    assert "11 Bass — written on CMaj7" in song.notes  # e come testo leggibile
    assert "9 add-drum — Yamaha kit 127-0-85" in song.notes and song.import_warnings == []  # kit normale: nessun avviso

    from editor.core.project import load_project, save_project

    save_project(song, tmp_path / "s.music")
    back = load_project(tmp_path / "s.music")
    assert back.notes == song.notes
    assert back.groups[by_ch[11].segments[0].group_id].style == rule


def test_example_style_demo_sty_generates_on_chords():
    """examples/demo.sty (2026-11-23, scritto da zero): lo stile pubblicabile del progetto
    si importa con le sue sezioni e parti e genera l'accompagnamento sulla traccia accordi."""
    from editor.core.arrange import generation_command
    from editor.core.chord_track import ChordEvent
    from editor.core.document import AddTrack, Document
    from editor.core.model import Group, Segment, Song, Track
    from pathlib import Path

    from editor.importer.style import load_style_data, style_sections

    path = str(Path(__file__).resolve().parent.parent / "examples" / "demo.sty")
    assert style_sections(path) == [("Main A", 2), ("Main B", 2), ("Fill In AA", 1), ("Fill In BB", 1)]
    data = load_style_data(path)
    assert [p.name for p in data.parts] == ["10 Rhythm", "11 Bass", "12 Piano"]

    song = Song()
    g = Group("Memoria 1", 3, [], chord_events=[ChordEvent(F(0), "G", 0)])  # Sol maggiore per tutto il tratto
    seg = Segment(g.id, 0, F(3), style=path, section="Main A", fill="Fill In AA")
    doc = Document(song)
    doc.execute(AddTrack(Track("Chords", segments=[seg], kind="chords", channel=1), 0, [g]))
    command, warnings = generation_command(song, [seg], lambda _p: data)
    assert not warnings
    doc.execute(command)
    tracks = {t.channel: t for t in song.tracks if t.kind == "style"}
    assert sorted(tracks) == [10, 11, 12]
    # basso su note di Sol (G B D); il Si del fill (settima di CMaj7) diventa Fa: settima di Sol
    # nella tonalità del brano (Do maggiore)
    assert {p.midi % 12 for p in song.placed_notes(tracks[11])} == {7, 11, 2, 5}
    assert {p.midi % 12 for p in song.placed_notes(tracks[12])} <= {7, 11, 2}  # accordo: Sol-Si-Re
    assert next(iter(song.placed_notes(tracks[11]))).midi % 12 == 7  # il basso parte dalla fondamentale


def test_chord_looper_import_and_chord_track(tmp_path):
    import struct

    from editor.core.document import Document, SplitSegment
    from editor.core.model import Song
    from editor.core.project import load_project, save_project
    from editor.importer.chord_looper import chord_track_from_clb, read_clb

    def chord(cr, ct, bn, bt):
        return bytes([0xFF, 0x7F, 0x07, 0x43, 0x7B, 0x01, cr, ct, bn, bt])

    track = (b"\x00" + chord(0x32, 0, 0x32, 0)  # D
             + b"\x9e\x00" + chord(0x36, 0, 0x41, 30)  # dopo 3840 tick (2 movimenti): A/C#
             + b"\x9e\x00" + b"\xff\x2f\x00")
    memory = b"MThd" + struct.pack(">IHHH", 6, 0, 1, 1920) + b"MTrk" + struct.pack(">I", len(track)) + track
    empty = b"MThd" + struct.pack(">IHHH", 6, 0, 1, 1920) + b"MTrk" + struct.pack(">I", 0)
    path = tmp_path / "giro.clb"
    path.write_bytes(b"CLD_001\r\n" + memory + b"\r\n" + empty + b"\r\n")

    mems = read_clb(path)
    assert [e.name for _t, e in mems[0].events] == ["D", "A/C#"] and not mems[1].events
    song = Song()
    ct, groups = chord_track_from_clb(song, path)
    assert ct.kind == "chords" and len(ct.segments) == 1 and ct.segments[0].label == "Memory 1"
    events = groups[0].chord_events
    assert [(e.pos, e.name) for e in events] == [(0, "D"), (F(1, 2), "A/C#")]  # posizioni esatte (non agganciate)

    doc = Document(song)
    from editor.core.document import AddTrack

    doc.execute(AddTrack(ct, 0, groups))
    assert song.chord_track is ct
    doc.execute(SplitSegment(ct, ct.segments[0], F(3, 4)))  # la parte destra riparte con l'accordo in vigore
    right = song.groups[ct.segments[1].group_id].chord_events
    assert [(e.pos, e.name) for e in right] == [(0, "A/C#")]
    ct.segments[0].style, ct.segments[0].section, ct.styles = "/x/bigrock-v4.sty", "Main A", ["/x/bigrock-v4.sty"]
    save_project(song, tmp_path / "s.music")
    back = load_project(tmp_path / "s.music")
    assert back.chord_track is not None and back.groups[ct.segments[0].group_id].chord_events[0].name == "D"
    assert back.chord_track.segments[0].section == "Main A" and back.chord_track.styles == ["/x/bigrock-v4.sty"]
    assert song.display_label(ct.segments[0]) == "Memory 1 · Main A"

    # Preview: accordo dalla fondamentale nell'ottava del Do centrale + basso; suona solo in Preview
    from editor.core.chord_track import preview_notes

    notes = sorted(preview_notes(song, ct))
    assert {n[2] for n in notes if n[0] == 0} == {38, 62, 66, 69}  # D: basso Re2 + Re Fa# La
    assert {n[2] for n in notes if n[0] == F(1, 2)} == {37, 69, 73, 76}  # A/C#: basso Do#2
    assert ct.id in song.silent_track_ids()
    ct.solo = True
    assert song.silent_track_ids() == {t.id for t in song.tracks if t is not ct}


def test_decompose_syncopation_and_shuffle():
    # 2026-11-20: croma dopo una semicroma (prima: semicroma doppiamente puntata + 1/64),
    # croma puntata del ritmo lombardo, semiminima di terzina dello shuffle sul 2° movimento
    from editor.engraving.layout import decompose

    def figs(offset, length):
        return [(f.base, f.dots, f.triplet, v) for f, v in decompose(offset, length, F(1, 4))]

    assert figs(F(1, 16), F(1, 8)) == [(F(1, 8), 0, False, F(1, 8))]
    assert figs(F(1, 16), F(3, 16)) == [(F(1, 8), 1, False, F(3, 16))]
    assert figs(F(1, 4), F(1, 6)) == [(F(1, 4), 0, True, F(1, 6))]
    assert figs(F(3, 4), F(1, 6)) == [(F(1, 4), 0, True, F(1, 6))]


def test_key_change_and_ties_in_chords():
    # 2026-11-20: un cambio di tonalità dopo la misura 1 si disegna (con i bequadri) e in un
    # accordo la legatura va solo sulla nota legata
    from editor.core.key import Key
    from editor.core.model import Group, Segment, Song, Track
    from editor.core.timeline import KeyTable
    from editor.engraving.layout import Tie

    song = Song()
    notes = [Note(F(0), F(1, 4), 60, tie_next=True), Note(F(0), F(1, 4), 64),
             Note(F(1, 4), F(1, 4), 60), Note(F(1, 4), F(1, 4), 64), Note(F(2), F(1), 66)]
    group = Group("g", 3, notes)
    song.groups[group.id] = group
    track = Track("t", segments=[Segment(group.id, 0, F(3))])
    song.tracks.append(track)
    assert sum(isinstance(i, Tie) for i in engrave(song, track).items) == 1
    song.keys = KeyTable(Key.parse("D"), [(2, Key.parse("C"))])
    names = [i.name for i in engrave(song, track).items if isinstance(i, Glyph)]
    assert names.count("accidentalNatural") >= 2  # i due diesis di Re annullati alla misura 3


def test_pedal_up_on_segment_end_stays_in_segment():
    # 2026-11-20: pedale su sulla stanghetta dove finisce il segmento (dopo un vuoto o alla
    # fine del brano): resta nel segmento, un tick prima, invece di spostarsi o perdersi
    bar = 4 * TPQ
    mid = mido.MidiFile(type=1, ticks_per_beat=TPQ)
    track = mido.MidiTrack()
    track.append(mido.Message("control_change", control=64, value=127, time=0))
    track.append(mido.Message("note_on", note=60, velocity=80, time=0))
    track.append(mido.Message("note_off", note=60, time=bar))
    track.append(mido.Message("control_change", control=64, value=0, time=0))  # misura 2, inizio
    track.append(mido.Message("note_on", note=62, velocity=80, time=2 * bar))  # misura 4
    track.append(mido.Message("note_off", note=62, time=bar))
    track.append(mido.Message("control_change", control=64, value=0, time=0))  # fine del brano
    mid.tracks.append(track)
    song = import_midi(mid)
    (t,) = song.tracks
    assert len(t.segments) == 2
    ups = [pos for pos, e in song.placed_events(t) if e.number == 64 and e.value == 0]
    assert len(ups) == 2
    assert F(3, 4) < ups[0] < 1 and F(15, 4) < ups[1] < 4


def test_rest_does_not_cross_half_bar_in_4_4():
    # 2026-11-20: semiminima, pausa sul 2° e 3° movimento, semiminima → due pause di
    # semiminima (non una pausa di minima sul 2° movimento)
    from editor.core.model import Group, Segment, Song, Track

    song = Song()
    group = Group("g", 1, [Note(F(0), F(1, 4), 60), Note(F(3, 4), F(1, 4), 60)])
    song.groups[group.id] = group
    track = Track("t", segments=[Segment(group.id, 0, F(1))])
    song.tracks.append(track)
    names = [i.name for i in engrave(song, track).items if isinstance(i, Glyph) and i.name.startswith("rest")]
    assert names == ["restQuarter", "restQuarter"]


def _one_bar_song(notes, bars=2):
    """Brano di prova in 4/4 con una traccia e un segmento di `bars` misure."""
    from editor.core.model import Group, Segment, Song, Track

    song = Song()
    group = Group("g", bars, notes)
    song.groups[group.id] = group
    track = Track("t", segments=[Segment(group.id, 0, F(bars))])
    song.tracks.append(track)
    return song, track


def test_sharp_repeated_after_tie_over_barline():
    # 2026-11-20: Fa♯ legato oltre la stanghetta e poi di nuovo Fa♯ nella misura 2: il
    # secondo ha il diesis (il proseguimento legato non lo mostra né lo "consuma")
    song, track = _one_bar_song([Note(F(3, 4), F(1, 2), 66), Note(F(3, 2), F(1, 4), 66)])
    sharps = [i for i in engrave(song, track).items if isinstance(i, Glyph) and i.name == "accidentalSharp"]
    assert len(sharps) == 2


def test_shuffle_has_one_triplet_per_beat():
    # 2026-11-20: semiminima + croma di terzina su ogni movimento → quattro "3"
    notes = []
    for beat in range(4):
        notes += [Note(F(beat, 4), F(1, 6), 60), Note(F(beat, 4) + F(1, 6), F(1, 12), 64)]
    song, track = _one_bar_song(notes, bars=1)
    assert sum(isinstance(i, Glyph) and i.name == "tuplet3" for i in engrave(song, track).items) == 4


def test_resized_segment_hides_note_tail():
    # 2026-11-20: accorciando la fine del segmento la coda della nota non suona più
    song, track = _one_bar_song([Note(F(1), F(1), 64)])
    track.segments[0].length = F(3, 2)
    (p,) = song.placed_notes(track)
    assert p.end == F(3, 2) and p.note.duration == 1


def test_fill_as_long_as_segment():
    # 2026-11-20: segmento di una misura con fill di una misura: c'è solo il fill
    from types import SimpleNamespace

    from editor.core.arrange import _blocks

    data = SimpleNamespace(sections={"Main A": F(2), "Fill In AA": F(1)})
    assert _blocks(F(0), F(1), data, "Main A", "Fill In AA") == [("Fill In AA", F(0), F(1), False)]
    assert _blocks(F(0), F(2), data, "Main A", "Fill In AA")[1] == ("Fill In AA", F(1), F(2), False)


def test_quarter_and_eighth_triplets_one_group():
    # 2026-11-20: semiminima, semiminima, croma, croma di terzina (mezza misura) = un solo "3"
    notes = [Note(F(0), F(1, 6), 60), Note(F(1, 6), F(1, 6), 62), Note(F(1, 3), F(1, 12), 64),
             Note(F(5, 12), F(1, 12), 65), Note(F(1, 2), F(1, 2), 67)]
    song, track = _one_bar_song(notes, bars=1)
    assert sum(isinstance(i, Glyph) and i.name == "tuplet3" for i in engrave(song, track).items) == 1


def test_eighth_triplet_with_split_middle_one_group():
    # 2026-11-21: croma, semicroma, semicroma, croma di terzina in un movimento = un solo "3"
    notes = [Note(F(0), F(1, 12), 60), Note(F(1, 12), F(1, 24), 62), Note(F(1, 8), F(1, 24), 64),
             Note(F(1, 6), F(1, 12), 65), Note(F(1, 4), F(3, 4), 67)]
    song, track = _one_bar_song(notes, bars=1)
    assert sum(isinstance(i, Glyph) and i.name == "tuplet3" for i in engrave(song, track).items) == 1


def test_copyright_import_save_export(tmp_path):
    """Copyright Notice (2026-11-23): letto all'import (MIDI e stili), salvato nel .music,
    scritto nell'export MIDI a tick 0 della traccia 0."""
    from editor.core.project import load_project, save_project
    from editor.exporter.midi import export_midi

    mid = mido.MidiFile(type=1)
    mid.tracks.append(mido.MidiTrack([mido.MetaMessage("copyright", text="(C) 1998 Somebody"),
                                      mido.MetaMessage("copyright", text="second notice")]))  # vale il primo
    mid.tracks.append(mido.MidiTrack([mido.Message("note_on", note=60, velocity=90),
                                      mido.Message("note_off", note=60, time=480)]))
    path = tmp_path / "c.mid"
    mid.save(path)
    song = import_midi(path)
    assert song.copyright == "(C) 1998 Somebody"
    assert import_midi("examples/small.mid").copyright == ""  # file senza Copyright Notice

    save_project(song, tmp_path / "c.music")
    assert load_project(tmp_path / "c.music").copyright == "(C) 1998 Somebody"

    song.copyright = "© 2026 Me — test"
    export_midi(song, tmp_path / "out.mid")
    first = mido.MidiFile(tmp_path / "out.mid").tracks[0]
    notice = next(m for m in first if m.type == "copyright")
    assert notice.text == "© 2026 Me - test"  # latin-1: © resta, la lineetta diventa "-"
    assert import_midi(tmp_path / "out.mid").copyright == "© 2026 Me - test"
    song.copyright = ""
    export_midi(song, tmp_path / "none.mid")
    assert not any(m.type == "copyright" for m in mido.MidiFile(tmp_path / "none.mid").tracks[0])
