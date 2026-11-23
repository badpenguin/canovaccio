"""Genera examples/demo.sty: piccolo stile in formato Yamaha SFF1, scritto da zero.

2026-11-23: serve uno stile pubblicabile con il progetto. Gli stili degli utenti trovati in rete
quasi sempre derivano da quelli di fabbrica Yamaha (lo dice il meta-evento Copyright rimasto nel
file) e non hanno una licenza che permetta di ridistribuirli: questo è nostro, sotto GPL come il resto.

Contiene: un 8beat pop a 100 BPM scritto su CMaj7, sezioni Main A, Main B, Fill In AA, Fill In BB;
parti Rhythm (canale 10, kit Yamaha 127-0-1), Bass (canale 11) e Chord1 "Piano" (canale 12).
Nessun meta-evento Copyright.

Struttura del file (come gli stili veri):
- un MIDI di tipo 0 con i marker "SFF1" e "SInt" a tick 0; la prima misura ("SInt") ha solo
  Bank Select, Program Change e volumi, poi le sezioni una dopo l'altra, ognuna col suo marker;
- dopo il MIDI il blocco CASM con un CSEG: l'elenco delle sezioni (Sdec) e una tabella Ctab per parte.

    .venv/bin/python tools/make_demo_sty.py
"""

from __future__ import annotations

import struct
from pathlib import Path

import mido

TPQ = 480
BAR = 4 * TPQ
OUT = Path(__file__).resolve().parent.parent / "examples" / "demo.sty"

# canali MIDI 0–15 (nel file) delle parti
RHYTHM, BASS, CHORD1 = 9, 10, 11

# batteria GM: cassa, rullante, charleston chiuso e aperto, crash, tom
KICK, SNARE, HH, OHH, CRASH = 36, 38, 42, 46, 49
TOM_HI, TOM_MID, TOM_LOW, TOM_FLOOR = 50, 48, 45, 43

# note di CMaj7: basso in ottava 2, accordo nella posizione E4 G4 B4 C5
C2, E2, G2, B2, C3 = 36, 40, 43, 47, 48
VOICING = (64, 67, 71, 72)


def _groove(bars: int, open_hat: bool) -> list[tuple]:
    """Batteria 8beat: cassa su 1 e 3 (più il "e" del 3 con open_hat), rullante su 2 e 4,
    charleston in crome (aperto sul levare del 4 con open_hat). Note (inizio, durata, altezza, vel)
    in semiminime dall'inizio della sezione."""
    out = []
    for bar in range(bars):
        t0 = bar * 4
        for eighth in range(8):  # charleston in crome, accento sui battiti
            t = t0 + eighth / 2
            pitch = OHH if open_hat and eighth == 7 else HH
            out.append((t, 0.25, pitch, 80 if eighth % 2 == 0 else 60))
        out += [(t0, 0.25, KICK, 100), (t0 + 2, 0.25, KICK, 95), (t0 + 1, 0.25, SNARE, 95), (t0 + 3, 0.25, SNARE, 95)]
        if open_hat:
            out.append((t0 + 2.5, 0.25, KICK, 80))
    return out


def _fill(toms: bool) -> list[tuple]:
    """Fill di una misura: groove sui primi due battiti, poi semicrome su tom (toms) o rullante."""
    out = [(0, 0.25, KICK, 100), (0, 0.25, CRASH, 90), (1, 0.25, SNARE, 95), (0.5, 0.25, HH, 60), (1.5, 0.25, HH, 60)]
    pitches = (TOM_HI, TOM_HI, TOM_MID, TOM_MID, TOM_LOW, TOM_LOW, TOM_FLOOR, TOM_FLOOR) if toms else (SNARE,) * 8
    for i, pitch in enumerate(pitches):  # 8 semicrome sui battiti 3 e 4, in crescendo
        out.append((2 + i / 4, 0.25, pitch, 75 + 3 * i))
    return out


# sezioni: nome → (misure, batteria, basso, accordo); note in semiminime dall'inizio della sezione
SECTIONS = {
    "Main A": (2, _groove(2, open_hat=False),
               [(b * 4 + t, d, p, 95) for b in range(2) for t, d, p in ((0, 1.5, C2), (1.5, 0.5, C2), (2, 1, G2), (3, 1, E2))],
               [(b * 4 + t, 1.9, p, 70) for b in range(2) for t in (0, 2) for p in VOICING]),
    "Main B": (2, _groove(2, open_hat=True),
               [(b * 4 + e / 2, 0.45, C3 if e in (2, 6) else C2, 95 if e % 2 == 0 else 80) for b in range(2) for e in range(8)],
               [(b * 4 + t, d, p, 75) for b in range(2) for t, d in ((0, 0.9), (1.5, 0.4), (2, 0.9), (3.5, 0.4)) for p in VOICING]),
    "Fill In AA": (1, _fill(toms=True),
                   [(0, 2, C2, 95), (2, 1, G2, 90), (3, 0.5, E2, 85), (3.5, 0.5, B2, 85)],
                   [(0, 1.9, p, 70) for p in VOICING]),
    "Fill In BB": (1, _fill(toms=False),
                   [(e / 2, 0.45, C2, 90) for e in range(4)] + [(2, 1, G2, 90), (3, 1, B2, 90)],
                   [(0, 0.9, p, 75) for p in VOICING] + [(1.5, 0.4, p, 75) for p in VOICING]),
}


