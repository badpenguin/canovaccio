from editor.core.instrument import GMInstrument
from editor.output.backend import RecordingBackend
from editor.output.devices import MIDI, OutputDevice, dump_devices, load_devices
from editor.output.midi_port_backend import MidiPortBackend, port_name
from editor.output.performance import NoteOn, Performance, TimedEvent, TrackSetup
from editor.output.transport import State, Transport


class FakeClock:
    t = 0.0

    def now(self):
        return self.t


class FakeOut:
    def __init__(self):
        self.sent = []

    def send_message(self, msg):
        self.sent.append(list(msg))


def test_devices_roundtrip_and_default():
    devices, current = load_devices({})
    assert [d.name for d in devices] == ["Internal"] and current is devices[0]
    yamaha = OutputDevice("Yamaha", kind=MIDI, port="Yamaha PSR", program_change=False)
    prefs = dump_devices(devices + [yamaha], yamaha)
    devices2, current2 = load_devices(prefs)
    assert current2.name == "Yamaha" and current2.port == "Yamaha PSR" and not current2.program_change


def test_port_name_drops_system_numbers():
    assert port_name("FluidSynth:FluidSynth 128:0") == "FluidSynth"
    assert port_name("Midi Through:Midi Through Port-0 14:0") == "Midi Through Port-0"
    assert port_name("USB Midi:USB Midi MIDI 1 20:0") == "USB Midi MIDI 1"
    assert port_name("Microsoft GS Wavetable Synth 0") == "Microsoft GS Wavetable Synth"


def test_midi_port_program_change_and_notes():
    b = MidiPortBackend("x")
    b._out = FakeOut()
    tracks = [TrackSetup("a", GMInstrument(24), "default", 1), TrackSetup("d", GMInstrument(0, True), "default", 10)]
    b.setup(tracks)
    assert b._out.sent == [[0xC0, 24]]  # niente Program Change sul canale 10
    b.setup(tracks)
    assert len(b._out.sent) == 1  # durante l'editing solo se cambia
    b.start()
    assert b._out.sent[-1] == [0xC0, 24]  # al play sempre
    b.send(NoteOn("d", 36, 100))
    assert b._out.sent[-1] == [0x99, 36, 100]
    off = MidiPortBackend("x", program_change=False)
    off._out = FakeOut()
    off.setup(tracks)
    off.start()
    assert off._out.sent == []


def test_transport_switches_backend_while_playing():
    perf = Performance([TrackSetup("a", GMInstrument(0), "default", 1)],
                       [TimedEvent(0.0, NoteOn("a", 60, 90)), TimedEvent(1.0, NoteOn("a", 62, 90))], 2.0)
    old, new, clock = RecordingBackend(), RecordingBackend(), FakeClock()
    tr = Transport(old, clock, threaded=False)
    tr.load(perf)
    tr.play()
    tr.tick()
    tr.set_backend(new)
    assert old.panics >= 1 and new.tracks and new.starts == 1 and tr.state is State.PLAYING
    clock.t = 1.0
    tr.tick()
    assert [e.key for e in new.sent] == [62]


def test_loopback_parsing():
    from editor.output.loopback import parse_loopbacks, parse_sources

    text = """Source #1
\tState: SUSPENDED
\tName: alsa_output.pci-0000_00_1f.3.analog-stereo.monitor
\tDescription: Monitor of Built-in Audio
Source #2
\tName: alsa_input.usb-Roland_FANTOM-6_7_8-01.analog-stereo.2
\tDescription: FANTOM-6 7 8 Analog Stereo
"""
    srcs = parse_sources(text)
    assert [s.description for s in srcs] == ["FANTOM-6 7 8 Analog Stereo"]
    assert srcs[0].name == "alsa_input.usb-Roland_FANTOM-6_7_8-01.analog-stereo.2"
    assert srcs[0].stable == "alsa_input.usb-Roland_FANTOM-6_7_8-01.analog-stereo"
    modules = "536870913\tmodule-loopback\tsource=alsa_input.usb-Roland_FANTOM-6_7_8-01.analog-stereo.2 latency_msec=10\t\n" \
              "536870914\tmodule-null-sink\tsink_name=x\t\n"
    assert parse_loopbacks(modules) == [("536870913", "alsa_input.usb-Roland_FANTOM-6_7_8-01.analog-stereo.2")]


