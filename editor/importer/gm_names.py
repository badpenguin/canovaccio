"""Strumento GM approssimato dal nome di una parte (stili Yamaha: "Bass", "ClnGt",
"E.Pno", "Str"…). I suoni Yamaha non seguono la numerazione GM (es. il basso Mega Voice
ha programma 17, che in GM è un organo): il nome della parte dice di più.
Le regole vanno dalla più specifica alla più generica; si confrontano le parole del nome
(minuscole, divise su spazi e segni, e anche il nome attaccato)."""

from __future__ import annotations

import re

# (parole o pezzi da cercare, programma GM 0–127)
RULES = [
    (("fretless",), 35), (("slap",), 36), (("synbass", "synbs", "synthbass"), 38), (("acbass", "upright", "woodbass", "contrabass"), 32),
    (("pickbass", "pick"), 34), (("bass", "bs"), 33),
    (("steeldr", "steeldrum", "steel dr"), 114), (("nylon",), 24), (("steel", "acgt", "acgtr", "folk", "12str"), 25), (("jazzgt", "jazzgtr"), 26),
    (("mute", "mutegt", "mutegtr"), 28), (("dist", "drive", "od", "lead gt"), 30),
    (("clngt", "clean", "gt", "gtr", "guit", "guitar", "eg"), 27),
    (("epiano", "epno", "ep", "e p", "rhodes", "dx", "tine", "wurli"), 4), (("clav", "clavi"), 7), (("harpsi", "harpsichord"), 6),
    (("piano", "pno", "grand", "pf"), 0),
    (("church", "pipe"), 19), (("rockorg", "rock organ"), 18), (("organ", "org", "drawbar", "jazzorg"), 16),
    (("accordion", "acc", "accord", "musette"), 21), (("harmonica", "harm"), 22),
    (("violin", "vln"), 40), (("cello",), 42), (("pizz",), 45), (("harp",), 46),
    (("str", "strings", "string", "ens"), 48),
    (("trumpet", "tp", "trp", "tpt"), 56), (("trombone", "tb", "trb"), 57), (("frhorn", "horn", "hrn"), 60),
    (("tenor", "tensax"), 66), (("sax", "altosax", "as"), 65), (("brass", "brs", "brss"), 61),
    (("piccolo", "picolo", "pic"), 72), (("flute", "fl", "flt"), 73), (("clarinet", "clar", "cl"), 71), (("oboe",), 68),
    (("pan", "panflute"), 75), (("whistle",), 78),
    (("choir", "vox", "voice", "aah", "ooh"), 52), (("pad", "warm"), 89), (("bell", "bells"), 14), (("marimba", "mrmba"), 12),
    (("vibes", "vibraphone", "vib"), 11), (("xylo",), 13), (("celesta",), 8),
    (("sitar",), 104), (("synth", "syn", "lead", "saw", "square"), 80), (("fantasy",), 88),
]


def gm_from_name(name: str) -> int | None:
    """Programma GM per il nome della parte, o None se il nome non dice niente."""
    low = name.lower()
    words = set(re.findall(r"[a-z0-9]+", low))
    spaced = " " + " ".join(re.findall(r"[a-z0-9]+", low)) + " "
    joined = spaced.replace(" ", "")
    for keys, program in RULES:
        for k in keys:
            if (" " in k and f" {k} " in spaced) or k in words or (len(k) >= 4 and k.replace(" ", "") in joined):
                return program
    return None
