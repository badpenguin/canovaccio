"""Genera examples/slur-test.music: prova d'ascolto dello slur su vari strumenti.

Una traccia per strumento, un segmento di una misura ciascuno, uno dopo l'altro.
In ogni misura (4/4, 90 BPM): 4 crome sotto slur (movimenti 1–2), poi le stesse
4 crome senza slur (movimenti 3–4), per confrontare. È un progetto .music perché
nel MIDI lo slur non esiste.

    .venv/bin/python tools/make_slur_test.py
"""

from __future__ import annotations

import sys
from fractions import Fraction as F
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from editor.core.clef import BASS, TREBLE  # noqa: E402
from editor.core.instrument import GMInstrument  # noqa: E402
from editor.core.model import Group, Note, Segment, Song, Track  # noqa: E402
from editor.core.project import save_project  # noqa: E402
from editor.core.timeline import TempoTable  # noqa: E402

OUT = ROOT / "examples" / "slur-test.music"
INSTRUMENTS = [  # (nome, programma GM 0-based, ottava della frase, chiave)
    ("Pianoforte", 0, 0, TREBLE), ("Flauto", 73, 12, TREBLE), ("Violino", 40, 0, TREBLE),
    ("Clarinetto", 71, 0, TREBLE), ("Tromba", 56, 0, TREBLE), ("Sax alto", 65, 0, TREBLE),
    ("Organo", 19, 0, TREBLE), ("Archi", 48, 0, TREBLE), ("Coro", 52, 0, TREBLE),
    ("Violoncello", 42, -12, BASS), ("Chitarra", 24, -12, TREBLE), ("Synth lead", 80, 0, TREBLE),
]
PHRASE = [60, 62, 64, 65]  # Do Re Mi Fa


def main() -> None:
    song = Song(title="Prova slur", tempos=TempoTable(90))
    e = F(1, 8)
    for bar, (name, program, octave, clef) in enumerate(INSTRUMENTS):
        notes = [Note(i * e, e, p + octave, velocity=85) for i, p in enumerate(PHRASE)]  # sotto slur
        notes += [Note(F(1, 2) + i * e, e, p + octave, velocity=85) for i, p in enumerate(PHRASE)]  # normali
        group = Group(name, 1, notes, slurs=[(notes[0].id, notes[3].id)])
        song.groups[group.id] = group
        track = Track(f"{bar + 1} {name}", GMInstrument(program), clef,
                      [Segment(group.id, bar, F(1), label=f"{name}: slur | normale")], channel=bar + 1 if bar < 9 else bar + 2)
        song.tracks.append(track)
    save_project(song, OUT)
    print(f"scritto {OUT}")


if __name__ == "__main__":
    main()
