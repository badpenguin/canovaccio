"""Mixer (2026-11-24): volume in vigore, Hold (Lock) nel transport, scrittura dei CC7 a inizio misura (2026-11-25)."""

from fractions import Fraction

from editor.core.document import Document
from editor.core.mixer import (DEFAULT_VOLUME, bar_volume_events, delete_volume_event, volume_at, volume_bars,
                               write_volumes)
from editor.core.model import CC, ControlEvent, Group, Note, Segment, Song, Track
from editor.output.backend import RecordingBackend
from editor.output.performance import Control, build_performance
from editor.output.transport import Transport


class FakeClock:
    def __init__(self) -> None:
        self.t = 0.0

    def now(self) -> float:
        return self.t


def _song() -> tuple[Song, Track]:
    """Una traccia con due segmenti (il secondo clone del primo) e un terzo indipendente:
    CC7 = 90 all'inizio del primo gruppo, 60 a metà del terzo."""
    song = Song()
    g1 = Group("A", 1, [Note(Fraction(0), Fraction(1, 4), 60)],
               events=[ControlEvent(Fraction(0), CC, 7, 90), ControlEvent(Fraction(0), CC, 10, 30)])
    g2 = Group("B", 1, [Note(Fraction(0), Fraction(1, 4), 62)], events=[ControlEvent(Fraction(1, 2), CC, 7, 60)])
    song.groups[g1.id], song.groups[g2.id] = g1, g2
    a = Segment(g1.id, 0, Fraction(1))
    clone = Segment(g1.id, 1, Fraction(1), clone_of=a.id)
    b = Segment(g2.id, 2, Fraction(1))
    track = Track("Piano", segments=[a, clone, b], channel=1)
    song.tracks.append(track)
    return song, track


def test_volume_at_follows_the_cc7_of_the_song():
    song, track = _song()
    assert volume_at(song, track, Fraction(0)) == 90
    assert volume_at(song, track, Fraction(2)) == 90  # il terzo segmento comincia ancora a 90
    assert volume_at(song, track, Fraction(5, 2)) == 60  # dopo il CC7 a metà
    assert volume_at(song, Track("Empty"), Fraction(0)) == DEFAULT_VOLUME


def test_write_changes_the_cc7_at_the_bar_start_or_adds_one():
    """Write (2026-11-25): nella misura 3 il CC7 a metà resta e se ne aggiunge uno all'inizio; nella misura 1 cambia
    quello che c'è (e, nel clone, anche nella misura 2); un passo di Ctrl+Z."""
    song, track = _song()
    doc = Document(song)
    doc.execute(write_volumes(song, {track.id: {0: 70, 2: 50}}))
    volumes = [(pos, e.value) for pos, e in song.placed_events(track) if e.kind == CC and e.number == 7]
    assert volumes == [(Fraction(0), 70), (Fraction(1), 70), (Fraction(2), 50), (Fraction(5, 2), 60)]
    assert any(e.number == 10 for _p, e in song.placed_events(track))  # il pan resta
    doc.undo()
    assert volume_at(song, track, Fraction(2)) == 90 and volume_at(song, track, Fraction(0)) == 90


def test_write_skips_same_values():
    song, track = _song()
    assert write_volumes(song, {track.id: {0: 90}}) is None  # già quel valore
    assert write_volumes(song, {}) is None


def test_write_creates_a_volume_segment_where_the_track_is_empty():
    """2026-11-25, scelta dell'utente: dove la traccia non ha segmenti Write crea un segmento "Volume" con solo il CC7;
    misure vuote di fila scritte insieme stanno in un segmento solo; uno che comincia a metà misura accorcia il nuovo."""
    song, track = _song()
    later = Group("C", 1, [Note(Fraction(0), Fraction(1, 4), 64)])
    song.groups[later.id] = later
    track.segments.append(Segment(later.id, 8, Fraction(1), offset=Fraction(1, 2)))  # comincia a metà misura 9
    doc = Document(song)
    doc.execute(write_volumes(song, {track.id: {3: 40, 5: 50, 6: 55, 8: 70}}))
    created = [s for s in track.segments if song.groups[s.group_id].name == "Volume"]
    spans = [song.segment_span(s) for s in created]
    assert spans == [(3, 4), (5, 7), (8, Fraction(17, 2))]
    assert [song.groups[s.group_id].notes for s in created] == [[], [], []]
    volumes = [(pos, e.value) for pos, e in song.placed_events(track) if e.number == 7 and pos >= 3]
    assert volumes == [(3, 40), (5, 50), (6, 55), (8, 70)]
    doc.undo()  # un passo solo
    assert not [s for s in track.segments if song.groups[s.group_id].name == "Volume"]
    assert len(song.groups) == 3