def test_track_sound_per_output_on_midi_port_and_project(tmp_path):
    from editor.core.model import Song, Sound, Track
    from editor.core.project import load_project, save_project

    tracks = [TrackSetup("a", GMInstrument(24), "default", 1, {"Yamaha": Sound(5, 104, 3)}),
              TrackSetup("d", GMInstrument(0, True), "default", 10, {"Yamaha": Sound(16, 127, 0)})]
    b = MidiPortBackend("x")
    b.output_name = "Yamaha"
    b._out = FakeOut()
    b.setup(tracks)
    assert b._out.sent == [[0xB0, 0, 104], [0xB0, 32, 3], [0xC0, 5],
                           [0xB9, 0, 127], [0xB9, 32, 0], [0xC9, 16]]  # anche il kit sul canale 10
    other = MidiPortBackend("x")
    other.output_name = "Altra"
    other._out = FakeOut()
    other.setup(tracks)
    assert other._out.sent == [[0xC0, 24]]  # su un'altra uscita: GM

    song = Song()
    song.tracks.append(Track("Chitarra", sounds={"Interno": Sound(24, 8, 0, "/x/Guitar.sf2")}))
    save_project(song, tmp_path / "s.music")
    assert load_project(tmp_path / "s.music").tracks[0].sounds == {"Interno": Sound(24, 8, 0, "/x/Guitar.sf2")}


def test_recent_files(tmp_path, monkeypatch):
    from editor.ui import preferences

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    for i in range(12):
        preferences.add_recent(str(tmp_path / f"f{i}.music"))
    files = preferences.add_recent(str(tmp_path / "f3.music"))
    assert files[0].endswith("f3.music") and len(files) == preferences.MAX_RECENT
    assert files.count(files[0]) == 1 and preferences.recent_files() == files
    assert files[0] not in preferences.remove_recent(files[0])


def test_last_open_file(tmp_path, monkeypatch):
    """File aperto alla chiusura (2026-11-23): si ricorda col percorso assoluto e si dimentica con None."""
    from editor.ui import preferences

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    assert preferences.last_open() is None
    preferences.set_last_open(str(tmp_path / "song.music"))
    assert preferences.last_open() == str((tmp_path / "song.music").resolve())
    preferences.set_last_open(None)
    assert preferences.last_open() is None


def test_patch_lists_rosegarden_plus_corrections():
    from editor.output import patch_lists as pl

    names = pl.list_names()
    assert names == ["GM", "Roland FANTOM EX"]
    gm = pl.load("GM")
    assert len(gm) == 128 and gm[0].name == "Acoustic Grand Piano" and gm[0].key == (0, 0, 0)
    fantom = {p.key: p for p in pl.load("Roland FANTOM EX")}
    assert fantom[(87, 93, 78)].name == "Nylon Guitar"  # PC 79 della Sound List
    assert fantom[(86, 64, 0)].drum and not any(k[0] == 85 for k in fantom)  # kit sì, Scene no
    assert fantom[(101, 64, 0)].group == "EXZ001"  # espansioni
    assert len(fantom) > 7000


