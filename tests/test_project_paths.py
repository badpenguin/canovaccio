"""Percorsi degli stili nel .music (2026-11-24): relativi alla cartella del file se lo stile sta lì
o in una sottocartella, assoluti altrimenti; in memoria sempre assoluti."""

from fractions import Fraction

from editor.core.chord_track import CHORDS, ChordEvent
from editor.core.model import Group, Segment, Song, Track
from editor.core.project import load_project, save_project, song_to_dict


def _song(style: str) -> Song:
    """Brano con una traccia accordi che usa `style` (fra gli stili del brano e nel segmento)."""
    song = Song()
    group = Group("Verse", 1, [], chord_events=[ChordEvent(Fraction(0), "C", 0)])
    song.groups[group.id] = group
    seg = Segment(group.id, 0, Fraction(1), style=style, section="Main A")
    song.tracks.append(Track("Chords", segments=[seg], kind=CHORDS, styles=[style]))
    return song


def test_style_next_to_the_project_is_saved_relative(tmp_path):
    styles = tmp_path / "styles"
    styles.mkdir()
    style = styles / "ballad.sty"
    style.write_bytes(b"")
    song = _song(str(style))
    data = song_to_dict(song, tmp_path)
    assert data["tracks"][0]["styles"] == ["styles/ballad.sty"]
    assert data["tracks"][0]["segments"][0]["style"] == "styles/ballad.sty"
    # rilette: di nuovo assolute, anche spostando la cartella intera
    save_project(song, tmp_path / "song.music")
    moved = tmp_path.parent / (tmp_path.name + "-moved")
    tmp_path.rename(moved)
    back = load_project(moved / "song.music")
    expected = str((moved / "styles" / "ballad.sty").resolve())
    assert back.chord_track.styles == [expected]
    assert back.chord_track.segments[0].style == expected


def test_style_elsewhere_stays_absolute(tmp_path):
    project_dir = tmp_path / "songs"
    project_dir.mkdir()
    style = str((tmp_path / "library" / "ballad.sty").resolve())  # fuori dalla cartella del .music
    song = _song(style)
    assert song_to_dict(song, project_dir)["tracks"][0]["styles"] == [style]
    save_project(song, project_dir / "song.music")
    back = load_project(project_dir / "song.music")
    assert back.chord_track.styles == [style]
    assert back.chord_track.segments[0].style == style


def test_no_style_stays_empty(tmp_path):
    song = _song("")
    song.chord_track.styles = []
    save_project(song, tmp_path / "song.music")
    back = load_project(tmp_path / "song.music")
    assert back.chord_track.styles == []
    assert back.chord_track.segments[0].style == ""
