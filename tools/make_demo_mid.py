"""Genera examples/demo.mid: brano di prova in Si minore, 8 misure.

Contiene: melodia con La# e terzine, basso, batteria, un cambio di tempo,
e una traccia "suonata" con tempi imprecisi (per la notazione in rosso).

    .venv/bin/python tools/make_demo_mid.py
"""

from __future__ import annotations

import random
from pathlib import Path

import mido

TPQ = 480
OUT = Path(__file__).resolve().parent.parent / "examples" / "demo.mid"


def track(name: str, channel: int, program: int | None, notes, extra=(), label: str = "") -> mido.MidiTrack:
    """notes: (inizio in semiminime, durata in semiminime, altezza, velocity).
    label: etichetta del segmento (meta-evento "marker" all'inizio della traccia)."""
    events = [(0, mido.MetaMessage("track_name", name=name))]
    if label:
        events.append((0, mido.MetaMessage("marker", text=label)))
    if program is not None:
        events.append((0, mido.Message("program_change", channel=channel, program=program)))
    events += list(extra)
    for start, dur, pitch, vel in notes:
        on, off = round(start * TPQ), round((start + dur) * TPQ)
        events.append((on, mido.Message("note_on", channel=channel, note=pitch, velocity=vel)))
        events.append((off, mido.Message("note_off", channel=channel, note=pitch, velocity=0)))
    events.sort(key=lambda e: (e[0], e[1].type == "note_on"))
    out, last = mido.MidiTrack(), 0
    for tick, msg in events:
        out.append(msg.copy(time=tick - last))
        last = tick
    return out


def main() -> None:
    random.seed(7)
    conductor = mido.MidiTrack(
        [
            mido.MetaMessage("track_name", name="Conductor", time=0),
            mido.MetaMessage("set_tempo", tempo=mido.bpm2tempo(96), time=0),
            mido.MetaMessage("time_signature", numerator=4, denominator=4, time=0),
            mido.MetaMessage("key_signature", key="Bm", time=0),
            mido.MetaMessage("set_tempo", tempo=mido.bpm2tempo(110), time=4 * 4 * TPQ),
        ]
    )

    B3, Cs4, D4, E4, Fs4, G4, As4, B4, Cs5, D5 = 59, 61, 62, 64, 66, 67, 70, 71, 73, 74
    melody = [
        (0, 1, B4, 90), (1, 1, D5, 80), (2, 1.5, Cs5, 80), (3.5, 0.5, B4, 70),
        (4, 1, As4, 90), (5, 1, Fs4, 80), (6, 2, B4, 85),
        # terzine di croma
        (8, 1 / 3, B4, 80), (8 + 1 / 3, 1 / 3, Cs5, 75), (8 + 2 / 3, 1 / 3, D5, 75),
        (9, 1, E4 + 12, 85), (10, 0.5, D5, 80), (10.5, 0.5, Cs5, 75), (11, 1, B4, 80),
        (12, 0.75, As4, 85), (12.75, 0.25, B4, 70), (13, 1, Cs5, 80), (14, 2, Fs4, 80),
        (16, 4, B4, 85), (20, 2, G4, 80), (22, 2, Fs4, 80),
        (24, 3, As4, 85), (27, 1, Cs5, 80), (28, 4, B4, 90),
    ]
    bass = [(bar * 4, 4, root, 90) for bar, root in enumerate(
        [B3 - 12, B3 - 12, G4 - 24, Fs4 - 24, B3 - 12, E4 - 24, Fs4 - 24, B3 - 12])]
    chords = []
    for bar, triad in enumerate([(B3, D4, Fs4), (B3, D4, G4), (G4 - 12, B3, D4), (Fs4 - 12, As4 - 12, Cs4)] * 2):
        for beat in (0, 2):
            for p in triad:
                jitter = random.uniform(-0.06, 0.06)
                chords.append((bar * 4 + beat + max(jitter, -beat), 1.8 + random.uniform(-0.1, 0.1), p, 70))
    drums = []
    for bar in range(8):
        for beat in range(4):
            t = bar * 4 + beat
            drums.append((t, 0.25, 42, 70))  # charleston
            drums.append((t + 0.5, 0.25, 42, 55))
            if beat in (0, 2):
                drums.append((t, 0.25, 36, 100))  # cassa
            else:
                drums.append((t, 0.25, 38, 95))  # rullante

    mid = mido.MidiFile(type=1, ticks_per_beat=TPQ)
    mid.tracks += [
        conductor,
        track("Melodia", 0, 73, melody, label="Tema"),
        track("Accordi (suonati)", 1, 0, chords, label="Accordi"),
        track("Basso", 2, 32, bass, label="Giro di basso"),
        track("Batteria", 9, None, drums, label="Groove"),
    ]
    OUT.parent.mkdir(exist_ok=True)
    mid.save(OUT)
    print(OUT)


if __name__ == "__main__":
    main()