def test_generate_style_on_chord_track():
    from fractions import Fraction as F

    from editor.core.arrange import Part, Pattern, StyleData, generation_command, transform
    from editor.core.chord_track import ChordEvent
    from editor.core.document import AddTrack, Document
    from editor.core.model import Group, Note, Segment, Song, Track
    from editor.core.style import StyleRule, Zone

    every = (1 << 35) - 1
    bass_rule = StyleRule("Bass", 11, 11, 0, 2, 0xFFF, every, (Zone(0, 127, 0, 3, 3, 28, 127, 2),))  # Root Trans · Bass
    chord_rule = StyleRule("Piano", 12, 12, 0, 2, 0xFFF, every, (Zone(0, 127, 1, 2, 4, 0, 127, 1),))  # Root Fixed · Chord
    drum_rule = StyleRule("Drum", 10, 10, 0, 2, 0xFFF, every, (Zone(0, 127, 1, 0, 6, 0, 127, 1),))
    bar = F(1)
    data = StyleData("x.sty", [
        Part("11 Bass", 11, False, 33, {"Main A": Pattern(bar, [Note(F(0), F(1, 4), 36)], bass_rule)}),
        Part("12 Piano", 12, False, 0, {"Main A": Pattern(bar, [Note(F(0), F(1, 2), m) for m in (60, 64, 67, 71)],
                                                          chord_rule)}),
        Part("10 Drum", 10, True, 0, {"Main A": Pattern(bar, [Note(F(0), F(1, 8), 36)], drum_rule),
                                      "Fill In AA": Pattern(bar, [Note(F(0), F(1, 8), 49)], drum_rule)}),
    ], {"Main A": bar, "Fill In AA": bar})

    # trasposizione: Am per il basso scritto in C (Root Trans fino a Re#: La va sotto)
    assert transform(36, bass_rule, ChordEvent(F(0), "A", 8)) == 33
    assert sorted(transform(m, chord_rule, ChordEvent(F(0), "A", 8)) for m in (60, 64, 67, 71)) == [57, 60, 64, 69]

    song = Song()
    g = Group("Memoria 1", 3, [], chord_events=[ChordEvent(F(0), "C", 2), ChordEvent(F(1), "A", 8)])
    seg = Segment(g.id, 0, F(3), style="x.sty", section="Main A", fill="Fill In AA")
    ct = Track("Accordi", segments=[seg], kind="chords", channel=1)
    doc = Document(song)
    doc.execute(AddTrack(ct, 0, [g]))
    command, warnings = generation_command(song, [seg], lambda _p: data)
    assert not warnings
    doc.execute(command)
    tracks = {t.channel: t for t in song.tracks if t.kind == "style"}
    assert sorted(tracks) == [10, 11, 12] and tracks[11].name == "Style · Bass"
    bass = [p.midi for p in song.placed_notes(tracks[11])]
    assert bass == [36, 33]  # misura 1 su CMaj7, misura 2 su Am; la 3 è il fill (solo batteria)
    drums = [p.midi for p in song.placed_notes(tracks[10])]
    assert drums == [36, 36, 49]  # batteria invariata, fill nell'ultima misura
    # rigenerare sostituisce (niente doppioni); Ctrl+Z torna indietro in un passo
    doc.execute(generation_command(song, [seg], lambda _p: data)[0])
    assert [p.midi for p in song.placed_notes(tracks[11])] == [36, 33]
    doc.undo()
    doc.undo()
    assert not any(t.kind == "style" for t in song.tracks)
    # 2026-11-20: canale cambiato a mano → la traccia si ritrova per nome; sezione che lo stile
    # non ha → avviso e niente comando (prima il tratto si svuotava)
    doc.execute(generation_command(song, [seg], lambda _p: data)[0])
    bass_track = next(t for t in song.tracks if t.name == "Style · Bass")
    bass_track.channel = 5
    doc.execute(generation_command(song, [seg], lambda _p: data)[0])
    assert sum(t.name == "Style · Bass" for t in song.tracks) == 1 and list(song.placed_notes(bass_track))
    seg.section = "Main D"
    command, warnings = generation_command(song, [seg], lambda _p: data)
    assert command is None and warnings


