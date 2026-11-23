"""Traccia accordi: una sola per brano, in cima alla scheda Tracce. Non suona; dice quali
accordi valgono e quando (serve agli stili). È separata dalle sigle delle tracce
normali, che sono solo testi per la partitura.

I suoi segmenti hanno gruppi con `chord_events` invece di note. Un evento: posizione
esatta nel gruppo (come Note.start, non agganciata al movimento), fondamentale, tipo
d'accordo Yamaha (0–34, quelli del Chord Looper; altri numeri si tengono) e basso
facoltativo (accordo con barra, es. D/F#).
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from fractions import Fraction

from .style import CHORD_TYPES

CHORDS = "chords"  # Track.kind della traccia accordi
CANCEL = 34  # tipo "cc": nessun accordo (N.C.)
ROOTS = ["A", "A#", "Bb", "B", "C", "C#", "Db", "D", "D#", "Eb", "E", "F", "F#", "Gb", "G", "G#", "Ab"]  # da A a G


def kind_name(kind: int) -> str:
    """Sigla del tipo dopo la fondamentale: "" (maggiore), "m7", "sus4"… Fuori tabella: "(38)"."""
    if kind == CANCEL:
        return "N.C."
    if 0 <= kind < len(CHORD_TYPES):
        return {8: "m", 9: "m6", 10: "m7", 12: "m(9)", 13: "m7(9)", 14: "m7(11)", 15: "mMaj7",
                16: "mMaj7(9)", 11: "m7b5", 30: "(1+8)", 31: "5", 33: "sus2"}.get(kind, CHORD_TYPES[kind])
    return f"({kind})"


@dataclass(frozen=True)
class ChordEvent:
    pos: Fraction  # dall'inizio del gruppo, in semibrevi
    root: str  # grafia della fondamentale, es. "F#"
    kind: int  # tipo d'accordo Yamaha
    bass: str = ""  # basso dell'accordo con barra ("" = nessuno)

    @property
    def name(self) -> str:
        if self.kind == CANCEL:
            return "N.C."
        return self.root + kind_name(self.kind) + (f"/{self.bass}" if self.bass else "")


# intervalli (semitoni dalla fondamentale) dei tipi d'accordo Yamaha 0–34
INTERVALS = {
    0: (0, 4, 7), 1: (0, 4, 7, 9), 2: (0, 4, 7, 11), 3: (0, 4, 7, 11, 18), 4: (0, 4, 7, 14),
    5: (0, 4, 7, 11, 14), 6: (0, 4, 7, 9, 14), 7: (0, 4, 8), 8: (0, 3, 7), 9: (0, 3, 7, 9),
    10: (0, 3, 7, 10), 11: (0, 3, 6, 10), 12: (0, 3, 7, 14), 13: (0, 3, 7, 10, 14), 14: (0, 3, 7, 10, 17),
    15: (0, 3, 7, 11), 16: (0, 3, 7, 11, 14), 17: (0, 3, 6), 18: (0, 3, 6, 9), 19: (0, 4, 7, 10),
    20: (0, 5, 7, 10), 21: (0, 4, 6, 10), 22: (0, 4, 7, 10, 14), 23: (0, 4, 7, 10, 18), 24: (0, 4, 7, 10, 21),
    25: (0, 4, 7, 10, 13), 26: (0, 4, 7, 10, 20), 27: (0, 4, 7, 10, 15), 28: (0, 4, 8, 11), 29: (0, 4, 8, 10),
    30: (0, 12), 31: (0, 7), 32: (0, 5, 7), 33: (0, 2, 7), CANCEL: (),
}
MIDDLE_C = 60
BASS_OCTAVE = 36  # basso della Preview: ottava del Do2
PREVIEW_VELOCITY = 70


def pitch_class(name: str) -> int:
    from .pitch import Spelling

    return Spelling.parse(name).pitch_class


def voicing(e: ChordEvent) -> list[int]:
    """Note della Preview: l'accordo dalla fondamentale nell'ottava del Do centrale, più la
    nota di basso (fondamentale, o basso dell'accordo con barra) nell'ottava del Do2.
    Tipi sconosciuti: triade maggiore. N.C.: niente."""
    if e.kind == CANCEL:
        return []
    root = MIDDLE_C + pitch_class(e.root)
    chord = [root + i for i in INTERVALS.get(e.kind, (0, 4, 7))]
    bass = BASS_OCTAVE + pitch_class(e.bass or e.root)
    return [bass] + chord


def segment_chords(song, seg) -> list[tuple]:
    """Accordi di un segmento della traccia accordi: [(posizione dall'inizio del segmento,
    evento)] in ordine. Decisione 2026-11-20: dopo un Resize dell'inizio (trim) l'accordo in
    vigore in quel punto vale dall'inizio del segmento (come fa già Split per la parte destra);
    prima si perdeva: Preview muta e Genera con l'accordo successivo."""
    events = sorted(song.groups[seg.group_id].chord_events, key=lambda e: e.pos)
    inside = [(e.pos - seg.trim, e) for e in events if 0 <= e.pos - seg.trim < seg.length]
    before = [e for e in events if e.pos < seg.trim]
    if before and (not inside or inside[0][0] > 0):
        inside.insert(0, (0 * seg.trim, before[-1]))
    return inside


def preview_notes(song, track):
    """(inizio, fine, altezza, velocity) della Preview della traccia accordi: ogni accordo
    tenuto fino al successivo (o alla fine del segmento)."""
    for seg in track.segments:
        start, end = song.segment_span(seg)
        chords = segment_chords(song, seg)
        for k, (rel, e) in enumerate(chords):
            on = start + rel
            off = start + chords[k + 1][0] if k + 1 < len(chords) else end
            if off > on:
                for midi in voicing(e):
                    yield on, off, midi, PREVIEW_VELOCITY


def detect_key(changes: list[tuple[Fraction, Fraction, ChordEvent]]):
    """Tonalità più probabile per una sequenza di accordi [(inizio, fine, accordo)]: per
    ognuna delle 24 tonalità conta quanto tempo le note degli accordi stanno nella sua
    armatura, più il tempo passato sull'accordo di tonica (maggiore o minore secondo il
    modo) e un premio se il primo (soprattutto) e l'ultimo accordo sono la tonica. None se
    non ci sono accordi."""
    from .key import MAJOR, MINOR, Key

    chords = [(b - a, e) for a, b, e in changes if e.kind != CANCEL and b > a]
    if not chords:
        return None
    total = sum(d for d, _e in chords)
    best = None
    for mode in (MAJOR, MINOR):
        for fifths in range(-6, 7):
            key = Key.from_fifths(fifths, mode)
            dia = {(7 * n) % 12 for n in range(key.fifths - 1, key.fifths + 6)}
            score = Fraction(0)
            for d, e in chords:
                root = pitch_class(e.root)
                tones = [(root + i) % 12 for i in INTERVALS.get(e.kind, (0, 4, 7))]
                score += d * Fraction(sum(t in dia for t in tones), len(tones))
            tonic = key.tonic.pitch_class

            def is_tonic(e: ChordEvent) -> bool:
                minor = e.kind in (8, 9, 10, 11, 12, 13, 14, 15, 16)
                return pitch_class(e.root) == tonic and (minor == (mode is MINOR) or e.kind in (30, 31))

            score += sum(d for d, e in chords if is_tonic(e)) / 2  # tempo passato sulla tonica
            score += total / 2 * is_tonic(chords[0][1]) + total / 4 * is_tonic(chords[-1][1])
            if best is None or score > best[0]:
                best = (score, key)
    return best[1]


# --- registrazione di accordi (Rec sulla traccia accordi) --------------------------
# Decisione dell'utente 2026-11-20: il Rec esistente, sulla traccia accordi, non salva le
# note suonate ma le decodifica in accordi, agganciati alla griglia di 1/8.

RECORD_SNAP = Fraction(1, 8)  # griglia degli accordi registrati (più grossa: Quantize nel Chord Editor)
CHORD_SETTLE = Fraction(1, 32)  # note attaccate entro questo tempo formano lo stesso accordo


def detect_chord_kind(midis: list[int]) -> tuple[int, int] | None:
    """(classe della fondamentale 0–11, tipo Yamaha) dell'accordo formato dalle note, o None.

    Come il modo "Fingered" delle tastiere Yamaha: i rivolti valgono come lo stato
    fondamentale (niente basso con barra); la 5ª può mancare; una nota sola è "1+8" e
    fondamentale + 5ª è "5". Fra più letture vince quella completa, poi quella con la
    fondamentale al basso (C6 / Am7), poi il tipo col numero più basso."""
    if not midis:
        return None
    pcs = {m % 12 for m in midis}
    bass = min(midis) % 12
    best = None
    for root in sorted(pcs):
        rel = {(p - root) % 12 for p in pcs}
        for kind, intervals in INTERVALS.items():
            if kind == CANCEL:
                continue
            template = {i % 12 for i in intervals}
            if rel == template:
                complete = True
            elif len(template) >= 3 and 7 in template and rel == template - {7}:
                complete = False  # quinta omessa (es. Do-Mi-Sib = C7)
            else:
                continue
            score = (complete, root == bass, -kind)
            if best is None or score > best[0]:
                best = (score, root, kind)
    return (best[1], best[2]) if best is not None else None


def chords_from_notes(song, start: Fraction, notes: list, length: Fraction) -> list[ChordEvent]:
    """Accordi suonati in una registrazione: `notes` (core.model.Note, posizioni dall'inizio
    `start` nel brano) → eventi accordo agganciati a RECORD_SNAP (la linea più vicina, nel
    metro di quella misura), dentro `length`.

    Un accordo nasce a ogni gruppo di attacchi (entro CHORD_SETTLE dal primo): con almeno tre
    note diverse vale da solo (le note di prima ancora giù sono legato), con una o due si
    aggiungono a quelle ancora tenute (es. la 7ª su un accordo); rilasciare i tasti non cambia l'accordo
    (resta in vigore, come sul PSR). Note che non fanno un accordo: ignorate. Due accordi
    nello stesso punto della griglia: resta l'ultimo; lo stesso accordo ripetuto: uno solo."""
    from .chords import FLAT_NAMES, SHARP_NAMES

    onsets = sorted({n.start for n in notes})
    clusters: list[Fraction] = []
    for t in onsets:  # inizio di ogni gruppo di attacchi
        if not clusters or t >= clusters[-1] + CHORD_SETTLE:
            clusters.append(t)
    snapped: dict[Fraction, tuple[int, int]] = {}
    for c in clusters:
        until = c + CHORD_SETTLE
        attacked = [n.midi for n in notes if c <= n.start <= until]  # anche brevissime
        if len({m % 12 for m in attacked}) >= 3:
            # accordo intero suonato adesso: le note di prima ancora giù sono legato che si rilascia
            held = attacked
        else:
            # una o due note aggiunte (es. la 7ª su un accordo tenuto): insieme a quelle ancora giù
            held = attacked + [n.midi for n in notes if n.start < c and n.start + n.duration > until]
        found = detect_chord_kind(held)
        if found is None:
            continue
        bar, off = song.split_position(start + c)
        # linea più vicina (a metà strada: quella dopo)
        pos = song.meters.bar_start(bar) + math.floor(off / RECORD_SNAP + Fraction(1, 2)) * RECORD_SNAP - start
        if 0 <= pos < length:
            snapped[pos] = found  # in ordine di tempo: a parità di punto vince l'ultimo
    events: list[ChordEvent] = []
    previous = None
    for pos in sorted(snapped):
        root, kind = snapped[pos]
        if (root, kind) == previous:
            continue  # stesso accordo di prima: nessun cambio
        previous = (root, kind)
        flats = song.keys.at(song.meters.bar_at(start + pos)).fifths < 0  # grafia dalla tonalità
        events.append(ChordEvent(pos, (FLAT_NAMES if flats else SHARP_NAMES)[root], kind))
    return events
