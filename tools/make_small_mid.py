"""Genera examples/small.mid: brano piccolo per provare Quantize, Do maggiore, 4/4, 100 BPM.

Tre tracce, un esempio per traccia, uno dopo l'altro (etichetta del segmento):
- Melodia (misure 1–4): crome con inizi in anticipo/ritardo e durate imprecise
  → Notes > Quantize to Snap con Snap 1/8 (o Quantize… "Inizio e durata", 1/8).
- Arpeggio (misure 5–8): terzine di croma imprecise
  → Notes > Quantize… con Griglia "1/8 terzina".
- Basso (misura 9): crome in ottave di Si, scritte piene
  → Notes > Articulations > Staccato (il MIDI non ha lo staccato: si mette nell'app).
- Terzine (misura 10): prova di disegno delle terzine, esatte: tre semiminime di
  terzina (movimenti 1–2, parentesi) e due gruppi di crome di terzina (3 e 4, travatura).

    .venv/bin/python tools/make_small_mid.py
"""

from __future__ import annotations

import random
from pathlib import Path

import mido

TPQ = 480
OUT = Path(__file__).resolve().parent.parent / "examples" / "small.mid"


def track(name: str, channel: int, program: int, label: str, notes, at: float = 0) -> mido.MidiTrack:
    """notes: (inizio in semiminime, durata in semiminime, altezza, velocity).
    label: etichetta del segmento (marker all'inizio dell'esempio, `at` in semiminime)."""
    events = [(0, mido.MetaMessage("track_name", name=name)),
              (round(at * TPQ), mido.MetaMessage("marker", text=label)),
              (0, mido.Message("program_change", channel=channel, program=program))]
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
    random.seed(3)
    conductor = mido.MidiTrack([
        mido.MetaMessage("track_name", name="Conductor", time=0),
        mido.MetaMessage("set_tempo", tempo=mido.bpm2tempo(100), time=0),
        mido.MetaMessage("time_signature", numerator=4, denominator=4, time=0),
        mido.MetaMessage("key_signature", key="C", time=0),
    ])

    def sloppy(t: float, d: float, first: float = 0) -> tuple[float, float]:
        """Inizio spostato fino a ±0.12 di semiminima (mai prima di `first`), durata ±20%."""
        start = max(t + random.uniform(-0.12, 0.12), first)
        return start, d * random.uniform(0.8, 1.2)

    # esempio 1: crome (scala di Do su e giù, poi una frase) suonate fuori griglia
    C4, D4, E4, F4, G4, A4, B4, C5 = 60, 62, 64, 65, 67, 69, 71, 72
    line = [C4, D4, E4, F4, G4, A4, B4, C5, D4 + 12, B4, A4, G4, F4, E4, D4, C4,
            E4, G4, C5, G4, E4, G4, C5, E4 + 12, D4 + 12, B4, G4, D4, C4, E4, G4, C5]
    melody = [(*sloppy(i * 0.5, 0.45), p, random.randint(70, 95)) for i, p in enumerate(line)]

    # esempio 2: terzine di croma (arpeggi C, Am, F, G) suonate fuori griglia
    chords = [(48, 52, 55), (45, 48, 52), (41, 45, 48), (43, 47, 50)]
    arpeggio = []
    for bar, triad in enumerate(chords):
        for beat in range(4):
            for k, p in enumerate(triad):
                t = 16 + bar * 4 + beat + k / 3  # dopo la melodia: misure 5–8
                arpeggio.append((*sloppy(t, 0.3, 16), p, random.randint(60, 80)))

    # esempio 3: linea di basso in ottave di Si (crome, durata piena), misura 9
    B1, B2 = 35, 47
    bass = [(32 + i * 0.5, 0.5, B1 if i % 2 == 0 else B2, 90) for i in range(8)]

    # esempio 4: terzine esatte, misura 10
    triplets = [(36 + i * 2 / 3, 2 / 3, p, 80) for i, p in enumerate([72, 76, 79])]  # semiminime di terzina
    triplets += [(38 + i / 3, 1 / 3, p, 80) for i, p in enumerate([72, 74, 76, 77, 79, 81])]  # crome di terzina

    mid = mido.MidiFile(type=1, ticks_per_beat=TPQ)
    mid.tracks += [
        conductor,
        track("Melodia", 0, 0, "Quantize 1/8", melody),
        track("Arpeggio", 1, 4, "Quantize terzine 1/8", arpeggio, at=16),
        track("Basso", 2, 33, "Staccato", bass, at=32),
        track("Terzine", 3, 0, "Terzine", triplets, at=36),
    ]
    OUT.parent.mkdir(exist_ok=True)
    mid.save(OUT)
    print(f"scritto {OUT}")


if __name__ == "__main__":
    main()
