"""Import delle partiture MuseScore (.mscz/.mscx 2.x–4.x) e MusicXML (2026-11-23).

File reali: examples/demo.mscz (MuseScore 2.3.2, formato 2.06) e examples/demo.musicxml (MusicXML 3.1).
Per i formati 3.x, 4.x e MusicXML
file sintetici con le cose che si importano: voci, terzine, legature di valore, levare, sigle,
staccato, abbellimenti (ignorati), pianoforte su due pentagrammi, batteria, strumento traspositore.
"""

import zipfile
from fractions import Fraction as F
from pathlib import Path

import pytest

from editor.core.clef import BASS, PERCUSSION, TREBLE
from editor.importer.musescore import import_musescore
from editor.importer.musicxml import import_musicxml, is_musicxml

EXAMPLES = Path(__file__).resolve().parent.parent / "examples"


def _placed(song, track):
    """(inizio, durata, altezza) delle note della traccia in posizione assoluta."""
    return [(p.start, p.duration, p.midi) for p in song.placed_notes(track)]


def _notes(song, track):
    """Note della traccia (oggetti Note) in ordine di posizione, con la posizione assoluta."""
    return [(p.start, p.note) for p in song.placed_notes(track)]


def test_musescore_2_real_file_demo():
    song = import_musescore(EXAMPLES / "demo.mscz")
    assert song.title == "demo"
    assert [(e.at, e.value) for e in song.tempos] == [(F(0), 96.0), (F(4), 110.0)]
    assert str(song.keys.at(0)) == "D"  # 2 diesis; il modo minore la 2.x non lo salva
    assert [(t.name, t.channel, t.instrument.is_percussion) for t in song.tracks] == [
        ("Flute, Melodia", 1, False), ("Piano, Accordi (suonati)", 2, False),
        ("Contrabass, Basso", 3, False), ("Drumset, Batteria", 10, True)]
    assert [t.clef for t in song.tracks] == [TREBLE, TREBLE, BASS, PERCUSSION]  # contrabbasso F8vb → basso
    assert [getattr(t.instrument, "program", 0) for t in song.tracks[:3]] == [73, 0, 32]
    melody = _notes(song, song.tracks[0])
    assert len(melody) == 24
    assert [(s, str(n.spelling), n.velocity) for s, n in melody[:3]] == [(F(0), "B", 90), (F(1, 4), "D", 80),
                                                                           (F(1, 2), "C#", 80)]
    assert str(melody[4][1].spelling) == "A#"  # La# (non Si♭)
    # misura 3: terzina di crome
    assert [(s, n.duration) for s, n in melody[7:10]] == [(F(2), F(1, 12)), (F(25, 12), F(1, 12)), (F(13, 6), F(1, 12))]
    assert len(_placed(song, song.tracks[2])) == 8 and len(_placed(song, song.tracks[3])) == 96


def test_musicxml_real_file_demo():
    """examples/demo.musicxml: esportato dall'utente da MuseScore (MusicXML 3.1 in chiaro).
    2026-11-23: si controllano solo i dati di questo file, senza confrontarlo con demo.mscz: gli
    esempi fatti a mano non devono restare sincronizzati fra loro (decisione dell'utente)."""
    song = import_musicxml(EXAMPLES / "demo.musicxml")
    assert song.title == "demo"
    assert [(e.at, e.value) for e in song.tempos] == [(F(0), 96.0), (F(4), 110.0)]
    assert str(song.keys.at(0)) == "D" and str(song.meters.at(0)) == "4/4"
    assert [(t.name, t.channel, t.clef, t.instrument.is_percussion) for t in song.tracks] == [
        ("Flute, Melodia", 1, TREBLE, False), ("Piano, Accordi (suonati)", 2, TREBLE, False),
        ("Contrabass, Basso", 3, BASS, False), ("Drumset, Batteria", 10, PERCUSSION, True)]
    assert [getattr(t.instrument, "program", 0) for t in song.tracks[:3]] == [73, 0, 32]
    assert [len(_placed(song, t)) for t in song.tracks] == [24, 67, 8, 96]
    melody = _notes(song, song.tracks[0])
    assert [(s, str(n.spelling)) for s, n in melody[:3]] == [(F(0), "B"), (F(1, 4), "D"), (F(1, 2), "C#")]
    assert str(melody[4][1].spelling) == "A#"
    assert [(s, n.duration) for s, n in melody[7:10]] == [(F(2), F(1, 12)), (F(25, 12), F(1, 12)), (F(13, 6), F(1, 12))]
    # contrabbasso: scritto un'ottava sopra (<transpose> di sole ottave), suona 47 e tiene la grafia
    bass = _notes(song, song.tracks[2])
    assert [(s, n.midi, str(n.spelling)) for s, n in bass[:3]] == [(F(0), 47, "B"), (F(1), 47, "B"), (F(2), 43, "G")]