def test_generation_follows_song_key_and_key_from_chords():
    from fractions import Fraction as F

    from editor.core.arrange import key_scale, transform
    from editor.core.chord_track import ChordEvent, detect_key
    from editor.core.key import Key
    from editor.core.style import StyleRule, Zone

    dm = Key.parse("Dm")
    assert key_scale(31, 2, dm)[2] == 3  # D5 in Re minore: terza minore (dall'armatura)
    assert key_scale(0, 9, dm) == (0, 1, 4, 5, 7, 8, 10)  # A in Re minore: La-Do#-Mi, con Si♭ e Fa della scala
    chord_rule = StyleRule("Piano", 12, 12, 0, 2, 0xFFF, (1 << 35) - 1, (Zone(0, 127, 1, 2, 4, 0, 127, 1),))
    assert sorted(transform(m, chord_rule, ChordEvent(F(0), "A", 0), dm) % 12 for m in (60, 64, 67)) == [1, 4, 9]
    melody = StyleRule("Lead", 15, 15, 0, 2, 0xFFF, (1 << 35) - 1, (Zone(0, 127, 1, 1, 6, 0, 127, 1),))
    # Mi (3ª di C) su D5: in Do maggiore Fa#, in Re minore Fa
    assert transform(64, melody, ChordEvent(F(0), "D", 31)) % 12 == 6
    assert transform(64, melody, ChordEvent(F(0), "D", 31), dm) % 12 == 5
    chords = [(F(0), F(1), ChordEvent(F(0), "D", 8)), (F(1), F(2), ChordEvent(F(0), "Bb", 0)),
              (F(2), F(3), ChordEvent(F(0), "C", 0)), (F(3), F(4), ChordEvent(F(0), "D", 8))]
    assert str(detect_key(chords)) == "Dm"


def test_gm_instrument_from_style_part_names():
    from editor.importer.gm_names import gm_from_name

    assert gm_from_name("BassMain") == 33 and gm_from_name("VeloSlap") == 36  # non l'organo del programma Yamaha
    assert gm_from_name("ClnGt") == 27 and gm_from_name("E.Pno") == 4 and gm_from_name("E.P  rt") == 4
    assert gm_from_name("SteelDr") == 114 and gm_from_name("Steel") == 25
    assert gm_from_name("Maj-min") is None  # il nome non dice niente: resta il programma del file


def test_style_velocities_normalized_per_part():
    from fractions import Fraction as F

    from editor.core.model import Group, Note
    from editor.importer.style import normalize_velocities

    a = Group("Main A", 1, [Note(F(0), F(1, 4), 36, v) for v in (40, 60, 80)])
    b = Group("Intro A", 1, [Note(F(0), F(1, 4), 36, 60)])
    normalize_velocities([a, b])
    # media 60 → 100 vorrebbe ×1.67, ma la più forte (80) arriverebbe oltre 127: ×1.59
    assert [n.velocity for n in a.notes] == [64, 95, 127] and b.notes[0].velocity == 95
    d = Group("Main B", 1, [Note(F(0), F(1, 4), 36, v) for v in (50, 60, 70)])
    normalize_velocities([d])
    assert [n.velocity for n in d.notes] == [83, 100, 117]  # media 60 → 100
    c = Group("Fill", 1, [Note(F(0), F(1, 4), 36, v) for v in (50, 127)])
    normalize_velocities([c])
    assert [n.velocity for n in c.notes] == [50, 127]  # già a 127: non si tocca
    e = Group("Drum", 1, [Note(F(0), F(1, 4), 36, v) for v in (100, 120)])
    normalize_velocities([e])
    assert [n.velocity for n in e.notes] == [100, 120]  # media sopra 100: non si abbassa


