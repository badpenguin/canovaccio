"""File di progetto `.music`: tutto il modello in JSON compresso con gzip.

Posizioni e durate come frazioni esatte ("3/8"), così nulla si arrotonda.
In testa formato e versione, per poterlo far evolvere: i campi che mancano
prendono il valore di default (file di versioni precedenti).
"""

from __future__ import annotations

import gzip
import json
from fractions import Fraction
from pathlib import Path

from .clef import CLEFS
from .instrument import GMInstrument
from .key import MODES, Key
from .model import ControlEvent, Group, Note, Segment, Song, Sound, Track
from .chord_track import ChordEvent
from .style import StyleRule
from .pitch import Spelling
from .timeline import KeyTable, MeterTable, TempoTable, TimeSignature

VERSION = 1
EXTENSION = ".music"
STYLE_EXTENSION = ".stile"  # uno stile Yamaha importato e personalizzato: stesso formato del .music


def _f(x: Fraction | int) -> str:
    return str(Fraction(x))


def _key(k: Key) -> dict:
    return {"tonic": str(k.tonic), "mode": k.mode.name}


def _unkey(d: dict) -> Key:
    return Key(Spelling.parse(d["tonic"]), MODES[d["mode"]])


def song_to_dict(song: Song) -> dict:
    return {
        "version": VERSION,
        "title": song.title,
        "notes": song.notes,
        "copyright": song.copyright,
        "tempos": [[_f(e.at), e.value] for e in song.tempos],
        "meters": [[int(e.at), e.value.numerator, e.value.denominator] for e in song.meters],
        "keys": [[int(e.at), _key(e.value)] for e in song.keys],
        "groups": [
            {"id": g.id, "name": g.name, "length_bars": g.length_bars, "notes": [
                {"id": n.id, "start": _f(n.start), "duration": _f(n.duration), "midi": n.midi,
                 "velocity": n.velocity, "voice": n.voice,
                 "spelling": str(n.spelling) if n.spelling is not None else None,
                 "tie_next": n.tie_next, "staccato": n.staccato, "strum": n.strum}
                for n in g.notes],
             "slurs": [list(s) for s in g.slurs],
             "chords": [[_f(p), c] for p, c in g.chords],
             "style": g.style.to_dict() if g.style is not None else None,
             "chord_events": [[_f(e.pos), e.root, e.kind, e.bass] for e in g.chord_events],
             "events": [[_f(e.start), e.kind, e.number, e.value] for e in g.events]}
            for g in song.groups.values()
        ],
        "tracks": [
            {"id": t.id, "name": t.name, "program": getattr(t.instrument, "program", 0),
             "percussion": t.instrument.is_percussion, "clef": t.clef.name, "output": t.output,
             "kind": t.kind, "styles": list(t.styles), "channel": t.channel, "velocity": t.velocity, "mute": t.mute, "solo": t.solo,
             "sounds": {out: {"program": s.program, "bank": s.bank, "lsb": s.lsb, "soundfont": s.soundfont}
                        for out, s in t.sounds.items()},
             "segments": [
                 {"id": s.id, "group": s.group_id, "start_bar": s.start_bar, "offset": _f(s.offset),
                  "length": _f(s.length), "trim": _f(s.trim), "transpose": s.transpose,
                  "label": s.label, "clone_of": s.clone_of,
                  "style": s.style, "section": s.section, "fill": s.fill}
                 for s in t.segments]}
            for t in song.tracks
        ],
    }


def song_from_dict(d: dict) -> Song:
    # 2026-11-23: il campo "format" (firma "todo-musician") è stato tolto, scelta dell'utente: legarlo al
    # nome del programma rendeva illeggibili i file salvati se il nome cambia. Non si scrive più e, nei
    # file vecchi che lo hanno ancora, si ignora.
    if d.get("version", 0) > VERSION:
        raise ValueError(f"file from a newer version ({d['version']}): update the program")
    F = Fraction
    tempos = d["tempos"]
    meters = d["meters"]
    keys = d["keys"]
    song = Song(
        title=d.get("title", "Untitled"),
        notes=d.get("notes", ""),
        copyright=d.get("copyright", ""),  # assente nei file salvati prima del 2026-11-23
        tempos=TempoTable(tempos[0][1], [(F(at), bpm) for at, bpm in tempos[1:]]),
        meters=MeterTable(TimeSignature(meters[0][1], meters[0][2]),
                          [(at, TimeSignature(n, den)) for at, n, den in meters[1:]]),
        keys=KeyTable(_unkey(keys[0][1]), [(at, _unkey(k)) for at, k in keys[1:]]),
    )
    for g in d["groups"]:
        notes = [Note(F(n["start"]), F(n["duration"]), n["midi"], n.get("velocity", 80), n.get("voice", 1),
                      Spelling.parse(n["spelling"]) if n.get("spelling") else None,
                      n.get("tie_next", False), n.get("staccato", False), n.get("strum"), id=n["id"])
                 for n in g["notes"]]
        song.groups[g["id"]] = Group(g["name"], g["length_bars"], notes, id=g["id"],
                                     slurs=[tuple(s) for s in g.get("slurs", [])],
                                     chords=[(F(p), c) for p, c in g.get("chords", [])],
                                     style=StyleRule.from_dict(g["style"]) if g.get("style") else None,
                                     chord_events=[ChordEvent(F(p), r, k, b) for p, r, k, b in g.get("chord_events", [])],
                                     events=[ControlEvent(F(e[0]), e[1], e[2], e[3]) for e in g.get("events", [])])
    for t in d["tracks"]:
        segments = [Segment(s["group"], s["start_bar"], F(s["length"]), s.get("transpose", 0), F(s.get("offset", "0")),
                            F(s.get("trim", "0")), s.get("label", ""), s.get("clone_of"), id=s["id"],
                            style=s.get("style", ""), section=s.get("section", ""), fill=s.get("fill", ""))
                    for s in t["segments"]]
        song.tracks.append(Track(
            t["name"], GMInstrument(t.get("program", 0), t.get("percussion", False)),
            CLEFS.get(t.get("clef", "treble"), CLEFS["treble"]), segments, t.get("output", "default"),
            id=t["id"], channel=t.get("channel", 1), velocity=t.get("velocity", 100),
            mute=t.get("mute", False), solo=t.get("solo", False), kind=t.get("kind", "notes"), styles=list(t.get("styles", [])),
            sounds={out: Sound(s.get("program", 0), s.get("bank", 0), s.get("lsb", 0), s.get("soundfont", ""))
                    for out, s in t.get("sounds", {}).items()}))
    return song


def save_project(song: Song, path: str | Path) -> None:
    data = json.dumps(song_to_dict(song), ensure_ascii=False, indent=1).encode("utf-8")
    tmp = Path(str(path) + ".tmp")
    with gzip.open(tmp, "wb") as f:  # prima su un file temporaneo: un errore non rovina il file buono
        f.write(data)
    tmp.replace(path)


def load_project(path: str | Path) -> Song:
    with gzip.open(path, "rb") as f:
        return song_from_dict(json.loads(f.read().decode("utf-8")))
