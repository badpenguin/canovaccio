from fractions import Fraction as F

import pytest

from editor.core.clef import BASS, TREBLE
from editor.core.document import Document, SetAttribute, SetInitialKey
from editor.core.instrument import GMInstrument
from editor.core.key import MAJOR, MINOR, Key, KeySpeller
from editor.core.model import Group, Note, Segment, Song, Track
from editor.core.pitch import Pitch, Spelling
from editor.core.timeline import MeterTable, TempoTable, TimeSignature


def names(key: str) -> list[str]:
    k = Key.parse(key)
    return [str(KeySpeller().spell(m, k).spelling) for m in range(60, 72)]


def test_pitch_midi_roundtrip():
    assert Pitch(Spelling("C"), 4).midi == 60
    assert Pitch.from_midi(60, Spelling("B", 1)) == Pitch(Spelling("B", 1), 3)
    assert Pitch.from_midi(59, Spelling("C", -1)) == Pitch(Spelling("C", -1), 4)
    with pytest.raises(ValueError):
        Pitch.from_midi(61, Spelling("D"))


def test_spelling_follows_key():
    assert names("Bm")[10] == "A#"  # sensibile di Si minore
    assert names("F")[10] == "Bb"
    assert names("C") == ["C", "C#", "D", "Eb", "E", "F", "F#", "G", "Ab", "A", "Bb", "B"]
    assert names("Am")[3] == "D#" and names("Am")[8] == "G#"
    assert not any(n.endswith(("bb", "##")) for k in ("Db", "Gb", "C#", "D#m") for n in names(k))


def test_key_signature_fifths():
    assert Key.parse("Bm").fifths == 2
    assert Key.parse("Eb").fifths == -3
    assert Key.from_fifths(2, MINOR) == Key.parse("Bm")
    assert Key.from_fifths(-1, MAJOR) == Key.parse("F")


def test_clef_steps():
    assert TREBLE.staff_step(Pitch(Spelling("E"), 4)) == 0
    assert TREBLE.staff_step(Pitch(Spelling("F"), 5)) == 8
    assert BASS.staff_step(Pitch(Spelling("G"), 2)) == 0
    assert BASS.staff_step(Pitch(Spelling("A"), 3)) == 8


def test_meter_table():
    m = MeterTable(TimeSignature(4, 4), [(2, TimeSignature(3, 4))])
    assert [m.bar_start(b) for b in range(4)] == [0, 1, 2, F(11, 4)]
    assert m.bar_at(F(9, 4)) == 2 and m.bar_at(F(11, 4)) == 3
    assert m.bar_ceil(F(1)) == 1 and m.bar_ceil(F(9, 8)) == 2


def test_tempo_table():
    t = TempoTable(120, [(F(1), 60)])
    assert t.seconds(F(1)) == pytest.approx(2.0)
    assert t.seconds(F(2)) == pytest.approx(6.0)
    assert t.position(6.0) == 2


def _song(key="C") -> tuple[Song, Track]:
    g = Group("g", 1, [Note(F(0), F(1, 4), 70)])
    track = Track("t", segments=[Segment(g.id, 1, F(1), transpose=0)])
    song = Song(tracks=[track], groups={g.id: g})
    song.keys.set(0, Key.parse(key))
    return song, track


def test_spelling_uses_active_key_and_override():
    song, track = _song("Bm")
    placed = next(song.placed_notes(track))
    assert placed.start == 1  # segmento alla misura 2
    assert str(song.spell(placed)) == "A#4"
    song.keys.set(0, Key.parse("F"))
    assert str(song.spell(placed)) == "Bb4"
    placed.note.spelling = Spelling("A", 1)  # grafia forzata
    assert str(song.spell(placed)) == "A#4"


def test_document_undo_redo():
    song, track = _song()
    doc = Document(song)
    seen = []
    doc.subscribe(lambda d: seen.append(1))
    doc.execute(SetAttribute(track, "instrument", GMInstrument(40)))
    doc.execute(SetInitialKey(Key.parse("Bm")))
    assert track.instrument.program == 40 and str(song.keys.initial) == "Bm"
    doc.undo()
    doc.undo()
    assert track.instrument.program == 0 and str(song.keys.initial) == "C"
    doc.redo()
    assert track.instrument.program == 40
    assert len(seen) == 5


