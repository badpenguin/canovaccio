"""Genera examples/ballad-strum.music: ballata acustica lenta (ritmo originale, nello
spirito delle ballate folk/rock anni '70), 4/4 a 72 BPM, 8 misure tutte su Em (forma
del libretto 022000). Le misure si alternano (G = giù, S = su, - = la corda risuona):

    misura A:  G - - -  G S G -  - - S -  G S G S   (passaggi di 3 e 4 pennate)
    misura B:  G - - -  G - S -  - - S -  G - S -

Ogni colpo suona fino al successivo. È un progetto .music: nel MIDI la pennata non è
un segno.

    .venv/bin/python tools/make_ballad_strum.py
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

OUT = ROOT / "examples" / "ballad-strum.music"
E = F(1, 16)
# misure in semicrome (posizione, direzione, velocity); si alternano A e B
#              1 e & a  2 e & a  3 e & a  4 e & a
#   misura A:  G - - -  G S G -  - - S -  G S G S   (passaggi di 3 e 4 pennate)
#   misura B:  G - - -  G - S -  - - S -  G - S -   (schema di base)
BAR_A = [(0, "down", 90), (4, "down", 80), (5, "up", 60), (6, "down", 66), (10, "up", 62),
         (12, "down", 78), (13, "up", 58), (14, "down", 64), (15, "up", 58)]
BAR_B = [(0, "down", 90), (4, "down", 80), (6, "up", 62), (10, "up", 62), (12, "down", 78), (14, "up", 60)]
PROGRESSION = [(4, "min")] * 8  # tutto su Em (per altri accordi: (classe d'altezza, tipo))


def main() -> None:
    song = Song(title="Ballata acustica Em", tempos=TempoTable(72), keys=KeyTable(Key.parse("Em")))
    notes = []
    for bar, (pc, kind) in enumerate(PROGRESSION):
        chord = guitar_chord(pc, kind).midi
        pattern = BAR_A if bar % 2 == 0 else BAR_B
        for k, (pos, strum, vel) in enumerate(pattern):
            nxt = pattern[k + 1][0] if k + 1 < len(pattern) else 16
            start, length = bar + pos * E, (nxt - pos) * E
            notes += [Note(start, length, m, velocity=vel, strum=strum) for m in chord]
    group = Group("Ballata", len(PROGRESSION), notes)
    song.groups[group.id] = group
    song.tracks.append(Track("1 Chitarra acustica", GMInstrument(24), BASS,
                             [Segment(group.id, 0, F(len(PROGRESSION)), label="Em: giù, giù-su, su-giù-su")], channel=1))
    save_project(song, OUT)
    print(f"scritto {OUT}")


if __name__ == "__main__":
    main()
