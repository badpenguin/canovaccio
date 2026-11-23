"""Genera examples/drum-test.music: traccia di batteria di prova (rombi in griglia).

4 misure 4/4 a 120 BPM, ritmo rock semplice: charleston chiuso in crome, cassa sull'1 e
sul 3 (più il 3&), rullante sul 2 e sul 4, crash all'inizio della 1ª e della 3ª misura,
charleston aperto sull'ultimo "&" di ogni misura (si chiude sul colpo dopo), piccolo
fill di tom nella 4ª; nella 5ª
misura un solo colpo: cassa + charleston aperto, lasciato risuonare (prova del charleston).
Ogni colpo ha durata scritta 1/4 (come Create in griglia); al play i colpi risuonano
(i piatti non vengono troncati).

    .venv/bin/python tools/make_drum_test.py
"""

from __future__ import annotations

import sys
from fractions import Fraction as F
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from editor.core.instrument import GMInstrument  # noqa: E402
from editor.core.model import Group, Note, Segment, Song, Track  # noqa: E402
from editor.core.project import save_project  # noqa: E402
from editor.core.timeline import TempoTable  # noqa: E402

OUT = ROOT / "examples" / "drum-test.music"
KICK, SNARE, HAT, OPEN_HAT, CRASH, TOM_HI, TOM_MID, TOM_LOW = 36, 38, 42, 46, 49, 50, 47, 43
E, HIT = F(1, 8), F(1, 4)


def main() -> None:
    song = Song(title="Prova batteria", tempos=TempoTable(120))
    hits = []
    for bar in range(4):
        b = F(bar)
        for i in range(8):  # charleston in crome, aperto sull'ultimo & di ogni misura
            sound = OPEN_HAT if i == 7 else HAT
            hits.append((b + i * E, sound, 70 if i % 2 else 85))
        hits += [(b, KICK, 100), (b + 4 * E, KICK, 95), (b + 5 * E, KICK, 80),
                 (b + 2 * E, SNARE, 100), (b + 6 * E, SNARE, 100)]
        if bar in (0, 2):
            hits.append((b, CRASH, 105))
    fill = F(3) + 6 * E  # fill di tom sull'ultimo movimento: sostituisce il charleston
    hits = [h for h in hits if not (h[0] >= fill and h[1] in (HAT, OPEN_HAT, SNARE))]
    hits += [(fill, TOM_HI, 95), (fill + E / 2, TOM_MID, 95), (fill + E, TOM_LOW, 100), (fill + 3 * E / 2, TOM_LOW, 105)]
    hits += [(F(4), KICK, 105), (F(4), OPEN_HAT, 100)]  # finale: charleston aperto che risuona
    notes = [Note(start, HIT, sound, velocity=vel) for start, sound, vel in sorted(hits)]
    group = Group("Rock", 5, notes)
    song.groups[group.id] = group
    song.tracks.append(Track("10 Batteria", GMInstrument(0, percussion=True), segments=[
        Segment(group.id, 0, F(5), label="Rock: hi-hat, cassa, rullante, crash, fill, charleston aperto")],
        channel=10))
    save_project(song, OUT)
    print(f"scritto {OUT}")


if __name__ == "__main__":
    main()