def test_stile_file_keeps_sounds_for_generation(tmp_path):
    from fractions import Fraction as F

    from editor.core.arrange import generation_command
    from editor.core.chord_track import ChordEvent
    from editor.core.document import AddTrack, Document
    from editor.core.instrument import GMInstrument
    from editor.core.model import Group, Note, Segment, Song, Sound, Track
    from editor.core.project import save_project
    from editor.core.style import StyleRule, Zone
    from editor.importer.style import load_style_data, style_sections

    rule = StyleRule("Bass", 11, 11, 0, 2, 0xFFF, (1 << 35) - 1, (Zone(0, 127, 0, 3, 3, 28, 127, 2),))
    style = Song()
    g = Group("Main A", 1, [Note(F(0), F(1, 4), 36)], style=rule)
    style.groups[g.id] = g
    style.tracks.append(Track("11 BassMain", GMInstrument(34), segments=[Segment(g.id, 0, F(1), label="Main A")],
                              channel=11, sounds={"Fantom": Sound(33, 87, 93)}))
    path = tmp_path / "bigrock.stile"
    save_project(style, path)
    assert style_sections(path) == [("Main A", 1)]
    data = load_style_data(path)
    assert data.parts[0].sounds == {"Fantom": Sound(33, 87, 93)} and data.parts[0].instrument == GMInstrument(34)

    song = Song()
    cg = Group("Memoria 1", 1, [], chord_events=[ChordEvent(F(0), "A", 8)])
    seg = Segment(cg.id, 0, F(1), style=str(path), section="Main A")
    doc = Document(song)
    doc.execute(AddTrack(Track("Accordi", segments=[seg], kind="chords"), 0, [cg]))
    doc.execute(generation_command(song, [seg], load_style_data)[0])
    bass = next(t for t in song.tracks if t.kind == "style")
    assert bass.instrument == GMInstrument(34) and bass.sounds == {"Fantom": Sound(33, 87, 93)}  # come nello stile
    assert [p.midi for p in song.placed_notes(bass)] == [33]


def test_midi_thru_follows_selected_track_and_releases_correctly():
    from editor.output.midi_input import Thru
    from editor.output.performance import Control, NoteOff, NoteOn, PitchBend

    sent, current = [], ["piano"]
    thru = Thru(sent.append, lambda: current[0])
    thru.handle([0x90, 60, 100])  # nota sulla traccia piano
    thru.handle([0xB0, 64, 127])  # pedale giù
    current[0] = "bass"  # cambio traccia con il tasto premuto
    thru.handle([0x80, 60, 0])  # il rilascio va al piano
    thru.handle([0x90, 40, 0])  # note on a velocity 0 = rilascio
    thru.handle([0xB0, 64, 0])  # pedale su: al piano (era giù lì) e al basso
    thru.handle([0xE0, 0, 64])  # pitch bend al centro
    assert sent[:3] == [NoteOn("piano", 60, 100), Control("piano", 64, 127), NoteOff("piano", 60)]
    assert sent[3] == NoteOff("bass", 40)
    assert set(sent[4:6]) == {Control("piano", 64, 0), Control("bass", 64, 0)}
    assert sent[6] == PitchBend("bass", 0)


def test_recording_punch_in_window_and_take():
    from fractions import Fraction as F

    from editor.core.model import Group, Segment, Song, Track
    from editor.core.recording import build_take, record_window

    song = Song()
    g = Group("A", 1, [])
    song.groups[g.id] = g
    track = Track("Piano", segments=[Segment(g.id, 4, F(1))])  # segmento alla misura 5
    assert record_window(song, track, F(9, 2)) is None  # dentro un segmento: non si parte
    assert record_window(song, track, F(5, 4)) == (F(1), F(4))  # inizio misura 2, limite misura 5
    events = [(F(1), 60, 90), (F(5, 4), 60, None), (F(15, 4), 64, 100), (F(9, 2), 67, 80)]  # l'ultima oltre il limite
    seg, group = build_take(song, F(1), F(4), F(5), events, "Rec 1")
    assert [(n.start, n.duration, n.midi) for n in group.notes] == [(0, F(1, 4), 60), (F(11, 4), F(1, 4), 64)]
    assert seg.start_bar == 1 and seg.length == 3 and seg.label == "Rec 1"  # fino al limite, nota tenuta tagliata
    assert build_take(song, F(1), None, F(2), [], "x") is None