def _midi() -> mido.MidiFile:
    """Il MIDI di tipo 0: misura d'impostazione e sezioni attaccate."""
    events = [  # (tick, messaggio); la misura "SInt" fa solo da impostazione
        (0, mido.MetaMessage("track_name", name="Demo 8Beat")),
        (0, mido.MetaMessage("marker", text="SFF1")),
        (0, mido.MetaMessage("marker", text="SInt")),
        (0, mido.MetaMessage("set_tempo", tempo=mido.bpm2tempo(100))),
        (0, mido.MetaMessage("time_signature", numerator=4, denominator=4)),
    ]
    for ch, msb, program, volume in ((RHYTHM, 127, 0, 100), (BASS, 0, 33, 100), (CHORD1, 0, 0, 85)):
        events += [(0, mido.Message("control_change", channel=ch, control=0, value=msb)),
                   (0, mido.Message("control_change", channel=ch, control=32, value=0)),
                   (0, mido.Message("program_change", channel=ch, program=program)),
                   (0, mido.Message("control_change", channel=ch, control=7, value=volume))]
    start = BAR  # le sezioni partono dopo la misura d'impostazione
    for name, (bars, *parts) in SECTIONS.items():
        events.append((start, mido.MetaMessage("marker", text=name)))
        # ogni parte con il suo canale: note (inizio, durata, altezza, vel) → note_on/note_off
        for ch, notes in zip((RHYTHM, BASS, CHORD1), parts):
            for t, dur, pitch, vel in notes:
                on = start + round(t * TPQ)
                events += [(on, mido.Message("note_on", channel=ch, note=pitch, velocity=vel)),
                           (on + round(dur * TPQ), mido.Message("note_off", channel=ch, note=pitch, velocity=0))]
        start += bars * BAR
    events.append((start, mido.MetaMessage("end_of_track")))
    # allo stesso tick: prima i meta (marker), poi note_off, poi il resto (note_on dopo le chiusure)
    order = {"note_off": 1, "note_on": 2}
    events.sort(key=lambda e: (e[0], 0 if e[1].is_meta and e[1].type != "end_of_track" else order.get(e[1].type, 1)))
    track, now = mido.MidiTrack(), 0
    for tick, msg in events:
        track.append(msg.copy(time=tick - now))
        now = tick
    mid = mido.MidiFile(type=0, ticks_per_beat=TPQ)
    mid.tracks.append(track)
    return mid


def _chunk(tag: bytes, body: bytes) -> bytes:
    """Blocco "TAG + lunghezza a 4 byte big-endian + dati"."""
    return tag + struct.pack(">I", len(body)) + body


def _ctab(channel: int, name: str, ntr: int, ntt: int, high_key: int, low: int = 0, high: int = 127) -> bytes:
    """Tabella Ctab (SFF1, 27 byte) di una parte: canale d'origine e di destinazione uguali,
    suona con tutte le fondamentali e tutti i tipi d'accordo, scritta su CMaj7, RTR Pitch Shift."""
    body = bytes([channel]) + name.ljust(8)[:8].encode("latin-1") + bytes([
        channel,                      # canale di destinazione
        1,                            # editabile
        0x0F, 0xFF,                   # note mute: tutte le 12 fondamentali
        0x07, 0xFF, 0xFF, 0xFF, 0xFF,  # chord mute: tutti i tipi d'accordo
        0, 2,                         # accordo d'origine: C, Maj7
        ntr, ntt, high_key, low, high,
        1,                            # RTR: Pitch Shift
        0,                            # nessuna funzione speciale
    ])
    return _chunk(b"Ctab", body)


def _casm() -> bytes:
    """Blocco CASM: un CSEG valido per tutte le sezioni."""
    sdec = _chunk(b"Sdec", ",".join(SECTIONS).encode("latin-1"))
    tables = (_ctab(RHYTHM, "Rhythm", ntr=1, ntt=0, high_key=0)    # Root Fixed · Bypass
              + _ctab(BASS, "Bass", ntr=0, ntt=3, high_key=5)      # Root Trans · Bass, fino a F
              + _ctab(CHORD1, "Piano", ntr=1, ntt=2, high_key=0))  # Root Fixed · Chord
    return _chunk(b"CASM", _chunk(b"CSEG", sdec + tables))


def main() -> None:
    OUT.parent.mkdir(exist_ok=True)
    _midi().save(OUT)
    OUT.write_bytes(OUT.read_bytes() + _casm())  # il CASM va subito dopo il MIDI
    print(OUT)


if __name__ == "__main__":
    main()
