"""Import dei progetti Rosegarden (.rg): file reale in examples + file sintetici per le regole."""

import gzip
from fractions import Fraction as F
from pathlib import Path

from editor.core.clef import BASS, TREBLE
from editor.core.model import BEND, CC, PRESSURE
from editor.importer.rosegarden import import_rosegarden

# 2026-11-23: file reale pubblicabile al posto di tests/fixtures/bozza1.rg (privato dell'utente, escluso
# da git): examples/demo.mid salvato da Rosegarden. Il test controlla solo i dati di demo.rg: non deve
# restare sincronizzato con demo.mid. Staccato, slur, controller e repeat restano coperti dai file
# sintetici qui sotto.
EXAMPLE = Path(__file__).resolve().parent.parent / "examples" / "demo.rg"
W = 3840  # unità Rosegarden per semibreve


def _rg(segments: str, tmp_path, *, repeat_end: int = 0, extra_tracks: str = "") -> Path:
    """Scrive un .rg sintetico (gzip) con una traccia MIDI sul canale 1, programma 33, 4/4 a 90 BPM."""
    xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<rosegarden-data version="26.06">
<composition endMarker="{repeat_end}" defaultTempo="120.0000">
  <track id="0" label="Basso" position="0" muted="true" solo="false" instrument="2000" defaultClef="0"/>
  {extra_tracks}
  <timesignature time="0" numerator="4" denominator="4"/>
  <tempo time="0" tempo="9000000"/>
</composition>
{segments}
<studio>
  <device id="0" type="midi">
    <instrument id="2000" channel="0" type="midi"><bank percussion="false" msb="0" lsb="0"/><program id="33"/></instrument>
    <instrument id="2001" channel="3" type="midi"><bank percussion="true" msb="120" lsb="0"/><program id="0"/></instrument>
  </device>
</studio>
</rosegarden-data>"""
    path = tmp_path / "prova.rg"
    path.write_bytes(gzip.compress(xml.encode()))
    return path


def _note(dur: int, pitch: int, extra: str = "", attrs: str = "") -> str:
    """Evento nota con altezza, velocity 90 e proprietà aggiuntive."""
    return (f'<event type="note" duration="{dur}"{attrs}><property name="pitch" int="{pitch}"/>'
            f'<property name="velocity" int="90"/>{extra}</event>')


def _placed(song, track):
    """(inizio, durata, altezza) delle note della traccia in posizione assoluta."""
    return [(p.start, p.duration, p.midi) for p in song.placed_notes(track)]


def test_timing_rules_chord_offset_and_endmarker(tmp_path):
    # chord: avanza della durata minima > 0 (qui 480, non la legatura da 1920);
    # timeOffset sposta l'evento rispetto alla fine del precedente; le note dopo
    # l'endmarker si tolgono e quella a cavallo si accorcia
    seg = f"""<segment track="0" start="{W}" endmarker="{W + 2400}">
  <event type="clefchange"><property name="clef" string="bass"/><property name="octaveoffset" int="0"/></event>
  <chord>
    <event type="indication" duration="1920"><property name="indicationtype" string="slur"/><property name="indicationduration" int="1920"/></event>
    {_note(480, 40)}
    {_note(960, 47)}
  </chord>
  {_note(480, 42, attrs=' timeOffset="20"')}
  {_note(960, 43, '<property name="tiedforward" bool="true"/>', attrs=' timeOffset="-20"')}
  {_note(960, 43)}
  {_note(480, 44)}
</segment>"""
    song = import_rosegarden(_rg(seg, tmp_path))
    track = song.tracks[0]
    assert (track.name, track.channel, track.instrument.program, track.clef, track.mute) == ("Basso", 1, 33, BASS, True)
    assert song.tempos.initial == 90.0
    assert _placed(song, track) == [
        (F(W, W), F(480, W), 40), (F(W, W), F(960, W), 47),
        (F(W + 500, W), F(480, W), 42),
        (F(W + 960, W), F(960, W), 43),
        (F(W + 1920, W), F(480, W), 43),  # accorciata all'endmarker (W + 2400)
    ]
    group = song.groups[track.segments[0].group_id]
    assert [n.tie_next for n in sorted(group.notes, key=lambda n: n.start)][3] is True
    assert len(group.slurs) == 1
    assert track.segments[0].length == F(2400, W)


def test_off_beat_segment_is_anticipated_and_sounds_the_same(tmp_path):
    # inizio a metà del secondo movimento: il segmento parte dal movimento, le note restano dove erano
    start = W + 960 + 100
    seg = f"""<segment track="0" start="{start}" endmarker="{start + 960}" label="Riff">{_note(480, 50)}</segment>"""
    song = import_rosegarden(_rg(seg, tmp_path))
    s = song.tracks[0].segments[0]
    assert (s.start_bar, s.offset, s.label) == (1, F(1, 4), "Riff")
    assert _placed(song, song.tracks[0]) == [(F(start, W), F(480, W), 50)]


def test_clef_change_splits_the_segment(tmp_path):
    seg = f"""<segment track="0" start="0" endmarker="{2 * W}" label="Parte">
  <event type="clefchange"><property name="clef" string="treble"/></event>
  {_note(W, 72)}
  <event type="clefchange"><property name="clef" string="bass"/></event>
  {_note(W, 40)}