def test_compound_is_one_undo_step():
    from editor.core.clef import BASS
    from editor.core.document import Compound

    song, track = _song()
    doc = Document(song)
    doc.execute(Compound([SetAttribute(track, "clef", BASS), SetAttribute(track, "instrument", GMInstrument(5))]))
    assert track.clef is BASS and track.instrument.program == 5
    doc.undo()
    assert track.clef is not BASS and track.instrument.program == 0


def test_move_segments_snap_space_and_undo():
    from fractions import Fraction as Fr

    from editor.core.document import MoveSegments

    g = Group("g", 2)
    a, b = Track("a"), Track("b")
    s1 = Segment(g.id, 0, F(2))
    s2 = Segment(g.id, 4, F(2))
    a.segments, b.segments = [s1], [s2]
    song = Song(tracks=[a, b], groups={g.id: g})  # 4/4
    assert song.snap_to_beat(Fr(5, 8)) in (Fr(1, 2), Fr(3, 4))  # sul movimento
    assert song.snap_to_beat(Fr(13, 16)) == Fr(3, 4)
    assert not MoveSegments.fits(song, [(s1, a, b, 2, Fr(1, 4))])  # 2¼–4¼ tocca 4–6
    assert MoveSegments.fits(song, [(s1, a, b, 1, Fr(3, 4))])  # 1¾–3¾: c'è spazio
    doc = Document(song)
    doc.execute(MoveSegments([(s1, a, b, 1, Fr(3, 4))]))
    assert a.segments == [] and b.segments == [s1, s2]
    assert song.segment_span(s1) == (Fr(7, 4), Fr(15, 4)) and song.segment_bars(s1) == (1, 4)
    doc.undo()
    assert a.segments == [s1] and (s1.start_bar, s1.offset) == (0, 0) and b.segments == [s2]


def test_split_segment_ties_crossing_notes_and_undo():
    from editor.core.document import SplitSegment

    g = Group("g", 2, [Note(F(0), F(1, 2), 60), Note(F(3, 4), F(1, 2), 62), Note(F(5, 4), F(1, 4), 64)])
    seg = Segment(g.id, 1, F(2))
    t = Track("t", segments=[seg])
    song = Song(tracks=[t], groups={g.id: g})
    before = [(p.start, p.midi) for p in song.placed_notes(t)]
    doc = Document(song)
    with pytest.raises(ValueError):
        SplitSegment(t, seg, F(0))  # sul bordo: già diviso lì
    doc.execute(SplitSegment(t, seg, F(1)))
    a, b = t.segments
    assert (a.start_bar, a.length, b.start_bar, b.length, b.offset) == (1, 1, 2, 1, 0)
    assert g.id not in song.groups  # il gruppo originale non è più usato
    left, right = song.groups[a.group_id].notes, song.groups[b.group_id].notes
    assert left[1].tie_next and left[1].end == 1 and right[0].start == 0 and right[0].duration == F(1, 4)
    # il suono non cambia: stesse altezze, la nota tagliata è legata
    assert [(p.midi) for p in song.placed_notes(t)] == [60, 62, 62, 64]
    doc.undo()
    assert t.segments == [seg] and g.id in song.groups
    assert [(p.start, p.midi) for p in song.placed_notes(t)] == before


def test_split_on_a_beat_inside_a_bar():
    from editor.core.document import SplitSegment

    g = Group("g", 2, [Note(F(0), F(1), 60)])  # semibreve
    seg = Segment(g.id, 0, F(2))
    t = Track("t", segments=[seg])
    song = Song(tracks=[t], groups={g.id: g})
    Document(song).execute(SplitSegment(t, seg, F(3, 4)))  # sulla quarta battuta della misura 1
    a, b = t.segments
    assert song.segment_span(a) == (0, F(3, 4)) and song.segment_span(b) == (F(3, 4), 2)
    assert (b.start_bar, b.offset) == (0, F(3, 4))
    assert [p.midi for p in song.placed_notes(t)] == [60, 60]  # nota tagliata in due, legata


def test_resize_keeps_content_in_place_and_delete():
    from editor.core.document import DeleteSegments, ResizeSegment

    g = Group("g", 2, [Note(F(0), F(1, 4), 60), Note(F(3, 2), F(1, 4), 62)])
    seg = Segment(g.id, 0, F(2))
    t = Track("t", segments=[seg])
    song = Song(tracks=[t], groups={g.id: g})
    doc = Document(song)
    doc.execute(ResizeSegment(seg, F(1, 2), F(2)))  # inizio spostato avanti: la prima nota sparisce
    assert [(p.start, p.midi) for p in song.placed_notes(t)] == [(F(3, 2), 62)]  # la seconda resta dov'era
    doc.execute(ResizeSegment(seg, F(1, 2), F(1)))  # fine accorciata: anche la seconda è nascosta
    assert list(song.placed_notes(t)) == []
    doc.undo(); doc.undo()
    assert [(p.start, p.midi) for p in song.placed_notes(t)] == [(0, 60), (F(3, 2), 62)]
    doc.execute(DeleteSegments([(t, seg)]))
    assert t.segments == [] and g.id not in song.groups
    doc.undo()
    assert t.segments == [seg] and g.id in song.groups


