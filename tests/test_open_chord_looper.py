"""File > Open… di un banco del Chord Looper (.clb) come brano nuovo (2026-11-24)."""

import struct

from editor.core.chord_track import CHORDS
from editor.importer.chord_looper import is_chord_looper, song_from_clb


def _chord(cr: int, ct: int, bn: int, bt: int) -> bytes:
    """Meta-evento accordo Yamaha FF 7F 07 43 7B 01 cr ct bn bt."""
    return bytes([0xFF, 0x7F, 0x07, 0x43, 0x7B, 0x01, cr, ct, bn, bt])


def _clb(path) -> None:
    """Banco con una memoria: Dm, poi Bb dopo una misura (1920 tick per semiminima: 7680 tick = misura)."""
    # delta in VLQ: 0xBC 0x00 = 7680 tick, una misura in 4/4
    track = (b"\x00" + _chord(0x32, 8, 0x32, 8)  # Dm (D naturale = 0x32, tipo 8 = minore)
             + b"\xbc\x00" + _chord(0x27, 0, 0x27, 0)  # Bb (B bemolle = 0x27, tipo 0 = maggiore)
             + b"\xbc\x00\xff\x2f\x00")  # fine della memoria dopo un'altra misura
    memory = b"MThd" + struct.pack(">IHHH", 6, 0, 1, 1920) + b"MTrk" + struct.pack(">I", len(track)) + track
    path.write_bytes(b"CLD_001\r\n" + memory + b"\r\n")


def test_is_chord_looper():
    assert is_chord_looper("bank.clb") and is_chord_looper("BANK.CLB")
    assert not is_chord_looper("song.mid")


def test_song_from_clb_has_only_the_chord_track_and_the_key(tmp_path):
    path = tmp_path / "night.clb"
    _clb(path)
    song = song_from_clb(path)
    assert song.title == "night"
    assert [t.kind for t in song.tracks] == [CHORDS]
    names = [e.name for g in song.groups.values() for e in g.chord_events]
    assert names[0] == "Dm"
    assert str(song.keys.initial.tonic) == "D"  # tonalità ricavata dagli accordi


def test_session_opens_a_clb_as_an_imported_song(tmp_path):
    from editor.session import Session

    path = tmp_path / "night.clb"
    _clb(path)
    session = Session(backend="null")
    try:
        doc = session.open_path(path)
        assert doc.project_path is None  # importato: Save lo salva accanto, come .music
        assert doc.song.chord_track is not None
    finally:
        session.close()
