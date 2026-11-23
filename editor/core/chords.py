"""Accordi standard (Composer > Chord…): note di un accordo da sigla.

- Fondamentale: una grafia (C, F#, Bb…).
- Tipo (uno solo): maggiore, minore, diminuito, aumentato, sus2, sus4.
- Flag: 6, 7, maj7, 9, 11, 13, add9 e "8" (raddoppio della fondamentale all'ottava:
  quattro voci). Con il tipo diminuito, 7 dà la settima diminuita (dim7). 9, 11 e 13
  portano con sé la 7ª (minore, o maggiore con maj7); 11 toglie la 3ª; 13 senza 11ª.
- Forma: stretta (terze sovrapposte sopra la fondamentale) o lata (la 3ª, o la nota
  al suo posto, sale di un'ottava).

Ogni nota ha la grafia data dalla sigla (F#7 = Fa# La# Do# Mi); dove servirebbe una
doppia alterazione la grafia resta automatica (dalla tonalità).
"""

from __future__ import annotations

from dataclasses import dataclass

from .pitch import Spelling

# intervalli: (passi sulla linea delle quinte, semitoni)
P1, M2, M3, m3, P4, d5, P5, A5, M6, d7, m7, M7 = (
    (0, 0), (2, 2), (4, 4), (-3, 3), (-1, 5), (-6, 6), (1, 7), (8, 8), (3, 9), (-9, 9), (-2, 10), (5, 11))
M9, P11, M13 = (2, 14), (-1, 17), (3, 21)

TYPES = {  # tipo → intervalli della triade (oltre alla fondamentale)
    "maj": (M3, P5), "min": (m3, P5), "dim": (m3, d5), "aug": (M3, A5), "sus2": (M2, P5), "sus4": (P4, P5),
}
TYPE_NAMES = [("maj", "major"), ("min", "minor"), ("dim", "diminished"), ("aug", "augmented"),
              ("sus2", "sus2"), ("sus4", "sus4")]
FLAGS = ["6", "7", "maj7", "9", "11", "13", "add9", "8"]
CLOSE, OPEN = "close", "open"


@dataclass(frozen=True)
class ChordTone:
    midi: int
    spelling: Spelling | None  # None = automatica (doppia alterazione evitata)


def chord_intervals(kind: str, flags: set[str]) -> list[tuple[int, int]]:
    """Intervalli dalla fondamentale (fondamentale compresa), dal basso verso l'alto."""
    triad = list(TYPES[kind])
    tones = [P1] + triad
    seventh = None
    if "maj7" in flags:
        seventh = M7
    elif "7" in flags or {"9", "11", "13"} & flags:
        seventh = d7 if kind == "dim" and "7" in flags else m7
    if "6" in flags:
        tones.append(M6)
    if seventh:
        tones.append(seventh)
    if {"9", "11", "13"} & flags:
        tones.append(M9)
    elif "add9" in flags:
        tones.append(M9)
    if "11" in flags and "13" not in flags:
        tones.append(P11)
        tones = [t for t in tones if t not in (M3, m3)]  # l'11ª stona con la 3ª
    if "13" in flags:
        tones.append(M13)
    return sorted(set(tones), key=lambda t: t[1])