def test_add_segments_checks_space_and_undo():
    from editor.core.document import AddSegments

    g = Group("g", 1, [Note(F(0), F(1, 4), 60)])
    t = Track("t", segments=[Segment(g.id, 0, F(1))])
    song = Song(tracks=[t], groups={g.id: g})
    g2 = Group("copia", 1, [Note(F(0), F(1, 4), 60)])
    overlap = [(t, Segment(g2.id, 0, F(1), offset=F(1, 2)), g2)]
    assert not AddSegments.fits(song, overlap)
    free = [(t, Segment(g2.id, 1, F(1)), g2)]
    assert AddSegments.fits(song, free)
    doc = Document(song)
    doc.execute(AddSegments(free))
    assert len(t.segments) == 2 and g2.id in song.groups
    doc.undo()
    assert len(t.segments) == 1 and g2.id not in song.groups


def test_segment_label_field_and_split_keeps_it():
    from editor.core.document import SplitSegment

    g = Group("g", 2, [Note(F(0), F(1, 4), 60)])
    seg = Segment(g.id, 0, F(2), label="Strofa")
    t = Track("t", segments=[seg])
    song = Song(tracks=[t], groups={g.id: g})
    Document(song).execute(SplitSegment(t, seg, F(1)))
    assert [s.label for s in t.segments] == ["Strofa", "Strofa"]


def test_add_linked_segment_keeps_shared_group_on_undo():
    from editor.core.document import AddSegments

    g = Group("g", 1, [Note(F(0), F(1, 4), 60)])
    seg = Segment(g.id, 0, F(1))
    t = Track("t", segments=[seg])
    song = Song(tracks=[t], groups={g.id: g})
    doc = Document(song)
    doc.execute(AddSegments([(t, Segment(g.id, 1, F(1)), g)]))  # copia collegata
    assert len(t.segments) == 2 and t.segments[1].group_id == g.id
    doc.undo()
    assert t.segments == [seg] and g.id in song.groups  # il gruppo condiviso resta


def test_clone_label_and_unlink():
    from editor.core.document import UnlinkSegments

    g = Group("g", 1, [Note(F(0), F(1, 4), 60)])
    orig = Segment(g.id, 0, F(1), label="Ritornello")
    clone = Segment(g.id, 1, F(1), clone_of=orig.id)
    t = Track("t", segments=[orig, clone])
    song = Song(tracks=[t], groups={g.id: g})
    assert song.display_label(clone) == "Ritornello (CLONE)"
    orig.label = ""
    assert song.display_label(clone) == "#1 (CLONE)"
    orig.label = "Ritornello"
    doc = Document(song)
    doc.execute(UnlinkSegments([clone]))
    assert clone.clone_of is None and clone.group_id != g.id and clone.label == "Ritornello"
    assert song.groups[clone.group_id].notes[0].midi == 60
    doc.undo()
    assert clone.clone_of == orig.id and clone.group_id == g.id and clone.label == ""


def test_add_delete_move_track_and_undo():
    from editor.core.document import AddTrack, DeleteTrack, MoveTrack

    song, a = _song()
    b, c = Track("b"), Track("c")
    doc = Document(song)
    doc.execute(AddTrack(b, 1))
    doc.execute(AddTrack(c, 1))  # sotto la prima
    assert [t.name for t in song.tracks] == ["t", "c", "b"]
    doc.execute(MoveTrack(a, 2))
    assert [t.name for t in song.tracks] == ["c", "b", "t"]
    gid = a.segments[0].group_id
    doc.execute(DeleteTrack(a))
    assert a not in song.tracks and gid not in song.groups  # il gruppo non è più usato
    doc.undo()
    assert song.tracks[2] is a and gid in song.groups
    doc.undo(); doc.undo(); doc.undo()
    assert song.tracks == [a]