def test_metronome_for_recording():
    from fractions import Fraction as F

    from editor.core.model import Song
    from editor.output.performance import METRONOME, NoteOn, with_metronome

    perf = with_metronome(Song(), F(1), F(2))  # una misura di 4/4 a 120: 4 colpi, accento sul primo
    clicks = [e for e in perf.events if isinstance(e.event, NoteOn) and e.event.track_id == METRONOME]
    assert [c.time for c in clicks] == [2.0, 2.5, 3.0, 3.5]
    assert [c.event.velocity for c in clicks] == [127, 80, 80, 80]
    assert perf.duration >= 4.0 and any(t.track_id == METRONOME and t.channel == 10 for t in perf.tracks)


def test_play_waits_after_program_changes():
    from editor.core.instrument import GMInstrument
    from editor.output.performance import NoteOn, Performance, TimedEvent, TrackSetup
    from editor.output.transport import Transport

    class Clock:
        t = 0.0

        def now(self):
            return self.t

    b, clock = MidiPortBackend("x"), Clock()
    b._out = FakeOut()
    tr = Transport(b, clock, threaded=False)
    tr.load(Performance([TrackSetup("a", GMInstrument(24), "default", 1)], [TimedEvent(0.0, NoteOn("a", 60, 90))], 1.0))
    tr.play()
    tr.tick()
    assert not any(m[0] == 0x90 for m in b._out.sent) and tr.position == 0.0  # Program Change appena mandato: si aspetta
    clock.t = 0.25
    tr.tick()
    assert [0x90, 60, 90] in b._out.sent


def test_file_new_empty_song():
    from editor.session import Session

    s = Session("null")
    doc = s.new_document()
    assert [t.name for t in doc.song.tracks] == ["Track 1"] and doc.path == "" and doc.project_path is None
    seg = doc.song.tracks[0].segments[0]
    assert seg.start_bar == 0 and seg.length == 1 and doc.song.groups[seg.group_id].notes == []
    s.close()


def test_close_document():
    """File > Close (2026-11-23): nessun brano, play fermo, esecuzione vuota; il documento chiuso non
    ricarica più il transport."""
    from editor.output.transport import State
    from editor.session import Session

    s = Session("null")
    doc = s.open_path("examples/small.mid")
    s.transport.play()
    s.close_document()
    assert s.document is None and s.transport.state is State.STOPPED
    assert not s.transport.performance.events
    doc.touch()  # una modifica al brano chiuso non arriva più al transport
    assert not s.transport.performance.events
    s.close_document()  # senza brano non fa nulla
    s.close()


def test_controls_imported_played_and_chased(tmp_path):
    from fractions import Fraction as F
    import mido

    from editor.importer.midi import import_midi
    from editor.output.backend import RecordingBackend
    from editor.output.performance import Control, NoteOn, PitchBend, build_performance
    from editor.output.transport import Transport

    mid = mido.MidiFile(ticks_per_beat=480)
    tr = mido.MidiTrack()
    mid.tracks.append(tr)
    tr += [mido.Message("control_change", channel=0, control=7, value=90),  # volume prima delle note
           mido.Message("note_on", note=60, velocity=80, time=0),
           mido.Message("pitchwheel", pitch=4096, time=480),
           mido.Message("control_change", channel=0, control=64, value=127, time=0),
           mido.Message("note_off", note=60, time=480)]
    path = tmp_path / "c.mid"
    mid.save(path)
    song = import_midi(path)
    events = [(p, e.kind, e.number, e.value) for p, e in song.placed_events(song.tracks[0])]
    assert events == [(0, "cc", 7, 90), (F(1, 4), "bend", 0, 4096), (F(1, 4), "cc", 64, 127)]

    perf = build_performance(song)
    tid = song.tracks[0].id
    assert perf.events[0].event == Control(tid, 7, 90) and isinstance(perf.events[1].event, NoteOn)  # CC prima della nota

    class Clock:
        t = 0.0

        def now(self):
            return self.t

    b = RecordingBackend()
    t = Transport(b, Clock(), threaded=False)
    t.load(perf)
    t.seek(perf.duration * 0.75)  # da metà: il bend e il sustain in vigore si mandano subito
    t.play()
    assert PitchBend(tid, 4096) in b.sent and Control(tid, 64, 127) in b.sent and Control(tid, 7, 90) in b.sent

    from editor.exporter.midi import export_midi

    export_midi(song, tmp_path / "out.mid")  # Save as MIDI: i controlli si esportano
    again = import_midi(tmp_path / "out.mid")
    assert [(e.kind, e.number, e.value) for _p, e in again.placed_events(again.tracks[0])] == [
        ("cc", 7, 90), ("bend", 0, 4096), ("cc", 64, 127)]


