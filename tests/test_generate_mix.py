"""Generate porta nelle tracce "Style · …" volume, pan, reverb e chorus delle parti dello stile
(2026-11-24: prima copiava solo le note e quelle tracce suonavano col volume di default)."""

from fractions import Fraction
from pathlib import Path

from editor.core.arrange import generation_command
from editor.core.chord_track import CHORDS, ChordEvent
from editor.core.document import Document
from editor.core.model import CC, Group, Segment, Song, Track
from editor.importer.style import load_style_data

DEMO_STY = str(Path(__file__).resolve().parent.parent / "examples" / "demo.sty")


def _generated(fill: str = "") -> Song:
    """Brano con un segmento accordi di 4 misure su Main A di demo.sty (col fill indicato), già generato."""
    song = Song()
    group = Group("Verse", 4, [], chord_events=[ChordEvent(Fraction(0), "C", 0), ChordEvent(Fraction(2), "F", 0)])
    song.groups[group.id] = group
    seg = Segment(group.id, 0, Fraction(4), style=DEMO_STY, section="Main A", fill=fill)
    song.tracks.append(Track("Chords", segments=[seg], kind=CHORDS, styles=[DEMO_STY]))
    command, warnings = generation_command(song, [seg], load_style_data)
    assert not warnings
    Document(song).execute(command)
    return song


def _volumes(song: Song) -> dict[str, list[tuple[Fraction, int]]]:
    """Traccia "Style · …" → CC7 (posizione, valore)."""
    return {t.name: [(pos, e.value) for pos, e in song.placed_events(t) if e.kind == CC and e.number == 7]
            for t in song.tracks if t.kind == "style"}


def test_style_part_volumes_reach_the_generated_tracks():
    # demo.sty: Rhythm e Bass a 100, Piano a 85 (impostazione dello stile)
    volumes = _volumes(_generated())
    assert volumes["Style · Piano"] == [(Fraction(0), 85)]
    assert volumes["Style · Bass"] == [(Fraction(0), 100)]
    assert volumes["Style · Rhythm"] == [(Fraction(0), 100)]


def test_fill_with_the_same_values_adds_no_events():
    # nel fill i valori non cambiano: un solo CC7 all'inizio del segmento
    volumes = _volumes(_generated(fill="Fill In AA"))
    assert volumes["Style · Piano"] == [(Fraction(0), 85)]