</segment>"""
    song = import_rosegarden(_rg(seg, tmp_path))
    track = song.tracks[0]
    assert track.clef == TREBLE  # la chiave del primo segmento
    assert [(s.start_bar, s.length, s.label) for s in track.segments] == [
        (0, F(1), "Parte"), (1, F(1), "Parte · Clef: Bass")]
    assert _placed(song, track) == [(F(0), F(1), 72), (F(1), F(1), 40)]


def test_same_clef_restated_does_not_split(tmp_path):
    seg = f"""<segment track="0" start="0" endmarker="{2 * W}">
  <event type="clefchange"><property name="clef" string="bass"/></event>{_note(W, 40)}
  <event type="clefchange"><property name="clef" string="bass"/></event>{_note(W, 41)}
</segment>"""
    assert len(import_rosegarden(_rg(seg, tmp_path)).tracks[0].segments) == 1


def test_repeat_becomes_clones_until_next_segment(tmp_path):
    seg = f"""<segment track="0" start="0" endmarker="{W}" repeat="true">{_note(960, 40)}</segment>
<segment track="0" start="{3 * W + 1920}" endmarker="{4 * W}">{_note(960, 45)}</segment>"""
    song = import_rosegarden(_rg(seg, tmp_path, repeat_end=8 * W))
    segs = song.tracks[0].segments
    first = segs[0]
    # ripetizioni a misura 1, 2 e 3 (l'ultima parziale, fino al segmento successivo)
    assert [(s.start_bar, s.length, s.clone_of) for s in segs[1:4]] == [
        (1, F(1), first.id), (2, F(1), first.id), (3, F(1, 2), first.id)]
    assert all(s.group_id == first.group_id for s in segs[1:4])
    assert [p[0] for p in _placed(song, song.tracks[0])] == [F(0), F(1), F(2), F(3), F(3) + F(1, 2)]


def test_controls_staccato_chord_symbols_and_drums(tmp_path):
    seg = f"""<segment track="0" start="0" endmarker="{W}">
  {_note(480, 40, '<property name="marks" int="1"/><property name="mark1" string="staccato"/>')}
  <event type="text"><property name="text" string="Am7"/><property name="type" string="Chord"/></event>
  <event type="controller"><property name="number" int="0"/><property name="value" int="1"/></event>
  <event type="controller"><property name="number" int="1"/><property name="value" int="26"/></event>
  <event type="pitchbend"><property name="msb" int="96"/><property name="lsb" int="0"/></event>
  <event type="channelpressure"><property name="pressure" int="50"/></event>
</segment>
<segment track="1" start="0" endmarker="{W}">{_note(240, 36)}</segment>"""
    drums = '<track id="1" label="" position="1" instrument="2001"/>'
    song = import_rosegarden(_rg(seg, tmp_path, extra_tracks=drums))
    group = song.groups[song.tracks[0].segments[0].group_id]
    assert group.notes[0].staccato
    assert group.chords == [(F(480, W), "Am7")]
    # il Bank Select (CC 0) non entra: è il suono della traccia
    assert [(e.kind, e.number, e.value) for e in group.events] == [(CC, 1, 26), (BEND, 0, 4096), (PRESSURE, 0, 50)]
    kit = song.tracks[1]
    assert (kit.name, kit.channel, kit.instrument.is_percussion) == ("4 Drums", 4, True)


def test_real_file_demo_rg():
    """demo.rg (file reale salvato da Rosegarden): tabelle globali, tracce e note."""
    song = import_rosegarden(EXAMPLE)
    assert song.title == "demo"
    assert [(e.at, e.value) for e in song.tempos] == [(F(0), 96.0), (F(4), 110.0)]  # 110 BPM dalla misura 5
    assert str(song.keys.at(0)) == "Bm" and str(song.meters.at(0)) == "4/4"
    assert [(t.name, t.channel) for t in song.tracks] == [
        ("Melodia", 1), ("Accordi (suonati)", 2), ("Basso", 3), ("Batteria", 10)]
    drum = song.tracks[3]
    assert drum.instrument.is_percussion and not any(t.instrument.is_percussion for t in song.tracks[:3])
    assert song.tracks[2].clef == BASS and song.tracks[0].clef == TREBLE
    assert [len(_placed(song, t)) for t in song.tracks] == [24, 48, 8, 96]
    assert _placed(song, song.tracks[0])[:3] == [(F(0), F(1, 4), 71), (F(1, 4), F(1, 4), 74), (F(1, 2), F(3, 8), 73)]
    assert _placed(song, song.tracks[1])[0] == (F(0), F(83, 192), 59)  # accordi suonati: durate non a griglia
    assert _placed(song, song.tracks[2])[:3] == [(F(0), F(1), 47), (F(1), F(1), 47), (F(2), F(1), 43)]
    assert _placed(song, drum)[:3] == [(F(0), F(1, 16), 36), (F(0), F(1, 16), 42), (F(1, 8), F(1, 16), 42)]


def test_session_opens_rg(tmp_path):
    from editor.session import Session

    session = Session(backend="null")
    try:
        doc = session.open_path(EXAMPLE)
        assert doc.project_path is None  # importato: Save chiede dove salvare il .music
        assert len(doc.song.tracks) == 4
    finally:
        session.close()
