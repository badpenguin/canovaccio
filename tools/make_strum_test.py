"""Genera examples/strum-test.music: ritmo di chitarra alternato veloce su Mi minore.

Due misure 4/4 a 112 BPM di semicrome, pennata alternata giù/su (⊓ V ⊓ V …) con
l'accordo di Em del libretto (022000, note reali Mi2 Si2 Mi3 Sol3 Si3 Mi4). È un
progetto .music perché nel MIDI la pennata non è un segno (diventa solo ritardi).

    .venv/bin/python tools/make_strum_test.py
"""

from __future__ import annotations

import sys
from fractions import Fraction as F
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from editor.core.clef import BASS  # noqa: E402
from editor.core.guitar import guitar_chord  # noqa: E402
from editor.core.instrument import GMInstrument  # noqa: E402
from editor.core.key import Key  # noqa: E402
from editor.core.model import Group, Note, Segment, Song, Track  # noqa: E402
from editor.core.project import save_project  # noqa: E402
from editor.core.timeline import KeyTable, TempoTable  # noqa: E402

OUT = ROOT / "examples" / "strum-test.music"


def main() -> None:
    song = Song(title="Pennata alternata Em", tempos=TempoTable(112), keys=KeyTable(Key.parse("Em")))
    em = guitar_chord(4, "min").midi  # 022000
    step = F(1, 16)
    notes = []
    for i in range(32):  # due misure di semicrome
        strum = "down" if i % 2 == 0 else "up"
        accent = 96 if i % 4 == 0 else 78  # un po' più forte sul movimento
        notes += [Note(i * step, step, m, velocity=accent, strum=strum) for m in em]
    group = Group("Em alternato", 2, notes)
    song.groups[group.id] = group
    track = Track("1 Chitarra acustica", GMInstrument(25), BASS,
                  [Segment(group.id, 0, F(2), label="Em: pennata alternata")], channel=1)
    song.tracks.append(track)
    save_project(song, OUT)
    print(f"scritto {OUT}")


if __name__ == "__main__":
    main()