def build_chord(root: Spelling, kind: str, flags: set[str], form: str = CLOSE, octave: int = 4) -> list[ChordTone]:
    """Note dell'accordo: fondamentale nell'ottava `octave` (4 = quella del Do centrale)."""
    root_midi = (octave + 1) * 12 + root.pitch_class
    tones = []
    for fifths, semis in chord_intervals(kind, flags):
        try:
            sp = Spelling.from_fifths(root.fifths + fifths)
            sp = sp if abs(sp.alter) < 2 else None  # niente doppie alterazioni
        except ValueError:
            sp = None
        tones.append(ChordTone(root_midi + semis, sp))
    if form == OPEN and len(tones) > 2:  # lata: la seconda nota dal basso (la 3ª) sale di un'ottava
        third = tones[1]
        tones = [tones[0]] + tones[2:] + [ChordTone(third.midi + 12, third.spelling)]
        tones.sort(key=lambda t: t.midi)
    if "8" in flags:  # quattro voci: la fondamentale raddoppiata sopra la nota più alta
        top = max(t.midi for t in tones)
        double = root_midi + 12 * ((top - root_midi) // 12 + 1)
        tones.append(ChordTone(double, tones[0].spelling))
    return tones


def chord_name(root: Spelling, kind: str, flags: set[str]) -> str:
    """Sigla leggibile, es. "C7", "Am9", "Fmaj7", "Gsus4 add9"."""
    base = {"maj": "", "min": "m", "dim": "dim", "aug": "aug", "sus2": "sus2", "sus4": "sus4"}[kind]
    ext = ""
    for f in ("13", "11", "9"):
        if f in flags:
            ext = ("maj" if "maj7" in flags else "") + f
            break
    else:
        if "maj7" in flags:
            ext = "maj7"
        elif "7" in flags:
            ext = "7"
    extra = [f for f in ("6", "add9") if f in flags and not (f == "add9" and ext)]
    name = f"{root}{base}{'6' if '6' in extra else ''}{ext}"
    return name + (" add9" if "add9" in extra else "")


def place_symbol(chords: list[tuple], pos, name: str) -> list[tuple]:
    """Sigla di un accordo inserito dal Composer, "solo dove l'accordo cambia": se la sigla
    in vigore prima di `pos` è uguale, niente; nello stesso punto sostituisce; una sigla
    successiva uguale (diventata inutile) viene tolta. La prima del segmento c'è sempre."""
    out = [(p, c) for p, c in sorted(chords) if p != pos]
    before = [c for p, c in out if p < pos]
    if not (before and before[-1] == name):
        out.append((pos, name))
    out.sort()
    result = []
    for p, c in out:
        if result and result[-1][1] == c and p > pos >= result[-1][0]:
            continue  # uguale a quella appena messa (o in vigore): inutile
        result.append((p, c))
    return result


SYMBOL_PATTERN = (r"^[A-G](#|b|♯|♭)?(maj|min|m|M|dim|aug|sus|add|°|ø|\+|-|\d|#|b|♯|♭|\(|\)|,)*"
                  r"(/[A-G](#|b|♯|♭)?)?$")


def looks_like_symbol(text: str) -> bool:
    """Un testo (es. evento Text del MIDI) che sembra una sigla d'accordo: C, F#m7, Bbmaj7, D/F#…"""
    import re

    return bool(re.match(SYMBOL_PATTERN, text.strip()))


def dedupe_symbols(chords: list[tuple]) -> list[tuple]:
    """Toglie le sigle uguali alla precedente (import)."""
    out = []
    for p, c in sorted(chords):
        if not out or out[-1][1] != c:
            out.append((p, c))
    return out


# --- Detect Chords: riconoscere un accordo dalle note --------------------------------

# schemi: (suffisso della sigla, classi d'altezza dalla fondamentale), dal più semplice
TEMPLATES = [
    ("", {0, 4, 7}), ("m", {0, 3, 7}), ("dim", {0, 3, 6}), ("aug", {0, 4, 8}),
    ("sus4", {0, 5, 7}), ("sus2", {0, 2, 7}), ("5", {0, 7}),
    ("7", {0, 4, 7, 10}), ("maj7", {0, 4, 7, 11}), ("m7", {0, 3, 7, 10}), ("6", {0, 4, 7, 9}),
    ("m6", {0, 3, 7, 9}), ("dim7", {0, 3, 6, 9}), ("m7b5", {0, 3, 6, 10}), ("7sus4", {0, 5, 7, 10}),
    ("add9", {0, 2, 4, 7}), ("madd9", {0, 2, 3, 7}), ("9", {0, 2, 4, 7, 10}), ("maj9", {0, 2, 4, 7, 11}),
    ("m9", {0, 2, 3, 7, 10}),
]
SHARP_NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]
FLAT_NAMES = ["C", "Db", "D", "Eb", "E", "F", "Gb", "G", "Ab", "A", "Bb", "B"]


def detect_chord(midis: list[int], flats: bool = False, slash: bool = True) -> str | None:
    """Sigla dell'accordo formato dalle note (altezze MIDI), o None se non è un accordo
    riconoscibile. Si sceglie lo schema che spiega più note con meno note mancanti (la 5ª
    può mancare) ed estranee; a parità, la nota più grave come fondamentale e lo schema più
    semplice. Con `slash`, se il basso non è la fondamentale: "C/E"."""
    if not midis:
        return None
    pcs = {m % 12 for m in midis}
    bass = min(midis) % 12
    best = None
    for root in pcs:
        rel = {(p - root) % 12 for p in pcs}
        for order, (suffix, template) in enumerate(TEMPLATES):
            matched = len(rel & template)
            missing = template - rel
            extra = len(rel - template)
            if matched < min(3, len(template)) or extra > 1:
                continue
            cost = sum(0.5 if m == 7 else 2.0 for m in missing) + 2.5 * extra
            score = (matched * 2 - cost, root == bass, -order)
            if best is None or score > best[0]:
                best = (score, root, suffix)
    if best is None or best[0][0] < 4:
        return None
    _score, root, suffix = best
    names = FLAT_NAMES if flats else SHARP_NAMES
    name = names[root] + suffix
    if slash and bass != root:
        name += "/" + names[bass]
    return name