def test_recording_keeps_controls_and_releases_pedal_at_end():
    from fractions import Fraction as F

    from editor.core.model import Song
    from editor.core.recording import build_take

    seg, group = build_take(Song(), F(0), None, F(2), [(F(0), 60, 90), (F(1, 2), 60, None)], "Rec 1",
                            [(F(0), "cc", 64, 127), (F(1, 4), "bend", 0, 2000), (F(3, 2), "cc", 64, 0)])
    assert seg.length == 1
    assert [(e.start, e.kind, e.number, e.value) for e in group.events] == [
        (0, "cc", 64, 127), (F(1, 4), "bend", 0, 2000), (1 - F(1, 1920), "cc", 64, 0)]  # pedale su in fondo



def test_mute_and_save_do_not_reload_the_performance():
    # 2026-11-20: Mute/Solo (touch) e Save (mark_saved) non ricostruiscono l'esecuzione (che
    # durante il play tagliava le note di tutte le tracce); una modifica vera sì.
    from editor.core.document import SetAttribute
    from editor.session import Session

    session = Session(backend="null")
    doc = session.open_path("examples/small.mid")
    first = session.transport.performance
    doc.song.tracks[0].mute = True
    doc.touch()
    doc.mark_saved("unused.music")
    assert session.transport.performance is first
    assert doc.song.tracks[0].id in session.transport.silenced
    doc.execute(SetAttribute(doc.song.tracks[0], "velocity", 90))
    assert session.transport.performance is not first


def test_export_midi_with_non_ascii_names(tmp_path):
    # 2026-11-20: titolo, nomi e etichette con caratteri tipografici facevano fallire l'export
    import mido

    from editor.exporter.midi import export_midi
    from editor.importer.midi import import_midi

    song = import_midi("examples/small.mid")
    song.title = "Song — “Verse” F♯"
    song.tracks[0].name = "Piano — Lead"
    song.tracks[0].segments[0].label = "“Intro”"
    out = tmp_path / "x.mid"
    export_midi(song, out)
    mid = mido.MidiFile(out)
    assert mid.tracks[0][0].name == 'Song - "Verse" F#' and mid.tracks[1][0].name == "Piano - Lead"
    assert not (tmp_path / "x.mid.tmp").exists()


def test_meter_change_that_overlaps_segments_is_refused():
    # 2026-11-20: Song Properties rifiuta un metro che farebbe sovrapporre i segmenti
    from editor.core.timeline import MeterTable, TimeSignature
    from editor.importer.midi import import_midi
    from editor.ui.song_dialog import _overlapping_segments

    song = import_midi("examples/long.mid")
    assert _overlapping_segments(song, song.meters) is None
    assert "overlap" in _overlapping_segments(song, MeterTable(TimeSignature(3, 4)))