def test_song_table_rows_roundtrip_and_errors():
    from editor.core.document import SetSongTables
    from editor.ui.song_dialog import build_tables, song_rows

    song = Song(tempos=TempoTable(100, [(F(9, 4), 80)]), meters=MeterTable(TimeSignature(3, 4)))
    rows = song_rows(song)
    assert rows == [(1, 1, "Tempo", "100"), (1, 1, "Scala", "C"), (1, 1, "Metro", "3/4"), (4, 1, "Tempo", "80")]
    rows += [(5, F(1), "Metro", "4/4"), (5, F(1), "Scala", "Am"), (5, F(3), "Tempo", "120")]
    tempos, meters, keys = build_tables(rows)
    assert [(e.at, e.value) for e in tempos] == [(0, 100), (F(9, 4), 80), (F(3) + F(2, 4), 120)]
    assert [(e.at, str(e.value)) for e in meters] == [(0, "3/4"), (4, "4/4")]
    assert str(keys.at(4)) == "Am"
    doc = Document(song)
    doc.execute(SetSongTables(tempos, meters, keys))
    assert song.meters is meters
    doc.undo()
    assert str(song.meters.at(4)) == "3/4"
    for bad in ([(2, F(2), "Metro", "4/4")], [(2, F(1), "Tempo", "abc")], [(2, F(4), "Tempo", "90")]):
        with pytest.raises(ValueError):
            build_tables(song_rows(song) + bad)


def test_note_commands_add_edit_delete_and_undo():
    from editor.core.document import AddNotes, DeleteNotes, EditNotes

    song, track = _song()
    group = song.groups[track.segments[0].group_id]
    first = group.notes[0]
    doc = Document(song)
    new = Note(F(1, 2), F(1, 8), 64)
    doc.execute(AddNotes([(group, new)]))
    doc.execute(EditNotes([(group, first, {"start": F(3, 4), "midi": 72})]))
    assert group.notes == [new, first] and first.midi == 72  # riordinate per inizio
    doc.execute(DeleteNotes([(group, new)]))
    assert group.notes == [first]
    doc.undo(); doc.undo(); doc.undo()
    assert group.notes == [first] and first.start == 0 and first.midi == 70


def test_diatonic_shift_follows_the_key():
    from editor.core.key import diatonic_shift

    c, am, d = Key.parse("C"), Key.parse("Am"), Key.parse("D")
    assert [diatonic_shift(m, c, 1) for m in (60, 64, 71)] == [62, 65, 72]  # Do→Re, Mi→Fa, Si→Do
    assert diatonic_shift(66, c, 1) == 68  # fuori scala: Fa# → Sol#
    assert diatonic_shift(60, c, -1) == 59 and diatonic_shift(60, c, 7) == 72
    assert diatonic_shift(69, am, 2) == 72  # La → Do in La minore
    assert diatonic_shift(62, d, 2) == 66  # Re → Fa# in Re maggiore


def test_quantize_spans():
    from editor.core.quantize import END, LENGTH, START, START_END, START_LENGTH, quantize_span

    m = MeterTable(TimeSignature(4, 4))
    e = F(1, 8)
    s, end = F(9, 64), F(9, 64) + F(5, 32)  # poco dopo 1/8, durata un po' più di 1/8
    assert quantize_span(s, end, e, m, START) == (e, e + F(5, 32))
    assert quantize_span(s, end, e, m, START_LENGTH) == (e, e + e)
    assert quantize_span(s, end, e, m, START_END) == (e, F(1, 4))
    assert quantize_span(s, end, e, m, END) == (s, F(1, 4))
    assert quantize_span(s, end, e, m, LENGTH) == (s, s + e)
    assert quantize_span(F(0), F(1, 64), e, m, START_LENGTH) == (0, e)  # durata minima un passo
    m34 = MeterTable(TimeSignature(3, 4))
    assert quantize_span(F(3, 4) + F(1, 13), F(1), F(1, 12), m34, START) [0] == F(3, 4) + F(1, 12)  # terzina dalla misura


def test_detect_staccato_exact_half_only():
    from editor.core.detect import detect_staccato

    q, tick = F(1, 4), F(1, 1920)
    notes = [Note(F(0), q / 2 - tick, 65), Note(q, q / 2, 65), Note(2 * q, q * F(3, 5), 65), Note(3 * q, q / 2, 65)]
    found = detect_staccato(notes)
    assert [(notes.index(n), d) for n, d in found] == [(0, q), (1, q)]  # 60%: no; ultima: no