def _mscx(version: str, keysig: str) -> str:
    """Partitura MuseScore 3.x/4.x sintetica: pianoforte (2 pentagrammi, canale salvato 2 → 3) e batteria."""
    tie_next = '<Spanner type="Tie"><Tie/><next><location><measures>1</measures></location></next></Spanner>'
    tie_prev = '<Spanner type="Tie"><prev><location><measures>-1</measures></location></prev></Spanner>'
    stacc = "<Articulation><subtype>articStaccatoAbove</subtype></Articulation>"

    def chord(dur, pitch, tpc, extra="", note_extra=""):
        return (f"<Chord><durationType>{dur}</durationType>{extra}<Note><pitch>{pitch}</pitch><tpc>{tpc}</tpc>"
                f"{note_extra}</Note></Chord>")

    pickup_rest = '<Measure len="1/4"><voice><Rest><durationType>quarter</durationType></Rest></voice></Measure>'
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<museScore version="{version}">
<Score>
  <Division>480</Division>
  <metaTag name="copyright">(C) Test</metaTag>
  <Part>
    <Staff id="1"><StaffType group="pitched"/></Staff>
    <Staff id="2"><StaffType group="pitched"/><defaultClef>F</defaultClef></Staff>
    <trackName>Piano</trackName>
    <Instrument><longName>Piano</longName><Channel><program value="0"/><midiPort>0</midiPort><midiChannel>2</midiChannel></Channel></Instrument>
  </Part>
  <Part>
    <Staff id="3"><StaffType group="percussion"/></Staff>
    <trackName>Drumset</trackName>
    <Instrument><useDrumset>1</useDrumset><Channel><program value="0"/></Channel></Instrument>
  </Part>
  <Staff id="1">
    <Measure len="1/4"><voice>
      {keysig}
      <TimeSig><sigN>4</sigN><sigD>4</sigD></TimeSig>
      <Tempo><tempo>2</tempo></Tempo>
      {chord("quarter", 69, 17)}
    </voice></Measure>
    <Measure>
      <voice>
        <Harmony><root>16</root><name>m7</name><base>13</base></Harmony>
        {chord("eighth", 64, 18, "<acciaccatura/>")}
        {chord("half", 62, 16, stacc, "<velocity>100</velocity><veloType>user</veloType>")}
        <Tuplet><normalNotes>2</normalNotes><actualNotes>3</actualNotes><baseNote>eighth</baseNote></Tuplet>
        {chord("eighth", 65, 13)}
        {chord("eighth", 67, 15)}
        {chord("eighth", 70, 12, "", tie_next)}
        <endTuplet/>
        {chord("quarter", 74, 16)}
      </voice>
      <voice>
        <location><fractions>1/2</fractions></location>
        {chord("half", 57, 17)}
      </voice>
    </Measure>
    <Measure><voice>
      {chord("quarter", 70, 12, "", tie_prev)}
      <Rest><durationType>quarter</durationType></Rest>
      <Tempo><tempo>1.5</tempo></Tempo>
      {chord("half", 60, 14)}
    </voice></Measure>
  </Staff>
  <Staff id="2">
    {pickup_rest}
    <Measure><voice>{chord("whole", 50, 16)}</voice></Measure>
    <Measure><voice><Rest><durationType>measure</durationType><duration>4/4</duration></Rest></voice></Measure>
  </Staff>
  <Staff id="3">
    {pickup_rest}
    <Measure><voice>{chord("quarter", 36, 14)}{chord("quarter", 38, 16, stacc)}<Rest><durationType>half</durationType></Rest></voice></Measure>
  </Staff>
