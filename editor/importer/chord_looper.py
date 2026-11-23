"""Import di un banco del Chord Looper Yamaha (.clb) nella traccia accordi del brano.

Il file: "CLD_001\\r\\n" e poi fino a 8 memorie, ognuna un piccolo MIDI (MThd + MTrk)
seguito da "\\r\\n". Gli accordi sono meta-eventi Yamaha FF 7F 07 43 7B 01 cr ct bn bt
(come i messaggi Chord Control del Data List PSR-SX900):
- cr: fondamentale, 0fffnnnn: fff alterazione (0 bbb, 1 bb, 2 b, 3 naturale, 4 #, 5 ##,
  6 ###), nnnn nota (1 C … 7 B);
- ct: tipo d'accordo 0–34 (34 = nessun accordo);
- bn, bt: basso; bt = 30 ("1+8": nota singola) → accordo con barra; bn = 127 o uguale
  all'accordo → nessun basso.
Le posizioni restano quelle registrate (non agganciate al movimento): si correggono a
mano nel Chord Editor. Ogni memoria piena diventa un segmento, uno dopo l'altro; la
lunghezza è la fine della memoria arrotondata alla misura.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass, field
from fractions import Fraction
from pathlib import Path

from ..core.chord_track import CHORDS, ChordEvent
from ..core.model import Group, Segment, Song, Track

LETTERS = {1: "C", 2: "D", 3: "E", 4: "F", 5: "G", 6: "A", 7: "B"}
ACCIDENTALS = {0: "bbb", 1: "bb", 2: "b", 3: "", 4: "#", 5: "##", 6: "###"}
CHORD_META = bytes([0x43, 0x7B, 0x01])
ONE_PLUS_EIGHT = 30  # tipo del basso "1+8": una nota sola → accordo con barra


@dataclass
class Memory:
    index: int  # 1–8
    tpq: int
    events: list[tuple[int, ChordEvent]] = field(default_factory=list)  # (tick, accordo con pos 0)
    end_tick: int = 0


def note_name(code: int) -> str:
    return LETTERS.get(code & 0x0F, "?") + ACCIDENTALS.get(code >> 4, "")


def decode_chord(cr: int, ct: int, bn: int, bt: int) -> ChordEvent:
    bass = ""
    if bn != 127 and (bn, bt) != (cr, ct):
        bass = note_name(bn)  # bt = 30 (nota singola) o un accordo sotto: si mostra il basso
    return ChordEvent(Fraction(0), note_name(cr), ct, bass)


def _vlq(data: bytes, i: int) -> tuple[int, int]:
    value = 0
    while True:
        b = data[i]
        i += 1
        value = (value << 7) | (b & 0x7F)
        if not b & 0x80:
            return value, i


def _parse_track(data: bytes, memory: Memory) -> None:
    i, tick, status = 0, 0, 0
    while i < len(data):
        delta, i = _vlq(data, i)
        tick += delta
        if data[i] == 0xFF:  # meta-evento
            kind = data[i + 1]
            length, j = _vlq(data, i + 2)
            payload = data[j:j + length]
            i = j + length
            if kind == 0x7F and payload[:3] == CHORD_META and len(payload) >= 7:
                memory.events.append((tick, decode_chord(*payload[3:7])))
            elif kind == 0x2F:
                break
        elif data[i] in (0xF0, 0xF7):  # sysex
            length, j = _vlq(data, i + 1)
            i = j + length
        else:  # eventi di canale (non previsti, si saltano)
            if data[i] & 0x80:
                status = data[i]
                i += 1
            i += 1 if status & 0xF0 in (0xC0, 0xD0) else 2
    memory.end_tick = tick


def read_clb(path: str | Path) -> list[Memory]:
    """Memorie del banco (anche vuote), in ordine."""
    data = Path(path).read_bytes()
    if not data.startswith(b"CLD_"):
        raise ValueError("not a Chord Looper file (.clb)")
    memories = []
    p = data.find(b"MThd")
    while p >= 0 and len(memories) < 8:
        tpq = struct.unpack(">H", data[p + 12:p + 14])[0]
        memory = Memory(len(memories) + 1, tpq)
        t = data.find(b"MTrk", p)
        if t < 0:
            break
        length = struct.unpack(">I", data[t + 4:t + 8])[0]
        _parse_track(data[t + 8:t + 8 + length], memory)
        memories.append(memory)
        p = data.find(b"MThd", t + 8 + length)
    return memories


def chord_track_from_clb(song: Song, path: str | Path) -> tuple[Track, list[Group]]:
    """Traccia accordi nuova (non ancora nel brano) con i suoi gruppi: un segmento per
    memoria piena, uno dopo l'altro dalla prima misura."""
    segments, groups = [], []
    bar = 0
    for memory in read_clb(path):
        if not memory.events:
            continue
        tpw = memory.tpq * 4
        start = song.meters.bar_start(bar)
        end = start + Fraction(max(memory.end_tick, memory.events[-1][0] + 1), tpw)
        last = max(song.meters.bar_ceil(end), bar + 1)
        name = f"Memory {memory.index}"
        group = Group(name, last - bar, [], chord_events=[
            ChordEvent(Fraction(tick, tpw), e.root, e.kind, e.bass) for tick, e in memory.events])
        groups.append(group)
        segments.append(Segment(group.id, bar, song.meters.bar_start(last) - start, label=name))
        bar = last
    if not segments:
        raise ValueError("the bank contains no chords")
    used = {t.channel for t in song.tracks}  # Preview su un canale libero (non il 10, non 9–16 degli stili)
    channel = next((c for c in range(1, 9) if c not in used), 8)
    # 2026-11-20: nome in inglese (prima "Accordi"); la traccia accordi si ritrova per kind, non per nome
    return Track("Chords", segments=segments, kind=CHORDS, channel=channel), groups


def guess_key(song: Song, track: Track, groups: list[Group]):
    """Tonalità ricavata dagli accordi della traccia accordi appena letta (core.chord_track.detect_key)."""
    from ..core.chord_track import detect_key

    by_id = {g.id: g for g in groups}
    changes = []
    for seg in track.segments:
        start, end = song.segment_span(seg)
        events = sorted(by_id[seg.group_id].chord_events, key=lambda e: e.pos)
        for k, e in enumerate(events):
            changes.append((start + e.pos, start + events[k + 1].pos if k + 1 < len(events) else end, e))
    return detect_key(changes)