def test_document_modified_flag():
    song, track = _song()
    doc = Document(song)
    assert not doc.modified
    doc.execute(SetAttribute(track, "name", "x"))
    assert doc.modified
    doc.mark_saved("brano.music")
    assert not doc.modified and doc.project_path == "brano.music"
    doc.undo()
    assert doc.modified  # tornare indietro dopo il salvataggio è una modifica
    doc.redo()
    assert not doc.modified
    doc.touch()  # Mute / Solo
    assert doc.modified


def test_tie_targets_same_pitch_touching():
    from editor.core.detect import tie_targets

    g = Group("g", 1, [Note(F(0), F(1, 4), 60), Note(F(1, 4), F(1, 4), 60), Note(F(1, 2), F(1, 4), 62),
                       Note(F(7, 8), F(1, 8), 62)])
    t = Track("t", segments=[Segment(g.id, 0, F(1))])
    song = Song(tracks=[t], groups={g.id: g})
    placed = list(song.placed_notes(t))
    pairs = tie_targets(placed, {id(n) for n in g.notes})
    assert [(a.note, b.note) for a, b in pairs] == [(g.notes[0], g.notes[1])]  # 62: non si toccano


def test_velocities_fixed_and_humanized():
    import random

    from editor.ui.velocity_dialog import velocities

    assert velocities(90, 3) == [90, 90, 90]
    values = velocities(120, 200, 10, random.Random(1))
    assert all(110 <= v <= 127 for v in values) and len(set(values)) > 5  # resta nel range MIDI


def test_slurs_survive_group_copy_and_split():
    from editor.core.document import SetSlurs, SplitSegment

    g = Group("g", 2, [Note(F(i, 4), F(1, 4), 60 + i) for i in range(8)])
    g.slurs = [(g.notes[0].id, g.notes[2].id), (g.notes[3].id, g.notes[5].id)]
    copy = g.copy()
    assert [(copy.notes.index(next(n for n in copy.notes if n.id == a)),
             copy.notes.index(next(n for n in copy.notes if n.id == b))) for a, b in copy.slurs] == [(0, 2), (3, 5)]
    seg = Segment(g.id, 0, F(2))
    t = Track("t", segments=[seg])
    song = Song(tracks=[t], groups={g.id: g})
    doc = Document(song)
    doc.execute(SplitSegment(t, seg, F(1)))  # taglio a metà: il primo arco resta, il secondo (3–5) si perde
    left, right = (song.groups[s.group_id] for s in t.segments)
    assert len(left.slurs) == 1 and right.slurs == []
    doc.undo()
    doc.execute(SetSlurs(g, []))
    assert g.slurs == []
    doc.undo()
    assert len(g.slurs) == 2


def test_chords_spelling_types_flags_and_forms():
    from editor.core.chords import OPEN, build_chord, chord_name
    from editor.core.pitch import Spelling as Sp

    def names(tones):
        return [str(t.spelling) if t.spelling else "?" for t in tones]

    c = Sp("C")
    assert [t.midi for t in build_chord(c, "maj", set())] == [60, 64, 67]
    assert names(build_chord(Sp("F", 1), "maj", {"7"})) == ["F#", "A#", "C#", "E"]
    assert [t.midi for t in build_chord(c, "maj", set(), OPEN)] == [60, 67, 76]  # lata: Do Sol Mi
    assert [t.midi for t in build_chord(c, "maj", {"8"})] == [60, 64, 67, 72]  # 4 voci
    assert [t.midi for t in build_chord(c, "dim", {"7"})] == [60, 63, 66, 69]  # dim7
    assert [t.midi for t in build_chord(c, "maj", {"11"})] == [60, 67, 70, 74, 77]  # senza 3ª
    assert [t.midi for t in build_chord(Sp("A"), "min", set(), octave=3)] == [57, 60, 64]
    assert chord_name(c, "maj", {"maj7"}) == "Cmaj7" and chord_name(Sp("A"), "min", {"9"}) == "Am9"


def test_guitar_chords_open_and_barre():
    from editor.core.guitar import guitar_chord

    c = guitar_chord(0, "maj")
    assert c.shape == "x32010" and c.midi == [48, 52, 55, 60, 64] and c.description == "open"
    f = guitar_chord(5, "maj")  # Fa: barré al 1° tasto, forma di Mi
    assert f.shape == "133211" and f.midi == [41, 48, 53, 57, 60, 65]
    bm = guitar_chord(11, "min")  # Si minore: barré al 2° tasto, forma di La
    assert bm.shape == "x24432" and "A shape" in bm.description
    for pc in range(12):  # tutte le forme hanno solo note dell'accordo giusto
        for kind, chord in (("maj", {0, 4, 7}), ("min", {0, 3, 7}), ("7", {0, 4, 7, 10}),
                            ("m7", {0, 3, 7, 10}), ("maj7", {0, 4, 7, 11})):
            assert {(m - pc) % 12 for m in guitar_chord(pc, kind).midi} <= chord, (pc, kind)