</Score>
</museScore>"""


def _check_synthetic_musescore(song):
    """Verifiche comuni alle partiture sintetiche 3.x e 4.x."""
    assert song.copyright == "(C) Test"
    assert str(song.keys.at(0)) == "Dm" and str(song.meters.at(0)) == "4/4"
    assert [(e.at, e.value) for e in song.tempos] == [(F(0), 120.0), (F(5, 2), 90.0)]  # il primo vale da 0
    piano, left, drums = song.tracks
    assert [(t.name, t.channel, t.clef) for t in song.tracks] == [
        ("Piano", 3, TREBLE), ("Piano (2)", 3, BASS), ("Drumset", 10, PERCUSSION)]
    third = F(1, 12)
    assert _placed(song, piano) == [
        (F(3, 4), F(1, 4), 69),  # levare: allineato a destra nella prima misura
        (F(1), F(1, 2), 62),  # l'acciaccatura prima si ignora
        (F(3, 2), F(1, 2), 57), (F(3, 2), third, 65), (F(3, 2) + third, third, 67), (F(3, 2) + 2 * third, third, 70),
        (F(7, 4), F(1, 4), 74),
        (F(2), F(1, 4), 70), (F(5, 2), F(1, 2), 60)]
    notes = dict(((s, n.midi), n) for s, n in _notes(song, piano))
    d = notes[(F(1), 62)]
    assert d.staccato and d.velocity == 100 and str(d.spelling) == "D"
    assert notes[(F(3, 2), 57)].voice == 2 and notes[(F(3, 2), 65)].voice == 1
    assert notes[(F(3, 2) + 2 * third, 70)].tie_next and str(notes[(F(3, 2) + 2 * third, 70)].spelling) == "Bb"
    assert not notes[(F(2), 70)].tie_next
    assert [s.start_bar for s in piano.segments] == [0]
    group = song.groups[piano.segments[0].group_id]
    assert group.chords == [(F(1), "Dm7/F")]
    assert [(s.start_bar, s.length) for s in left.segments] == [(1, F(1))]
    assert _placed(song, drums) == [(F(1), F(1, 4), 36), (F(5, 4), F(1, 4), 38)]
    assert not any(n.staccato or n.spelling for _s, n in _notes(song, drums))  # batteria: niente staccato e grafia


def test_musescore_3_synthetic(tmp_path):
    path = tmp_path / "s3.mscx"
    path.write_text(_mscx("3.02", "<KeySig><accidental>-1</accidental><mode>minor</mode></KeySig>"))
    _check_synthetic_musescore(import_musescore(path))


def test_musescore_4_synthetic_mscz(tmp_path):
    path = tmp_path / "s4.mscz"
    container = ('<?xml version="1.0"?><container><rootfiles><rootfile full-path="s4.mscx"/></rootfiles>'
                 '</container>')
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("META-INF/container.xml", container)
        z.writestr("s4.mscx", _mscx("4.20", "<KeySig><concertKey>-1</concertKey><mode>minor</mode></KeySig>"))
    _check_synthetic_musescore(import_musescore(path))


def test_musescore_1_rejected(tmp_path):
    path = tmp_path / "old.mscx"
    path.write_text('<museScore version="1.14"><Score/></museScore>')
    with pytest.raises(ValueError, match="MuseScore 2 or later"):
        import_musescore(path)


MUSICXML = """<?xml version="1.0" encoding="UTF-8"?>
<score-partwise version="3.1">
  <identification><rights>© 2026 Test</rights></identification>
  <part-list>
    <score-part id="P1"><part-name>Piano</part-name>
      <midi-instrument id="P1-I1"><midi-channel>1</midi-channel><midi-program>1</midi-program></midi-instrument></score-part>
    <score-part id="P2"><part-name>Clarinet in Bb</part-name>
      <midi-instrument id="P2-I1"><midi-channel>2</midi-channel><midi-program>72</midi-program></midi-instrument></score-part>
    <score-part id="P3"><part-name>Drumset</part-name>
      <score-instrument id="P3-I36"><instrument-name>Bass Drum</instrument-name></score-instrument>
      <score-instrument id="P3-I39"><instrument-name>Snare</instrument-name></score-instrument>
      <midi-instrument id="P3-I36"><midi-channel>10</midi-channel><midi-unpitched>37</midi-unpitched></midi-instrument>
      <midi-instrument id="P3-I39"><midi-channel>10</midi-channel><midi-unpitched>39</midi-unpitched></midi-instrument></score-part>
  </part-list>
  <part id="P1">
    <measure number="0" implicit="yes">
      <attributes><divisions>6</divisions><key><fifths>-1</fifths><mode>minor</mode></key>
        <time><beats>4</beats><beat-type>4</beat-type></time><staves>2</staves>
        <clef number="1"><sign>G</sign><line>2</line></clef><clef number="2"><sign>F</sign><line>4</line></clef></attributes>
      <direction><sound tempo="100"/></direction>
      <note><pitch><step>A</step><octave>4</octave></pitch><duration>6</duration><voice>1</voice><staff>1</staff></note>
      <backup><duration>6</duration></backup>
      <note><rest/><duration>6</duration><voice>5</voice><staff>2</staff></note>
    </measure>
    <measure number="1">
      <harmony><root><root-step>D</root-step></root><kind text="m7">minor-seventh</kind><bass><bass-step>F</bass-step></bass></harmony>
      <note><grace/><pitch><step>E</step><octave>4</octave></pitch><voice>1</voice><staff>1</staff></note>
      <note><pitch><step>D</step><octave>4</octave></pitch><duration>12</duration><voice>1</voice><staff>1</staff>
        <notations><articulations><staccato/></articulations></notations></note>
      <note><pitch><step>F</step><octave>4</octave></pitch><duration>2</duration><voice>1</voice>
        <time-modification><actual-notes>3</actual-notes><normal-notes>2</normal-notes></time-modification><staff>1</staff></note>
      <note><pitch><step>G</step><octave>4</octave></pitch><duration>2</duration><voice>1</voice>
        <time-modification><actual-notes>3</actual-notes><normal-notes>2</normal-notes></time-modification><staff>1</staff></note>
      <note><pitch><step>B</step><alter>-1</alter><octave>4</octave></pitch><duration>2</duration><tie type="start"/><voice>1</voice>
        <time-modification><actual-notes>3</actual-notes><normal-notes>2</normal-notes></time-modification><staff>1</staff></note>
      <note><pitch><step>D</step><octave>5</octave></pitch><duration>6</duration><voice>1</voice><staff>1</staff></note>
      <backup><duration>24</duration></backup>
      <note><pitch><step>D</step><octave>3</octave></pitch><duration>24</duration><voice>5</voice><staff>2</staff></note>
      <note><chord/><pitch><step>A</step><octave>3</octave></pitch><duration>24</duration><voice>5</voice><staff>2</staff></note>
    </measure>
    <measure number="2">
      <note><pitch><step>B</step><alter>-1</alter><octave>4</octave></pitch><duration>6</duration><tie type="stop"/><voice>1</voice><staff>1</staff></note>
      <forward><duration>6</duration></forward>
      <direction><sound tempo="90"/></direction>
      <note><pitch><step>C</step><octave>4</octave></pitch><duration>12</duration><voice>1</voice><staff>1</staff></note>
    </measure>
  </part>
  <part id="P2">
    <measure number="0" implicit="yes">
      <attributes><divisions>1</divisions><key><fifths>1</fifths></key><clef><sign>G</sign><line>2</line></clef>
        <transpose><diatonic>-1</diatonic><chromatic>-2</chromatic></transpose></attributes>
      <note><rest/><duration>1</duration></note>
    </measure>
    <measure number="1"><note><pitch><step>E</step><octave>4</octave></pitch><duration>4</duration></note></measure>
  </part>
  <part id="P3">
    <measure number="0" implicit="yes">
      <attributes><divisions>1</divisions><clef><sign>percussion</sign></clef></attributes>
      <note><rest/><duration>1</duration></note>
    </measure>
    <measure number="1">
      <note><unpitched><display-step>F</display-step><display-octave>4</display-octave></unpitched><duration>1</duration>
        <instrument id="P3-I36"/><notations><articulations><staccato/></articulations></notations></note>
      <note><unpitched><display-step>C</display-step><display-octave>5</display-octave></unpitched><duration>1</duration>
        <instrument id="P3-I39"/></note>
      <note><rest/><duration>2</duration></note>
    </measure>
  </part>
