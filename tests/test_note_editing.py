"""Comandi comuni sulle note (griglia e notazione), provati senza GTK con una vista finta."""

from fractions import Fraction as F

from editor.core.document import Document
from editor.importer.midi import import_midi
from editor.ui.note_editing import NoteEditing


class FakeView(NoteEditing):
    def __init__(self, doc, tracks):
        self.doc, self._tracks = doc, tracks
        self.transport = type("T", (), {"preview": lambda *a: None})()
        self.messages = []
        self.on_message = lambda text, error=True: self.messages.append(text)
        self.snap = F(1, 8)
        self.init_note_editing()
        self.cache()
        doc.subscribe(lambda _d: self.cache())

    edit_tracks = property(lambda self: self._tracks)

    def redraw(self):
        pass

    def paste_target(self):
        return self._tracks[0], F(1)

    def cache(self):
        self._placed = [p for t in self._tracks for p in self.doc.song.placed_notes(t)]
        self._by_note = {id(p.note): p for p in self._placed}


def test_shared_commands_on_two_tracks():
    doc = Document(import_midi("examples/small.mid"))
    view = FakeView(doc, doc.song.tracks[:2])
    first = view._placed[0].note
    view.selection = [first]
    midi = first.midi
    view.semitone(1)
    assert first.midi == midi + 1
    view.octave(-1)
    assert first.midi == midi - 11
    view.double_lengths()
    view.toggle_staccato()
    assert first.staccato
    view.set_velocities(100)
    assert first.velocity == 100
    assert view.copy_selection()
    view.paste()
    assert len(view.selection) == 1 and view.selection[0] is not first
    view.select_all()
    assert len(view.selection) == len(view._placed)
    view.quantize_selection()
    view.delete_selection()
    assert view._placed == []
    doc.undo()
    assert view._placed


def test_paste_refuses_overlap_with_same_pitch():
    doc = Document(import_midi("examples/small.mid"))
    view = FakeView(doc, doc.song.tracks[:1])
    view.selection = [view._placed[0].note]
    view.copy_selection()
    view.paste_target = lambda: (view._tracks[0], view._placed[0].start)  # proprio sopra l'originale
    count = len(view._placed)
    view.paste()
    assert len(view._placed) == count and "same pitch" in view.messages[-1]


def test_set_lengths_from_palette_respects_space():
    doc = Document(import_midi("examples/small.mid"))
    view = FakeView(doc, doc.song.tracks[2:3])  # basso: crome attaccate
    first = view._placed[0].note
    view.selection = [first]
    view.set_lengths(F(1, 16))
    assert first.duration == F(1, 16)
    view.set_lengths(F(1, 2))  # arriverebbe sopra il Si basso successivo (stessa altezza): rifiutato
    assert first.duration == F(1, 16) and "same pitch" in view.messages[-1]
    view.set_lengths(F(1, 4))  # fino all'attacco del Si basso successivo: entra
    assert first.duration == F(1, 4)


class ScoreFake(FakeView):
    """Vista finta con il cambio di durata dello Score (le note della misura fanno spazio)."""

    def _track_of(self, p):
        return self._track_of_segment(p.segment)

    def set_lengths(self, length):
        from editor.ui.notation import NotationView

        NotationView.set_lengths(self, length)


def test_score_lengths_ripple_inside_the_bar():
    doc = Document(import_midi("examples/small.mid"))
    view = ScoreFake(doc, doc.song.tracks[2:3])  # basso: 8 crome attaccate in una misura
    bass = sorted(view._placed, key=lambda p: p.start)
    view.selection = [p.note for p in bass]
    view.set_lengths(F(1, 16))  # tutte semicrome: restano attaccate, mezza misura
    starts = sorted(p.start for p in view._placed)
    assert [b - a for a, b in zip(starts, starts[1:])] == [F(1, 16)] * 7
    view.selection = [bass[0].note]
    view.set_lengths(F(1, 4))  # la prima diventa semiminima: le altre si spostano di 3/16
    starts = sorted(p.start for p in view._placed)
    assert starts[1] - starts[0] == F(1, 4)
    view.selection = [p.note for p in view._placed]
    view.set_lengths(F(1, 4))  # 8 semiminime non entrano in una misura 4/4: rifiutato
    assert "bar" in view.messages[-1]


