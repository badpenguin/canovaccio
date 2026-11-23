"""Genera examples/long.mid: brano lungo di prova (circa 200 misure, 16 tracce, batteria inclusa,
pause di qualche misura dentro le tracce). Le prime 3 tracce iniziano con un
segmento sulle stesse misure (1–8), seguito da misure vuote: serve a provare la
notazione a più tracce.

Serve a provare scorrimento, zoom e prestazioni con file grandi.

    .venv/bin/python tools/make_long_mid.py
"""

from __future__ import annotations

import random
from pathlib import Path

import mido

TPQ = 480
INTRO_BARS = 8  # misure del segmento comune iniziale
INTRO_TRACKS = {0: (67, 79), 1: (55, 67), 2: (36, 48)}  # traccia → altezze (acuta, media, grave)
OUT = Path(__file__).resolve().parent.parent / "examples" / "long.mid"


def main() -> None:
    random.seed(1)
    intro_rng = random.Random(2)  # separato: il resto del brano resta uguale
    mid = mido.MidiFile(type=1, ticks_per_beat=TPQ)
    mid.tracks.append(mido.MidiTrack([mido.MetaMessage("set_tempo", tempo=mido.bpm2tempo(110), time=0)]))
    for t in range(16):
        track = mido.MidiTrack()
        channel = 9 if t == 15 else t if t < 9 else t + 1  # l'ultima traccia è la batteria
        track.append(mido.Message("program_change", channel=channel, program=(t * 7) % 128, time=0))
        start_bar, end_bar = random.randint(0, 60), random.randint(120, 200)
        intro = []
        if t in INTRO_TRACKS:
            # segmento comune: semiminime esatte nelle misure 1–8, poi almeno 2 misure vuote
            lo, hi = INTRO_TRACKS[t]
            intro.append((0, mido.MetaMessage("marker", text="Intro")))  # etichetta del segmento
            for q in range(INTRO_BARS * 4):
                note = intro_rng.randint(lo, hi)
                intro.append((q * TPQ, mido.Message("note_on", channel=channel, note=note, velocity=80)))
                intro.append(((q + 1) * TPQ, mido.Message("note_off", channel=channel, note=note)))
            start_bar = max(start_bar, INTRO_BARS + 2)
        # alcune pause di qualche misura: all'import dividono la traccia in più segmenti
        rests = sorted(random.sample(range(start_bar + 8, end_bar - 8), k=random.randint(1, 3)))
        events, tick = intro, start_bar * 4 * TPQ
        part = 1
        events.append((tick, mido.MetaMessage("marker", text="Parte A")))
        while tick < end_bar * 4 * TPQ:
            if rests and tick >= rests[0] * 4 * TPQ:
                tick = (rests.pop(0) + random.randint(2, 8)) * 4 * TPQ
                part += 1
                events.append((tick, mido.MetaMessage("marker", text=f"Parte {chr(64 + part)}")))
                continue
            dur = random.choice([TPQ // 2, TPQ, TPQ * 2])
            note = random.randint(40, 80)
            events.append((tick, mido.Message("note_on", channel=channel, note=note, velocity=80)))
            events.append((tick + dur - 10, mido.Message("note_off", channel=channel, note=note)))
            tick += dur
        events.sort(key=lambda e: (e[0], e[1].type == "note_on"))  # a parità: prima i note_off
        last = 0
        for tk, msg in events:
            track.append(msg.copy(time=tk - last))
            last = tk
        mid.tracks.append(track)
    OUT.parent.mkdir(exist_ok=True)
    mid.save(OUT)
    print(OUT)


if __name__ == "__main__":
    main()