</score-partwise>
"""


def _check_musicxml(song):
    """Verifiche sulla partitura MusicXML sintetica."""
    assert song.copyright == "© 2026 Test"
    assert str(song.keys.at(0)) == "Dm"  # dalla prima parte (il clarinetto scritto in Mi minore non conta)
    assert [(e.at, e.value) for e in song.tempos] == [(F(0), 100.0), (F(5, 2), 90.0)]
    assert [(t.name, t.channel, t.clef, t.instrument.is_percussion) for t in song.tracks] == [
        ("Piano", 1, TREBLE, False), ("Piano (2)", 1, BASS, False),
        ("Clarinet in Bb", 2, TREBLE, False), ("Drumset", 10, PERCUSSION, True)]
    assert getattr(song.tracks[2].instrument, "program", 0) == 71
    piano, left, clarinet, drums = song.tracks
    third = F(1, 12)
    assert _placed(song, piano) == [
        (F(3, 4), F(1, 4), 69), (F(1), F(1, 2), 62),
        (F(3, 2), third, 65), (F(3, 2) + third, third, 67), (F(3, 2) + 2 * third, third, 70), (F(7, 4), F(1, 4), 74),
        (F(2), F(1, 4), 70), (F(5, 2), F(1, 2), 60)]
    notes = dict(((s, n.midi), n) for s, n in _notes(song, piano))
    assert notes[(F(1), 62)].staccato and str(notes[(F(3, 2) + 2 * third, 70)].spelling) == "Bb"
    assert notes[(F(3, 2) + 2 * third, 70)].tie_next and not notes[(F(2), 70)].tie_next
    assert song.groups[piano.segments[0].group_id].chords == [(F(1), "Dm7/F")]
    assert _placed(song, left) == [(F(1), F(1), 50), (F(1), F(1), 57)]
    assert {n.voice for _s, n in _notes(song, left)} == {1}  # voce 5 (2° pentagramma) → 1
    assert _placed(song, clarinet) == [(F(1), F(1), 62)]  # Mi scritto → Re reale (clarinetto in Si♭)
    assert _notes(song, clarinet)[0][1].spelling is None
    assert _placed(song, drums) == [(F(1), F(1, 4), 36), (F(5, 4), F(1, 4), 38)]
    assert not any(n.staccato for _s, n in _notes(song, drums))


def test_musicxml_partwise(tmp_path):
    path = tmp_path / "score.musicxml"
    path.write_text(MUSICXML, encoding="utf-8")
    _check_musicxml(import_musicxml(path))


def test_musicxml_compressed_mxl_and_plain_xml(tmp_path):
    path = tmp_path / "score.mxl"
    container = ('<?xml version="1.0"?><container><rootfiles><rootfile full-path="score.xml"/></rootfiles>'
                 '</container>')
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("META-INF/container.xml", container)
        z.writestr("score.xml", MUSICXML)
    _check_musicxml(import_musicxml(path))
    plain = tmp_path / "score.xml"
    plain.write_text(MUSICXML, encoding="utf-8")
    other = tmp_path / "other.xml"
    other.write_text("<config/>")
    assert is_musicxml(plain) and not is_musicxml(other)  # .xml solo se è una partitura


def test_musicxml_timewise_rejected(tmp_path):
    path = tmp_path / "t.musicxml"
    path.write_text('<score-timewise version="3.1"/>')
    with pytest.raises(ValueError, match="score-partwise"):
        import_musicxml(path)


def test_session_opens_scores(tmp_path):
    from editor.session import Session

    xml = tmp_path / "score.musicxml"
    xml.write_text(MUSICXML, encoding="utf-8")
    session = Session(backend="null")
    try:
        for path, tracks in ((EXAMPLES / "demo.mscz", 4), (xml, 4)):
            doc = session.open_path(path)
            assert doc.project_path is None and len(doc.song.tracks) == tracks  # importato: Save chiede dove
    finally:
        session.close()