def test_insert_chord_at_insert_point():
    from editor.core.chords import build_chord
    from editor.core.guitar import guitar_chord
    from editor.core.chords import ChordTone
    from editor.core.pitch import Spelling

    doc = Document(import_midi("examples/small.mid"))
    track = doc.song.tracks[3]  # terzine, misura 10: la misura è piena di note alte
    seg = track.segments[0]
    start = doc.song.segment_span(seg)[0]
    view = FakeView(doc, [track])
    view.insert_point = lambda: (track, start, F(1, 4))
    view.transport = type("T", (), {"preview": lambda *a: None, "seek": lambda *a: None})()
    low = build_chord(Spelling("C"), "maj", set(), octave=2)  # Do2 Mi2 Sol2: non tocca le terzine
    count = len(view._placed)
    assert view.insert_chord(low)
    assert len(view._placed) == count + 3
    assert not view.insert_chord(low)  # di nuovo nello stesso punto: stesse altezze, rifiutato
    guitar = [ChordTone(m, None) for m in guitar_chord(0, "maj").midi]  # x32010: Do3 Mi3 Sol3 Do4 Mi4
    assert view.insert_chord(guitar, "down")
    assert {n.strum for n in (p.note for p in view._placed) if n.strum} == {"down"}


def test_detect_chords_writes_symbols_where_they_change():
    from editor.core.project import load_project

    doc = Document(load_project("examples/strum-test.music"))  # due misure di Em in semicrome
    view = FakeView(doc, doc.song.tracks[:1])
    view.select_all()
    view.detect_chords()
    group = doc.song.groups[doc.song.tracks[0].segments[0].group_id]
    assert group.chords == [(F(0), "Em")]  # una sola sigla: l'accordo non cambia mai
    doc.undo()
    assert group.chords == []


def test_quantize_keeps_notes_in_a_segment_shorter_than_the_step():
    # 2026-11-20: con un segmento più corto del passo le note finivano prima del segmento e sparivano
    doc = Document(import_midi("examples/small.mid"))
    track = doc.song.tracks[0]
    seg = track.segments[0]
    seg.length = F(1, 8)
    view = FakeView(doc, [track])
    before = len(view._placed)
    assert before
    view.selection = [p.note for p in view._placed]
    view.quantize_selection(F(1, 4))
    a, _b = doc.song.segment_span(seg)
    assert len(view._placed) == before and all(p.start >= a for p in view._placed)


def _song_with(notes, length=F(1), percussion=False):
    """Brano di una traccia con un segmento di `length` e le note date (per i test)."""
    from editor.core.instrument import GMInstrument
    from editor.core.model import Group, Note, Segment, Song, Track

    song = Song()
    group = Group("g", 1, list(notes))
    song.groups[group.id] = group
    track = Track("t", segments=[Segment(group.id, 0, length)], channel=10 if percussion else 1)
    if percussion:
        track.instrument = GMInstrument(0, percussion=True)
    song.tracks.append(track)
    return Document(song), track


def test_duplicate_and_merge_refuse_same_pitch_overlaps():
    # 2026-11-20: Duplicate e Merge non creano più note sopra note della stessa altezza
    from editor.core.model import Note

    doc, track = _song_with([Note(F(0), F(1, 4), 60), Note(F(1, 4), F(1, 4), 60), Note(F(1, 2), F(1, 4), 60)])
    view = FakeView(doc, [track])
    first, _second, third = [p.note for p in view._placed]
    view.selection = [first]
    view.duplicate_selection()
    assert len(view._placed) == 3 and "same pitch" in view.messages[-1]
    view.selection = [first, third]
    view.merge_selection()
    assert len(view._placed) == 3 and "same pitch" in view.messages[-1]


def test_quantize_rounds_half_up_and_refuses_doubles():
    # 2026-11-20: le metà vanno sempre verso l'alto; due note della stessa altezza nello stesso punto: rifiuto
    from editor.core.model import Note

    doc, track = _song_with([Note(F(1, 16), F(1, 16), 60), Note(F(3, 16), F(1, 16), 62),
                             Note(F(5, 16), F(1, 16), 64)])
    view = FakeView(doc, [track])
    view.selection = [p.note for p in view._placed]
    view.quantize_selection(F(1, 8))
    assert [p.start for p in view._placed] == [F(1, 8), F(1, 4), F(3, 8)]
    doc2, track2 = _song_with([Note(F(0), F(1, 4), 60), Note(F(1, 32), F(1, 8), 60)])
    view2 = FakeView(doc2, [track2])
    view2.selection = [p.note for p in view2._placed]
    view2.quantize_selection(F(1, 8))
    assert [p.start for p in view2._placed] == [F(0), F(1, 32)] and "same point" in view2.messages[-1]