def test_delete_removes_an_empty_volume_segment():
    """La X sull'ultimo CC7 di un segmento "Volume" toglie anche il segmento (2026-11-25); Ctrl+Z lo rimette."""
    song, track = _song()
    doc = Document(song)
    doc.execute(write_volumes(song, {track.id: {4: 40}}))
    [(_t, group, event)] = bar_volume_events(song, 4)
    doc.execute(delete_volume_event(song, group, event))
    assert len(track.segments) == 3 and group.id not in song.groups
    doc.undo()
    assert len(track.segments) == 4 and volume_at(song, track, Fraction(4)) == 40


def test_lock_replaces_the_song_cc7_while_playing():
    song, track = _song()
    backend, clock = RecordingBackend(), FakeClock()
    tr = Transport(backend, clock, threaded=False)
    tr.load(build_performance(song))
    tr.set_volume_lock(track.id, 40)
    assert backend.sent[-1] == Control(track.id, 7, 40)  # subito
    backend.sent.clear()
    tr.play()
    clock.t = 100.0
    tr.tick()
    volumes = [e.value for e in backend.sent if isinstance(e, Control) and e.control == 7]
    assert volumes and set(volumes) == {40}  # i CC7 del brano (90, 60) diventano 40
    tr.set_volume_lock(track.id, None)  # senza Lock: torna il volume del brano
    assert tr.volume_locks == {}


def test_try_without_lock_lasts_until_the_next_song_cc7():
    """Cursore mosso senza Lock: si sente subito, il prossimo CC7 del brano lo rimette com'è nel brano."""
    song, track = _song()
    backend, clock = RecordingBackend(), FakeClock()
    tr = Transport(backend, clock, threaded=False)
    tr.load(build_performance(song))
    tr.try_volume(track.id, 30)
    assert backend.sent[-1] == Control(track.id, 7, 30) and tr.volume_tries == {track.id: 30}
    tr.play()
    tr.tick()  # parte il CC7 del brano a tempo 0 (90): la prova finisce
    assert tr.volume_tries == {}
    assert Control(track.id, 7, 90) in backend.sent


def test_try_with_lock_moves_the_lock():
    song, track = _song()
    tr = Transport(RecordingBackend(), FakeClock(), threaded=False)
    tr.load(build_performance(song))
    tr.set_volume_lock(track.id, 40)
    tr.try_volume(track.id, 70)
    assert tr.volume_locks == {track.id: 70} and tr.volume_tries == {}


def test_forget_tries_resends_the_song_volume():
    song, track = _song()
    backend = RecordingBackend()
    tr = Transport(backend, FakeClock(), threaded=False)
    tr.load(build_performance(song))
    tr.seek(1.0)  # dopo il CC7 iniziale: il chase lo ritrova
    tr.try_volume(track.id, 30)
    sent = len(backend.sent)
    tr.forget_tries()
    assert tr.volume_tries == {} and Control(track.id, 7, 90) in backend.sent[sent:]


def test_volume_bars_only_on_the_first_beat():
    """Righello (2026-11-24): segno solo dove un CC7 cade esattamente su X:1:0 (il 60 a metà misura 3 no)."""
    song, _track = _song()
    assert volume_bars(song) == {0, 1}  # il primo segmento e il suo clone


def test_bar_volume_events_and_delete():
    """La X del mixer: il CC7 all'inizio della misura dove sta il play esce dal brano (nel clone: da tutte le copie)."""
    song, track = _song()
    doc = Document(song)
    assert bar_volume_events(song, 2) == []  # CC7 solo a metà misura
    [(found, group, event)] = bar_volume_events(song, 1)  # nel clone: il gruppo è quello condiviso
    assert found is track and event.value == 90
    doc.execute(delete_volume_event(song, group, event))
    assert volume_bars(song) == set()
    assert any(e.number == 10 for e in group.events)  # il pan resta
    doc.undo()
    assert bar_volume_events(song, 0)[0][2].value == 90
