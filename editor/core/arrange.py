"""Genera l'accompagnamento di uno stile Yamaha sugli accordi della traccia accordi.

Per ogni segmento della traccia accordi (stile + sezione + fill finale + accordi):
- il pattern della sezione si ripete dall'inizio del segmento (Intro ed Ending una volta
  sola, tagliati se il segmento è più corto); il fill finale, se c'è, prende il posto
  delle ultime misure;
- per ogni nota: l'accordo in vigore al suo inizio; se la parte non suona su quell'accordo
  (note mute / chord mute) la nota salta; altrimenti l'altezza si adatta con le regole
  CASM della parte (`transform`);
- una nota tenuta oltre un cambio d'accordo segue l'RTR: Stop la taglia; gli altri la
  tagliano e la fanno ripartire all'altezza giusta per il nuovo accordo;
- il risultato va per canale di destinazione (9–16): le varianti di una parte finiscono
  sullo stesso canale (per ogni accordo ne suona una).

Regole di trasposizione (`transform`), approssimazione dell'algoritmo Yamaha (non
pubblico): Bypass + Root Fixed (batteria) non cambia; Root Trans sposta della distanza
fra le fondamentali (oltre l'High key ripiega di un'ottava sotto); Root Fixed sceglie
l'ottava più vicina alla nota scritta; Chord porta ogni nota sulla stessa funzione nel
nuovo accordo (fondamentale, 3ª, 5ª, 7ª…) e poi sulla nota dell'accordo più vicina;
Melody/Bass la porta sullo stesso grado della scala (Bass, e "Bass On" in SFF2, usano
il basso dell'accordo con barra); le scale minori dell'NTT valgono sugli accordi minori;
i limiti di nota ripiegano di un'ottava. Le note dell'accordo vengono dal tipo
d'accordo; le altre (2ª, 4ª, 6ª, e 3ª o 7ª se l'accordo non le ha) dalla tonalità del
brano.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from fractions import Fraction

from .chord_track import CANCEL, INTERVALS, ChordEvent, pitch_class, segment_chords
from .model import Note
from .style import StyleRule

# scale degli accordi (7 gradi, semitoni dalla fondamentale) per tipo Yamaha
IONIAN = (0, 2, 4, 5, 7, 9, 11)
LYDIAN = (0, 2, 4, 6, 7, 9, 11)
MIXOLYDIAN = (0, 2, 4, 5, 7, 9, 10)
DORIAN = (0, 2, 3, 5, 7, 9, 10)
AEOLIAN = (0, 2, 3, 5, 7, 8, 10)
MELODIC_MINOR = (0, 2, 3, 5, 7, 9, 11)
HARMONIC_MINOR = (0, 2, 3, 5, 7, 8, 11)
LOCRIAN = (0, 1, 3, 5, 6, 8, 10)
DIMINISHED = (0, 2, 3, 5, 6, 8, 9)
AUGMENTED = (0, 2, 4, 5, 8, 9, 10)
SUS4 = (0, 2, 5, 5, 7, 9, 10)
SUS2 = (0, 2, 2, 5, 7, 9, 10)
SCALES = {
    0: IONIAN, 1: IONIAN, 2: IONIAN, 3: LYDIAN, 4: IONIAN, 5: IONIAN, 6: IONIAN, 7: AUGMENTED,
    8: AEOLIAN, 9: DORIAN, 10: DORIAN, 11: LOCRIAN, 12: AEOLIAN, 13: DORIAN, 14: DORIAN,
    15: MELODIC_MINOR, 16: MELODIC_MINOR, 17: DIMINISHED, 18: DIMINISHED, 19: MIXOLYDIAN, 20: SUS4,
    21: MIXOLYDIAN, 22: MIXOLYDIAN, 23: MIXOLYDIAN, 24: MIXOLYDIAN, 25: MIXOLYDIAN, 26: MIXOLYDIAN,
    27: MIXOLYDIAN, 28: (0, 2, 4, 5, 8, 9, 11), 29: AUGMENTED, 30: IONIAN, 31: IONIAN, 32: SUS4, 33: SUS2,
}
MINOR_TYPES = {8, 9, 10, 11, 12, 13, 14, 15, 16}
# NTT SFF2 con scala minore propria (vale sugli accordi minori)
NTT_MINOR_SCALES = {3: MELODIC_MINOR, 4: MELODIC_MINOR, 5: HARMONIC_MINOR, 6: HARMONIC_MINOR,
                    7: AEOLIAN, 8: AEOLIAN, 9: DORIAN, 10: DORIAN}
NTT_MINOR_SCALES_SFF1 = {4: MELODIC_MINOR, 5: HARMONIC_MINOR}
SIXTH_CHORDS = {1, 6, 9}  # Maj6, Maj6(9), m6: il 9 è la 6ª, non la 7ª


def diatonic(key) -> set[int]:
    """Classi d'altezza dell'armatura della tonalità (le sue 7 note)."""
    return {(7 * n) % 12 for n in range(key.fifths - 1, key.fifths + 6)}


def key_scale(kind: int, root: int, key) -> tuple[int, ...]:
    """Scala (7 gradi, semitoni dalla fondamentale) su cui si adattano le note. Le note
    dell'accordo vengono dal tipo d'accordo (A = La-Do#-Mi anche in Re minore: per Am si
    scrive Am); le altre (2ª, 4ª, 6ª, e 3ª o 7ª se l'accordo non le ha, es. D5 o una
    triade) dalla tonalità del brano (armatura). Senza tonalità: la scala fissa del tipo."""
    base = list(SCALES.get(kind, IONIAN))
    if key is None:
        return tuple(base)
    tones = {i % 12 for i in INTERVALS.get(kind, (0, 4, 7))}
    dia = diatonic(key)

    def pick(index: int, chord: tuple[int, ...], scale: tuple[int, ...]) -> None:
        own = next((st for st in chord if st in tones), None)
        found = own if own is not None else next((st for st in scale if (root + st) % 12 in dia), None)
        if found is not None:
            base[index] = found

    pick(1, (1, 2, 3) if kind == 27 else (1, 2), (1, 2))  # 2ª (9ª dell'accordo, se c'è)
    if kind not in (32, 33):  # sus4 / sus2: la "terza" è la 4ª / la 2ª dell'accordo
        pick(2, (3, 4), (3, 4))
    pick(3, (6,) if kind in (3, 23) else (5,), (5, 6))  # 4ª (#11 dell'accordo, se c'è)
    pick(4, (6, 7, 8), (7, 6, 8))
    if kind in SIXTH_CHORDS:  # 6ª
        base[5] = 9
    else:
        pick(5, (8, 9), (8, 9))  # (13ª / b13 dell'accordo, se c'è)
    if kind == 18:  # dim7: la settima è il 9
        base[6] = 9
    else:
        pick(6, (10, 11) if kind not in SIXTH_CHORDS else (), (10, 11))
    return tuple(base)


def chord_tones(kind: int, scale: tuple[int, ...], key) -> list[int]:
    """Note dell'accordo (classi relative alla fondamentale): sempre dal tipo d'accordo."""
    tones = {i % 12 for i in INTERVALS.get(kind, (0, 4, 7))}
    return sorted(tones)


# 2026-11-24: controller di mixaggio delle parti (volume, pan, reverb, chorus) che Generate porta nelle
# tracce "Style · …"; prima si copiavano solo le note e quelle tracce suonavano col volume di default
MIX_CONTROLS = (7, 10, 91, 93)


@dataclass
class Pattern:
    length: Fraction  # durata della sezione (semibrevi)
    notes: list[Note]  # posizioni dall'inizio della sezione
    rule: StyleRule | None
    # controller di MIX_CONTROLS in vigore all'inizio della sezione, nello stile: numero → valore
    controls: dict[int, int] = field(default_factory=dict)


@dataclass
class Part:
    name: str  # nome della parte nello stile (es. "12 ClnGt (2)")
    channel: int  # canale di destinazione
    percussion: bool
    program: int
    patterns: dict[str, Pattern] = field(default_factory=dict)  # sezione → pattern
    instrument: object | None = None  # strumento della parte (da un .stile: quello scelto)
    sounds: dict = field(default_factory=dict)  # suoni per uscita della parte (Track.sounds)


@dataclass
class StyleData:
    path: str
    parts: list[Part]
    sections: dict[str, Fraction]  # sezione → durata


# --- trasposizione di una nota -------------------------------------------------------


def _zone(rule: StyleRule, midi: int):
    return next((z for z in rule.zones if z.low <= midi <= z.high), rule.zones[0] if rule.zones else None)


def _nearest(pc: int, near: int) -> int:
    """Altezza con classe `pc` più vicina a `near`."""
    base = near - ((near - pc) % 12)
    return min((base, base + 12), key=lambda p: (abs(p - near), p))


def _degree(rel: int, scale: tuple[int, ...]) -> tuple[int, int]:
    """Grado della scala più vicino (dal basso) a `rel` (0–11) e scarto cromatico."""
    best = max((i for i, s in enumerate(scale) if s <= rel), default=0)
    return best, rel - scale[best]


def transform(midi: int, rule: StyleRule | None, chord: ChordEvent, key=None) -> int:
    """Altezza della nota scritta (sull'accordo di partenza della regola) suonata su `chord`.
    `key`: tonalità del brano in quel punto (per le note fuori dall'accordo)."""
    if rule is None or not rule.zones:
        return midi
    z = _zone(rule, midi)
    ntt = z.ntt & 0x7F
    if ntt == 0 and z.ntr == 1:  # Bypass + Root Fixed: non si tocca (batteria, frasi fisse)
        return midi
    kind = chord.kind if chord.kind in INTERVALS and chord.kind != CANCEL else 0
    root = pitch_class(chord.root)
    bass_on = (rule.sff2 and bool(z.ntt & 0x80)) or (not rule.sff2 and ntt == 3)
    ref = pitch_class(chord.bass) if bass_on and chord.bass else root
    src = rule.source_root % 12
    interval = (ref - src) % 12
    if z.ntr == 0:  # Root Trans: su fino all'High key, oltre un'ottava sotto
        shift = interval if interval <= (z.high_key - src) % 12 else interval - 12
    else:  # Root Fixed / Guitar: lo spostamento più piccolo
        shift = interval if interval <= 6 else interval - 12
    near = midi + shift
    if ntt == 0:  # Bypass: solo lo spostamento
        out = near
    else:
        src_scale = SCALES.get(rule.source_type, IONIAN)
        degree, chromatic = _degree((midi - src) % 12, src_scale)
        tgt_scale = key_scale(kind, root, key)
        minor_scales = NTT_MINOR_SCALES if rule.sff2 else NTT_MINOR_SCALES_SFF1
        if kind in MINOR_TYPES and ntt in minor_scales:
            tgt_scale = minor_scales[ntt]
        rel = tgt_scale[degree] + chromatic
        if ntt == 2 or z.ntr == 2:  # Chord (e Guitar): sulla nota dell'accordo più vicina
            tones = chord_tones(kind, tgt_scale, key)
            if degree == 6 and not any(t in (10, 11, 9) for t in tones):
                rel = 12  # 7ª su un accordo senza settima: la fondamentale
            rel = min(tones + [12], key=lambda t: (abs(t - rel % 12), t)) % 12
        out = _nearest((root + rel) % 12, near)
    while out < z.note_low and out + 12 <= 127:
        out += 12
    while out > z.note_high and out - 12 >= 0:
        out -= 12
    return max(0, min(127, out))


def plays_on(rule: StyleRule | None, chord: ChordEvent) -> bool:
    """La parte suona su quell'accordo (note mute: fondamentale; chord mute: tipo)?"""
    if rule is None:
        return True
    if chord.kind == CANCEL:
        return False
    root_ok = bool(rule.note_mute >> pitch_class(chord.root) & 1)
    kind_ok = chord.kind > CANCEL or bool(rule.chord_mute >> chord.kind & 1)
    return root_ok and kind_ok


# --- generazione di un segmento ------------------------------------------------------


def _trim_style_segments(song, tracks, regenerated: list[tuple], removed: list) -> tuple[list, list]:
    """Parti dei segmenti delle tracce di stile fuori dai tratti `regenerated`.

    Riempie `removed` con i segmenti tutti dentro; ritorna (ridimensionati [(segmento, inizio,
    fine)], aggiunti [(traccia, segmento, gruppo)]) per quelli che sporgono: la prima parte che
    resta tiene il segmento (Resize), le altre diventano segmenti nuovi con una copia del gruppo."""
    from .model import Segment

    resized, extra = [], []
    for track in tracks:
        for seg in track.segments:
            s0, s1 = song.segment_span(seg)
            if not any(a < s1 and b > s0 for a, b in regenerated):
                continue
            keep = [(s0, s1)]  # parti che restano: si tolgono uno per uno i tratti rigenerati
            for a, b in regenerated:
                keep = [part for x0, x1 in keep for part in ((x0, min(x1, a)), (max(x0, b), x1)) if part[1] > part[0]]
            if not keep:
                removed.append((track, seg))
                continue
            first, *others = keep
            resized.append((seg, first[0], first[1]))
            for x0, x1 in others:
                group = song.groups[seg.group_id].copy()
                bar, offset = song.split_position(x0)
                extra.append((track, Segment(group.id, bar, x1 - x0, seg.transpose, offset, seg.trim + (x0 - s0),
                                             label=seg.label), group))
    return resized, extra


def _chord_at(changes: list[tuple[Fraction, ChordEvent]], t: Fraction) -> ChordEvent:
    current = changes[0][1]
    for pos, e in changes:
        if pos > t:
            break
        current = e
    return current


def _blocks(seg_start: Fraction, seg_end: Fraction, data: StyleData, section: str,
            fill: str) -> list[tuple[str, Fraction, Fraction, bool]]:
    """Tratti del segmento: (sezione, inizio, fine, si ripete)."""
    fill_len = data.sections.get(fill, Fraction(0)) if fill else Fraction(0)
    # 2026-11-20: il fill prende le ultime misure anche quando è lungo quanto (o più del)
    # segmento: allora c'è solo il fill (prima veniva ignorato e restava la sezione)
    main_end = max(seg_start, seg_end - fill_len) if fill_len > 0 else seg_end
    once = section.startswith(("Intro", "Ending"))
    out = [(section, seg_start, main_end, not once)] if main_end > seg_start else []
    if main_end < seg_end:
        out.append((fill, main_end, seg_end, False))
    return out


def generate(changes: list[tuple[Fraction, ChordEvent]], seg_start: Fraction, seg_end: Fraction,
             data: StyleData, section: str, fill: str = "", key_at=lambda _pos: None) -> dict[int, list[Note]]:
    """Note generate per canale di destinazione, con posizioni dall'inizio del segmento.
    `changes`: accordi (posizione nel brano, accordo) in ordine; senza accordi: niente.
    `key_at`: posizione → tonalità del brano (None = scale fisse dei tipi d'accordo)."""
    out: dict[int, list[Note]] = {}
    if not changes:
        return out
    for name, start, end, loop in _blocks(seg_start, seg_end, data, section, fill):
        for part in data.parts:
            pattern = part.patterns.get(name)
            if pattern is None or not pattern.notes or pattern.length <= 0:
                continue
            notes = out.setdefault(part.channel, [])
            origin = start
            while origin < end:
                for n in pattern.notes:
                    on = origin + n.start
                    if on >= end or on < start:
                        continue
                    off = min(on + n.duration, end)
                    notes += _play(n, on, off, part, pattern.rule, changes, seg_start, key_at)
                if not loop:
                    break
                origin += pattern.length
    for notes in out.values():
        notes.sort(key=lambda n: (n.start, n.midi))
    return out


def _play(n: Note, on: Fraction, off: Fraction, part: Part, rule: StyleRule | None,
          changes: list[tuple[Fraction, ChordEvent]], seg_start: Fraction, key_at) -> list[Note]:
    """La nota (assoluta on–off), adattata agli accordi; spezzata ai cambi d'accordo (RTR)."""
    cuts = [pos for pos, _e in changes if on < pos < off]
    pieces, start = [], on
    for cut in cuts + [off]:
        chord = _chord_at(changes, start)
        if plays_on(rule, chord):
            midi = n.midi if part.percussion else transform(n.midi, rule, chord, key_at(start))
            pieces.append(n.copy(start=start - seg_start, duration=cut - start, midi=midi, spelling=None,
                                 tie_next=False))
        if part.percussion or (rule is not None and rule.zones and _zone(rule, n.midi).rtr == 0):
            break  # batteria: il colpo resta uno; RTR Stop: la nota si ferma al cambio d'accordo
        start = cut
    return pieces


# --- scrittura nelle tracce dello stile -----------------------------------------------


def _mix_events(data: StyleData, channel: int, seg_start: Fraction, seg_end: Fraction, seg) -> list:
    """Volume, pan, reverb e chorus della parte (MIX_CONTROLS) all'inizio del segmento generato e, se
    cambiano, all'inizio del fill: i valori che lo stile ha all'inizio di quella sezione. Più parti sullo
    stesso canale (varianti): vale la prima che ha il controller.
    2026-11-24: prima Generate copiava solo le note (segnalato dall'utente: volumi dello stile persi)."""
    from .model import CC, ControlEvent

    parts = [p for p in data.parts if p.channel == channel]
    events, current = [], {}
    for name, start, _end, _loop in _blocks(seg_start, seg_end, data, seg.section, seg.fill):
        controls: dict[int, int] = {}
        for part in parts:  # la prima parte che ha il controller vince
            pattern = part.patterns.get(name)
            for number, value in (pattern.controls if pattern else {}).items():
                controls.setdefault(number, value)
        for number, value in sorted(controls.items()):  # solo ciò che cambia rispetto al tratto prima
            if current.get(number) != value:
                events.append(ControlEvent(start - seg_start, CC, number, value))
                current[number] = value
    return events



def _part_title(name: str) -> str:
    """"12 ClnGt (2)" → "ClnGt"."""
    words = name.split(" ", 1)
    title = words[1] if len(words) > 1 and words[0].isdigit() else name
    return title.rsplit(" (", 1)[0] if title.endswith(")") else title


def generation_command(song, segments: list, load) -> tuple[object | None, list[str]]:
    """Comando (un passo di Ctrl+Z) che genera i segmenti della traccia accordi indicati:
    per ognuno, nelle tracce "Stile · …" (una per canale 9–16, kind "style", create se
    mancano) toglie quello che c'era in quel tratto e mette le note nuove. `load`: percorso
    dello stile → StyleData. Ritorna (comando o None, avvisi)."""
    from .clef import BASS, TREBLE
    from .document import AddSegments, AddTrack, Compound, DeleteSegments, ResizeSegment
    from .instrument import GMInstrument
    from .model import Group, Segment, Track

    warnings: list[str] = []
    style_tracks = {t.channel: t for t in song.tracks if t.kind == "style"}
    # 2026-11-20: una traccia "Style · …" a cui l'utente ha cambiato canale si ritrova per nome
    # (prima se ne creava un'altra sul canale dello stile e quella vecchia restava vuota)
    by_name = {t.name: t for t in song.tracks if t.kind == "style"}
    new_tracks: list = []
    removed, added = [], []
    regenerated: list[tuple] = []  # tratti (inizio, fine) rigenerati
    for seg in segments:
        label = song.display_label(seg) or "segment"
        if not seg.style or not seg.section:
            warnings.append(f"{label}: missing style or section")
            continue
        try:
            data = load(seg.style)
        except OSError:
            warnings.append(f"{label}: style not found ({seg.style})")
            continue
        if seg.section not in data.sections:
            # 2026-11-20: sezione che lo stile non ha (es. dopo un cambio di stile): prima il tratto
            # si svuotava e il messaggio diceva "Generated"
            warnings.append(f"{label}: the style has no {seg.section}")
            continue
        start, end = song.segment_span(seg)
        # 2026-11-20: segment_chords comprende l'accordo in vigore all'inizio (dopo un Resize
        # dell'inizio); prima si usava il primo accordo successivo, cioè quello sbagliato
        changes = [(start + rel, e) for rel, e in segment_chords(song, seg)]
        if not changes:
            warnings.append(f"{label}: no chords")
            continue
        out = generate(changes, start, end, data, seg.section, seg.fill,
                       key_at=lambda pos: song.keys.at(song.meters.bar_at(pos)))
        regenerated.append((start, end))  # rigenerare: via quello che c'era nel tratto (sotto)
        for channel, notes in sorted(out.items()):
            if not notes:
                continue
            track = style_tracks.get(channel)
            part = next(p for p in data.parts if p.channel == channel)
            if track is None and f"Style · {_part_title(part.name)}" in by_name:
                track = style_tracks[channel] = by_name[f"Style · {_part_title(part.name)}"]
            if track is None:
                avg = sum(n.midi for n in notes) / len(notes)
                instrument = part.instrument or GMInstrument(0 if part.percussion else part.program,
                                                             percussion=part.percussion)
                # 2026-11-20: "Style · …" in inglese (prima "Stile · …"): le tracce si ritrovano per kind, non per nome
                track = Track(f"Style · {_part_title(part.name)}", instrument,
                              TREBLE if part.percussion or avg >= 60 else BASS,
                              channel=channel, kind="style", sounds=dict(part.sounds))
                style_tracks[channel] = track
                new_tracks.append(track)
            bars = song.meters.bar_ceil(end) - song.meters.bar_at(start)
            g = Group(seg.section, max(bars, 1), notes, events=_mix_events(data, channel, start, end, seg))
            bar, offset = song.split_position(start)
            text = seg.section + (f" + {seg.fill}" if seg.fill else "")
            added.append((track, Segment(g.id, bar, end - start, 0, offset, label=text), g))
    # 2026-11-20: dai segmenti "Style · …" si toglie solo la parte dentro i tratti rigenerati: un
    # segmento tutto dentro si cancella, uno che sporge si accorcia (Resize: il contenuto fuori
    # resta), uno che sporge da entrambi i lati diventa due. Prima si cancellava per intero.
    # tracce una volta sola: una ritrovata per nome sta nel dizionario sotto due canali, e trattata due
    # volte duplicava il pezzo destro di un segmento tagliato (2026-11-21)
    unique = list({id(t): t for t in style_tracks.values()}.values())
    resized, extra = _trim_style_segments(song, unique, regenerated, removed)
    if not added and not removed and not resized:
        return None, warnings
    commands = [AddTrack(t, len(song.tracks) + i) for i, t in enumerate(sorted(new_tracks, key=lambda t: t.channel))]
    if removed:
        commands.append(DeleteSegments(removed))
    commands += [ResizeSegment(s, a, b) for s, a, b in resized]
    if extra:
        commands.append(AddSegments(extra))
    if added:
        commands.append(AddSegments(added))
    return Compound(commands, "Generate style"), warnings