def test_partial_style_regeneration_and_chord_after_trim():
    # 2026-11-20: rigenerando solo una parte, i segmenti "Style · …" fuori da quel tratto restano
    # (prima sparivano interi); dopo un Resize dell'inizio vale l'accordo in vigore in quel punto
    from fractions import Fraction as F

    from editor.core.arrange import Part, Pattern, StyleData, generation_command
    from editor.core.chord_track import ChordEvent, segment_chords
    from editor.core.document import AddTrack, Document, ResizeSegment, SplitSegment
    from editor.core.model import Group, Note, Segment, Song, Track
    from editor.core.style import StyleRule, Zone

    every = (1 << 35) - 1
    rule = StyleRule("Bass", 11, 11, 0, 2, 0xFFF, every, (Zone(0, 127, 0, 3, 3, 28, 127, 2),))
    data = StyleData("x.sty", [Part("11 Bass", 11, False, 33, {"Main A": Pattern(F(1), [Note(F(0), F(1, 4), 36)], rule)})],
                     {"Main A": F(1)})
    song = Song()
    g = Group("Memory 1", 4, [], chord_events=[ChordEvent(F(0), "C", 2), ChordEvent(F(2), "A", 8)])
    seg = Segment(g.id, 0, F(4), style="x.sty", section="Main A")
    ct = Track("Chords", segments=[seg], kind="chords", channel=1)
    doc = Document(song)
    doc.execute(AddTrack(ct, 0, [g]))
    doc.execute(generation_command(song, [seg], lambda _p: data)[0])
    bass = next(t for t in song.tracks if t.kind == "style")
    assert [p.start for p in song.placed_notes(bass)] == [0, 1, 2, 3]
    doc.execute(SplitSegment(ct, seg, F(2)))
    left = ct.segments[0]
    doc.execute(generation_command(song, [left], lambda _p: data)[0])
    assert [p.start for p in song.placed_notes(bass)] == [0, 1, 2, 3]  # misure 3–4 ancora lì
    right = ct.segments[1]
    doc.execute(ResizeSegment(right, F(5, 2), F(4)))
    assert segment_chords(song, right)[0][1].root == "A"  # accordo in vigore all'inizio


def test_mute_shared_channel_and_unmute_chase():
    # 2026-11-20: due tracce sul canale 1 (mano destra col pedale, mano sinistra): in mute
    # la destra, il suo pedale su arriva comunque (canale condiviso); togliendo il mute a una
    # traccia durante il play le si rimandano i controlli in vigore (volume)
    from editor.output.performance import Control

    tracks = [TrackSetup("rh", GMInstrument(0), "", 1), TrackSetup("lh", GMInstrument(0), "", 1),
              TrackSetup("bass", GMInstrument(33), "", 2)]
    events = [TimedEvent(0.0, Control("bass", 7, 70)), TimedEvent(0.1, Control("rh", 64, 127)),
              TimedEvent(1.0, Control("rh", 64, 0))]
    clock, backend = FakeClock(), RecordingBackend()
    tr = Transport(backend, clock, threaded=False)
    tr.set_silenced({"bass"})
    tr.load(Performance(tracks, events, 2.0))
    tr.play()
    clock.t = 0.5
    tr.tick()
    tr.set_silenced({"bass", "rh"})
    clock.t = 1.5
    tr.tick()
    assert Control("rh", 64, 0) in backend.sent
    assert Control("bass", 7, 70) not in backend.sent
    tr.set_silenced({"rh"})
    assert Control("bass", 7, 70) in backend.sent


def test_panic_resets_controllers_to_gm_defaults():
    # 2026-11-20: pulsante Panic: oltre al panic, volume 100 e pan al centro su ogni canale
    b = MidiPortBackend("x")
    b._out = FakeOut()
    tr = Transport(b, FakeClock(), threaded=False)
    tr.reset()
    assert [0xB0, 121, 0] in b._out.sent and [0xB0 | 15, 7, 100] in b._out.sent and [0xB3, 10, 64] in b._out.sent
    assert tr.state is State.STOPPED