def test_drum_hits_fit_in_the_last_quarter_of_a_segment():
    # 2026-11-20: sulla batteria conta l'attacco: colpi anche nell'ultimo quarto del segmento
    doc, track = _song_with([], percussion=True)
    view = FakeView(doc, [track])
    seg = track.segments[0]
    assert view._fits(seg, F(7, 8), F(7, 8) + F(1, 4), 42)
    assert not view._fits(seg, F(1), F(5, 4), 42)


def test_event_editor_note_checks():
    # 2026-11-20: Apply e Add dell'Event Editor rifiutano note oltre la fine del segmento o
    # sopra una nota della stessa altezza (sulla batteria conta solo l'attacco)
    from editor.core.model import Group, Note, Segment
    from editor.ui.event_editor import note_problem

    group = Group("g", 1, [Note(F(0), F(1, 4), 60), Note(F(1, 2), F(1, 4), 62)])
    seg = Segment(group.id, 0, F(1))
    assert note_problem(group, None, seg, F(0), F(1, 4), 60, False)
    assert note_problem(group, None, seg, F(1, 4), F(1, 4), 60, False) is None
    assert note_problem(group, None, seg, F(7, 8), F(1, 4), 64, False)
    assert note_problem(group, None, seg, F(7, 8), F(1, 4), 64, True) is None
    assert note_problem(group, group.notes[0], seg, F(0), F(1, 2), 60, False) is None


def test_noop_commands_add_no_undo_step():
    # 2026-11-20: un comando che non cambia nulla (es. Velocity con lo stesso valore) non
    # crea un passo di Undo né segna il brano come modificato
    from editor.core.document import Compound, Document, EditNotes, SetAttribute
    from editor.core.model import Group, Note, Song

    song = Song()
    group = Group("g", 1, [Note(F(0), F(1, 4), 60, velocity=90)])
    song.groups[group.id] = group
    doc = Document(song)
    doc.execute(EditNotes([(group, group.notes[0], {"velocity": 90})]))
    doc.execute(Compound([SetAttribute(song, "title", song.title)]))
    assert not doc.can_undo and doc.version == 0
    doc.execute(EditNotes([(group, group.notes[0], {"velocity": 91})]))
    assert doc.can_undo


def test_undo_delete_keeps_group_order():
    # 2026-11-20: dopo Delete + Undo i gruppi tornano al loro posto, non in fondo
    from editor.core.document import DeleteSegments, Document
    from editor.core.model import Group, Note, Segment, Song, Track

    song = Song()
    groups = [Group(f"g{i}", 1, [Note(F(0), F(1, 4), 60)]) for i in range(3)]
    track = Track("t")
    for i, g in enumerate(groups):
        song.groups[g.id] = g
        track.segments.append(Segment(g.id, i, F(1)))
    song.tracks.append(track)
    doc = Document(song)
    before = list(song.groups)
    doc.execute(DeleteSegments([(track, track.segments[0])]))
    doc.undo()
    assert list(song.groups) == before


def test_deleting_original_promotes_a_clone():
    # 2026-11-20: Duplicate e poi Delete del primo: il clone rimasto diventa l'originale
    # (prima restava grigio e non modificabile); Undo rimette tutto com'era
    from editor.core.document import DeleteSegments, Document
    from editor.core.model import Group, Note, Segment, Song, Track

    song = Song()
    group = Group("g", 1, [Note(F(0), F(1, 4), 60)])
    song.groups[group.id] = group
    original = Segment(group.id, 0, F(1), label="Chorus")
    clones = [Segment(group.id, i, F(1), clone_of=original.id) for i in (1, 2)]
    track = Track("t", segments=[original, *clones])
    song.tracks.append(track)
    doc = Document(song)
    doc.execute(DeleteSegments([(track, original)]))
    assert clones[0].clone_of is None and clones[1].clone_of == clones[0].id
    assert clones[0].label == "Chorus"  # l'etichetta passa al nuovo originale
    doc.undo()
    assert [c.clone_of for c in clones] == [original.id, original.id] and clones[0].label == ""