def test_chord_symbols_only_where_the_chord_changes():
    from editor.core.chords import dedupe_symbols, looks_like_symbol, place_symbol

    q = F(1, 4)
    chords = place_symbol([], F(0), "Em")
    chords = place_symbol(chords, q, "Em")  # uguale a quella in vigore: niente
    assert chords == [(F(0), "Em")]
    chords = place_symbol(chords, 2 * q, "C")
    chords = place_symbol(chords, 3 * q, "Em")
    assert chords == [(F(0), "Em"), (2 * q, "C"), (3 * q, "Em")]
    chords = place_symbol(chords, 2 * q, "Em")  # al posto di C: anche la Em dopo diventa inutile
    assert chords == [(F(0), "Em")]
    assert dedupe_symbols([(F(0), "C"), (q, "C"), (2 * q, "G")]) == [(F(0), "C"), (2 * q, "G")]
    assert all(looks_like_symbol(t) for t in ("C", "F#m7", "Bbmaj7", "Gsus4", "D/F#", "Am(add9)"))
    assert not any(looks_like_symbol(t) for t in ("Main A", "Fill In AA", "SFF1", "Intro"))


def test_detect_chord_names():
    from editor.core.chords import detect_chord

    assert detect_chord([60, 64, 67]) == "C"
    assert detect_chord([57, 60, 64]) == "Am"
    assert detect_chord([64, 67, 72]) == "C/E"  # rivolto: basso Mi
    assert detect_chord([40, 47, 52, 55, 59, 64]) == "Em"  # Em di chitarra (raddoppi)
    assert detect_chord([43, 47, 50, 53]) == "G7"
    assert detect_chord([60, 64, 70]) == "C7"  # 7 senza la 5ª
    assert detect_chord([62, 65, 69, 72]) == "Dm7"
    assert detect_chord([70, 74, 77], flats=True) == "Bb"
    assert detect_chord([60, 61, 62]) is None  # grappolo: non è un accordo
    assert detect_chord([60]) is None


def test_paste_segments_from_another_song():
    """Segmenti copiati in un brano e incollati in un altro (2026-11-23): tracce sotto la prima
    di destinazione alla stessa distanza, stessa distanza nel tempo, gruppi e note nuovi."""
    from fractions import Fraction

    from editor.core.document import AddSegments
    from editor.core.paste import paste_from_other_song
    from editor.session import Session

    s = Session("null")
    src = s.open_path("examples/small.mid").song
    t0, t1 = src.tracks[0], src.tracks[1]
    a, b = t0.segments[0], t1.segments[0]
    first = min(src.segment_span(a)[0], src.segment_span(b)[0])
    entries = [{"track_index": src.tracks.index(t), "rel": src.segment_span(seg)[0] - first,
                "length": seg.length, "transpose": seg.transpose, "trim": seg.trim, "label": seg.label,
                "arrangement": seg.arrangement(), "name": src.groups[seg.group_id].name,
                "content": src.groups[seg.group_id].copy()} for t, seg in ((t0, a), (t1, b))]
    doc = s.new_document()
    dest = doc.song
    assert paste_from_other_song(dest, entries, 0, Fraction(0)) is None  # una traccia sola: non bastano
    dest.tracks[0].segments.clear()
    doc2 = s.open_path("examples/small.mid")  # stesse tracce, brano diverso
    song2 = doc2.song
    for t in song2.tracks:
        t.segments.clear()
    items = paste_from_other_song(song2, entries, 0, Fraction(4))
    assert [t for t, _s, _g in items] == [song2.tracks[0], song2.tracks[1]]
    starts = [song2.meters.bar_start(sg.start_bar) + sg.offset for _t, sg, _g in items]
    assert starts[0] - Fraction(4) == entries[0]["rel"] and starts[1] - Fraction(4) == entries[1]["rel"]
    assert all(g.id not in src.groups for _t, _s, g in items)  # gruppi nuovi, non quelli del brano di partenza
    assert len(items[0][2].notes) == len(src.groups[a.group_id].notes)
    doc2.execute(AddSegments(items))
    assert all(sg.clone_of is None for _t, sg, _g in items)
    s.close()
