"""Import di un progetto Rosegarden (.rg) nel modello interno.

Decisione 2026-11-20: si importa solo ciò che il modello sa rappresentare; il resto
(audio, testi delle canzoni, tenuto, dinamiche, marker della composizione, volume/pan e
banco degli strumenti, grafia delle alterazioni) si scarta senza avvisi.

Formato: XML compresso con gzip (anche non compresso è accettato). Il tempo è in unità
da 960 per semiminima (3840 per semibreve): `Fraction(t, 3840)` è esatto, terzine comprese.

Tempo degli eventi dentro un `<segment>` (verificato sui tempi di notazione salvati):
- si parte da `start` del segmento; ogni evento parte dalla fine del precedente;
- `absoluteTime` fissa il tempo, `timeOffset` lo sposta rispetto a quel punto;
- dentro `<chord>` gli eventi partono tutti dallo stesso tempo e alla fine il tempo
  avanza della durata più breve diversa da zero fra quelle del chord.

Mappatura:
- `<track>` con almeno un segmento → Track (nome = label; se vuota, canale + strumento GM),
  mute e solo; canale e programma dallo strumento dello studio; MSB 120/126/127 o
  `percussion="true"` → batteria.
- `<segment>` → Segment + Group; `endmarker` = fine (le note oltre si tolgono, quelle a
  cavallo si accorciano); `label` → etichetta; `transpose` → trasposizione.
- Inizio fuori battuta: il segmento si anticipa al movimento precedente e le note si
  spostano nel gruppo, così il suono resta identico (decisione dell'utente 2026-11-20:
  le sovrapposizioni col segmento prima vanno bene).
- `repeat="true"`: cloni ripetuti fino al segmento successivo della traccia (o alla fine
  della composizione); l'ultimo può essere parziale.
- Cambio di chiave a metà segmento: il segmento si divide lì (decisione dell'utente
  2026-11-20: così la parte si sposta facilmente su un'altra traccia); la chiave della
  traccia è quella del primo segmento.
- Note: altezza, velocity, `tiedforward` → tie_next, mark "staccato" → staccato.
- Indicazione "slur" → legatura di frase fra la prima e l'ultima nota coperte.
- Testi di tipo "Chord" → sigle d'accordo del gruppo.
- controller (senza Bank Select), pitchbend, channelpressure, keypressure → ControlEvent.
- `<tempo>`, `<timesignature>` della composizione → tabelle globali; keychange dei
  segmenti → tonalità globale (in caso di conflitto vince la prima nel tempo).
"""

from __future__ import annotations

import gzip
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from fractions import Fraction
from pathlib import Path

from ..core.clef import ALTO, BASS, PERCUSSION, TENOR, TREBLE, TREBLE_8VB, Clef
from ..core.instrument import GM_PROGRAMS, GMInstrument
from ..core.key import MAJOR, MINOR, Key
from ..core.model import BEND, CC, POLY_PRESSURE, PRESSURE, ControlEvent, Group, Note, Segment, Song, Track
from ..core.pitch import Spelling
from ..core.timeline import KeyTable, MeterTable, TempoTable, TimeSignature
from .midi import DRUM_KIT_MSB, PERCUSSION_CHANNEL

WHOLE = 3840  # unità Rosegarden per semibreve (960 per semiminima)


def pos(t: int) -> Fraction:
    """Tempo Rosegarden (unità assolute) → posizione in semibrevi."""
    return Fraction(t, WHOLE)


@dataclass
class _RawNote:
    t: int
    dur: int
    pitch: int
    velocity: int
    tie: bool
    staccato: bool


@dataclass
class _Piece:
    """Pezzo di segmento Rosegarden (tempi assoluti in unità RG), prima di diventare Segment."""

    start: int
    end: int
    clef: Clef | None
    label: str
    transpose: int
    notes: list[_RawNote] = field(default_factory=list)
    slurs: list[tuple[int, int]] = field(default_factory=list)  # (inizio, durata)
    chords: list[tuple[int, str]] = field(default_factory=list)
    controls: list[tuple[int, str, int, int]] = field(default_factory=list)  # (t, tipo, numero, valore)


def import_rosegarden(path: str | Path) -> Song:
    """Legge un file .rg e restituisce il brano."""
    root = _read_xml(path)
    comp = root.find("composition")
    if comp is None:
        raise ValueError("Rosegarden file without <composition>")

    # --- tabelle globali: metro, tempo ----------------------------------
    meters = MeterTable()
    for ts in sorted(comp.findall("timesignature"), key=lambda e: int(e.get("time", 0))):
        p = pos(int(ts.get("time", 0)))
        meters.set(meters.bar_ceil(p), TimeSignature(int(ts.get("numerator", 4)), int(ts.get("denominator", 4))))

    tempos = TempoTable(_default_tempo(comp))
    for te in comp.findall("tempo"):
        bpm = _tempo_bpm(te)
        if bpm:
            tempos.set(pos(int(te.get("time", 0))), bpm)

    instruments = _instruments(root)
    comp_end = int(comp.get("endMarker", 0))

    # --- segmenti letti per traccia RG ---------------------------------
    raw_segments: dict[str, list[ET.Element]] = {}
    for seg in root.iter("segment"):
        if seg.get("type", "internal") != "internal":  # segmenti audio: non supportati
            continue
        raw_segments.setdefault(seg.get("track", ""), []).append(seg)

    song = Song(title=Path(path).stem, tempos=tempos, meters=meters)
    keys: list[tuple[int, int, Key]] = []  # (tempo, posizione traccia, tonalità) da tutti i segmenti

    # tracce nell'ordine di Rosegarden (attributo position)
    rg_tracks = sorted(comp.findall("track"), key=lambda e: int(e.get("position", 0)))
    for rg_track in rg_tracks:
        segs = sorted(raw_segments.get(rg_track.get("id", ""), []), key=lambda s: int(s.get("start", 0)))
        if not segs:  # come l'import MIDI: niente tracce vuote
            continue
        order = int(rg_track.get("position", 0))
        channel, program, percussion = instruments.get(rg_track.get("instrument", ""), (0, 0, False))
        percussion = percussion or channel == PERCUSSION_CHANNEL
        name = rg_track.get("label", "").strip()
        if not name:  # come l'import MIDI: canale + strumento
            name = f"{channel + 1} Drums" if percussion else f"{channel + 1} {GM_PROGRAMS[program]}"

        # ogni segmento RG → pezzi (divisi ai cambi di chiave), con le eventuali ripetizioni
        pieces_by_seg: list[tuple[ET.Element, list[_Piece]]] = []
        for seg in segs:
            pieces, seg_keys = _read_segment(seg)
            keys.extend((t, order, k) for t, k in seg_keys)
            pieces_by_seg.append((seg, pieces))

        segments: list[Segment] = []
        for i, (seg, pieces) in enumerate(pieces_by_seg):
            if not pieces:
                continue
            originals = [_place(song, p, segments, name) for p in pieces]
            if seg.get("repeat") == "true":
                # ripetizioni fino al segmento successivo della traccia (o alla fine della composizione)
                start, end = pieces[0].start, pieces[-1].end
                limit = int(segs[i + 1].get("start", 0)) if i + 1 < len(segs) else comp_end
                length = end - start
                k = 1
                while length > 0 and start + k * length < limit:  # una ripetizione per giro
                    delta = k * length
                    for piece, orig in zip(pieces, originals):
                        if orig is None or piece.start + delta >= limit:
                            continue
                        _place_repeat(song, piece, orig, delta, min(piece.end + delta, limit), segments)
                    k += 1

        segments.sort(key=lambda s: song.segment_span(s)[0])
        first_clef = next((p.clef for _, ps in pieces_by_seg for p in ps if p.clef is not None), None)
        clef = first_clef or (BASS if rg_track.get("defaultClef") == "1" else TREBLE)

        song.tracks.append(Track(
            name=name,
            instrument=GMInstrument(program=0 if percussion else program, percussion=percussion),
            clef=clef,
            segments=segments,
            channel=channel + 1,
            mute=rg_track.get("muted") == "true",
            solo=rg_track.get("solo") == "true",
        ))

    song.keys = _key_table(keys, meters)
    return song


# --- lettura del file --------------------------------------------------------


def _read_xml(path: str | Path) -> ET.Element:
    """XML del file: di solito compresso con gzip, a volte in chiaro."""
    data = Path(path).read_bytes()
    if data[:2] == b"\x1f\x8b":
        data = gzip.decompress(data)
    return ET.fromstring(data)


def _default_tempo(comp: ET.Element) -> float:
    """Tempo iniziale della composizione (BPM), se non c'è un <tempo> a 0."""
    raw = comp.get("compositionDefaultTempo")
    if raw:
        return round(int(raw) / 100000, 3)
    return float(comp.get("defaultTempo", 120.0))


def _tempo_bpm(te: ET.Element) -> float | None:
    """BPM di un <tempo>: `tempo` = semiminime al minuto × 100000; i file vecchi hanno
    solo `bph` (battiti all'ora)."""
    if te.get("tempo"):
        return round(int(te.get("tempo")) / 100000, 3)
    if te.get("bph"):
        return round(int(te.get("bph")) / 60, 3)
    return None


def _instruments(root: ET.Element) -> dict[str, tuple[int, int, bool]]:
    """Strumenti MIDI dello studio: id → (canale 0-based, programma, batteria)."""
    out = {}
    for inst in root.iter("instrument"):
        if inst.get("type", "midi") != "midi":
            continue
        bank = inst.find("bank")
        program = inst.find("program")
        msb = int(bank.get("msb", 0)) if bank is not None else 0
        drums = (bank is not None and bank.get("percussion") == "true") or msb in DRUM_KIT_MSB
        out[inst.get("id", "")] = (int(inst.get("channel", 0)) % 16,
                                   min(max(int(program.get("id", 0)), 0), 127) if program is not None else 0,
                                   drums)
    return out


def _props(e: ET.Element) -> dict[str, str]:
    """Proprietà di un evento (<property>/<nproperty>): nome → valore come stringa."""
    out = {}
    for p in e:
        name = p.get("name")
        if name is None:
            continue
        for kind in ("int", "string", "bool"):
            if p.get(kind) is not None:
                out[name] = p.get(kind)
                break
    return out


def _timed_events(seg: ET.Element) -> list[tuple[int, ET.Element]]:
    """Eventi del segmento con il loro tempo assoluto (regole nel docstring del modulo)."""
    t = int(seg.get("start", 0))
    out = []

    def at(e: ET.Element, now: int) -> int:
        """Tempo dell'evento: `absoluteTime` lo fissa, `timeOffset` lo sposta."""
        if e.get("absoluteTime") is not None:
            now = int(e.get("absoluteTime"))
        if e.get("timeOffset") is not None:
            now += int(e.get("timeOffset"))
        return now

    for child in seg:
        if child.tag == "event":
            t = at(child, t)
            out.append((t, child))
            t += int(child.get("duration", 0))
        elif child.tag == "chord":
            # tutti dallo stesso tempo (salvo offset); poi avanti della durata minima > 0
            durations = []
            for e in child.iter("event"):
                t = at(e, t)
                out.append((t, e))
                durations.append(int(e.get("duration", 0)))
            t += min((d for d in durations if d > 0), default=0)
    return out


_CLEFS = {"treble": TREBLE, "bass": BASS, "alto": ALTO, "tenor": TENOR, "percussion": PERCUSSION}


def _clef(props: dict[str, str]) -> Clef:
    """Chiave Rosegarden → la nostra. Violino un'ottava sotto → violino 8vb; le altre ottave
    non le abbiamo e si ignorano (le note restano giuste); chiavi sconosciute → violino."""
    name = props.get("clef", "treble")
    if name == "treble" and props.get("octaveoffset") == "-1":
        return TREBLE_8VB
    return _CLEFS.get(name, TREBLE)


def _key(text: str) -> Key | None:
    """"B minor", "Bb major" → Key; None se non si riconosce."""
    try:
        tonic, mode = text.split()
        return Key(Spelling.parse(tonic), MINOR if mode.lower() == "minor" else MAJOR)
    except (ValueError, KeyError, IndexError):
        return None


def _read_segment(seg: ET.Element) -> tuple[list[_Piece], list[tuple[int, Key]]]:
    """Legge un segmento RG: pezzi (divisi ai cambi di chiave) e tonalità trovate."""
    start = int(seg.get("start", 0))
    events = _timed_events(seg)
    # fine: endmarker; senza, dove finisce l'ultimo evento (come Rosegarden)
    end = int(seg.get("endmarker")) if seg.get("endmarker") else max(
        (t + int(e.get("duration", 0)) for t, e in events), default=start)
    if end <= start:
        return [], []
    label = seg.get("label", "").strip()
    transpose = int(seg.get("transpose", 0) or 0)

    pieces = [_Piece(start, end, None, label, transpose)]
    keys: list[tuple[int, Key]] = []
    for t, e in events:
        kind = e.get("type")
        if t >= end:
            continue  # oltre la fine del segmento (es. pause in coda)
        props = _props(e)
        piece = pieces[-1]
        if kind == "clefchange":
            clef = _clef(props)
            if piece.clef is None or t <= piece.start:  # chiave iniziale del pezzo
                piece.clef = clef
            elif clef != piece.clef:
                # cambio di chiave a metà: nuovo pezzo da qui (decisione 2026-11-20)
                piece.end = t
                name = clef.name.replace("_", " ").title()
                pieces.append(_Piece(t, end, clef, f"{label} · Clef: {name}" if label else f"Clef: {name}",
                                     transpose))
        elif kind == "keychange":
            key = _key(props.get("key", ""))
            if key is not None:
                keys.append((t, key))
        elif kind == "note":
            dur = min(int(e.get("duration", 0)), end - t)  # accorciata alla fine del segmento
            marks = [props[k] for k in props if k.startswith("mark") and k != "marks"]
            piece.notes.append(_RawNote(t, max(dur, 1), int(props.get("pitch", 60)),
                                        int(props.get("velocity", 100)), props.get("tiedforward") == "true",
                                        "staccato" in marks))
        elif kind == "indication" and props.get("indicationtype") == "slur":
            piece.slurs.append((t, int(props.get("indicationduration", e.get("duration", 0)))))
        elif kind == "text" and props.get("type") == "Chord" and props.get("text", "").strip():
            piece.chords.append((t, props["text"].strip()))
        elif kind == "controller":
            number = int(props.get("number", 0))
            if number not in (0, 32):  # Bank Select: è il suono della traccia (come l'import MIDI)
                piece.controls.append((t, CC, number, int(props.get("value", 0))))
        elif kind == "pitchbend":
            value = (int(props.get("msb", 64)) << 7 | int(props.get("lsb", 0))) - 8192
            piece.controls.append((t, BEND, 0, value))
        elif kind == "channelpressure":
            piece.controls.append((t, PRESSURE, 0, int(props.get("pressure", 0))))
        elif kind == "keypressure":
            piece.controls.append((t, POLY_PRESSURE, int(props.get("pitch", 0)), int(props.get("pressure", 0))))
    return pieces, keys


# --- dai pezzi ai segmenti ----------------------------------------------------


def _origin(meters: MeterTable, t: int) -> tuple[int, Fraction, Fraction]:
    """Inizio allineato al movimento per il tempo RG `t`: (misura, spostamento, posizione)."""
    p = pos(max(t, 0))
    bar = meters.bar_at(p)
    beat = meters.at(bar).beat
    offset = (p - meters.bar_start(bar)) // beat * beat  # movimento precedente (o lo stesso)
    return bar, offset, meters.bar_start(bar) + offset


def _build_group(song: Song, piece: _Piece, origin: Fraction, delta: int, name: str) -> Group:
    """Gruppo con il contenuto del pezzo, posizioni relative a `origin`; `delta` sposta i
    tempi del pezzo (ripetizioni)."""

    def rel(t: int) -> Fraction:
        """Posizione nel gruppo (mai prima dell'inizio)."""
        return max(pos(t + delta) - origin, Fraction(0))

    notes = []
    for n in piece.notes:
        notes.append(Note(start=rel(n.t), duration=pos(n.dur), midi=n.pitch, velocity=n.velocity,
                          tie_next=n.tie, staccato=n.staccato))
    # legature di frase: dalla prima nota che inizia nella legatura all'ultima
    slurs = []
    for t, dur in piece.slurs:
        covered = [note for raw, note in sorted(zip(piece.notes, notes), key=lambda rn: rn[0].t)
                   if t <= raw.t < t + dur]
        if len(covered) >= 2:
            slurs.append((covered[0].id, covered[-1].id))
    end_bar = song.meters.bar_ceil(pos(piece.end + delta))
    group = Group(name=name, length_bars=max(end_bar - song.meters.bar_at(origin), 1), notes=notes, slurs=slurs,
                  chords=[(rel(t), text) for t, text in piece.chords],
                  events=[ControlEvent(rel(t), kind, number, value)
                          for t, kind, number, value in sorted(piece.controls, key=lambda c: c[0])])
    song.groups[group.id] = group
    return group


def _place(song: Song, piece: _Piece, segments: list[Segment], track_name: str) -> Segment | None:
    """Pezzo → Segment (con il suo gruppo), aggiunto a `segments`; il gruppo prende
    l'etichetta del pezzo o, se manca, il nome della traccia."""
    bar, offset, origin = _origin(song.meters, piece.start)
    length = pos(piece.end) - origin
    if length <= 0:
        return None
    group = _build_group(song, piece, origin, 0, piece.label or track_name)
    seg = Segment(group_id=group.id, start_bar=bar, length=length, offset=offset, transpose=piece.transpose,
                  label=piece.label)
    segments.append(seg)
    return seg


def _place_repeat(song: Song, piece: _Piece, original: Segment, delta: int, end: int,
                  segments: list[Segment]) -> None:
    """Ripetizione (`repeat`) del pezzo spostata di `delta`, fino a `end`. Clone dello stesso
    gruppo se l'anticipo al movimento è lo stesso dell'originale; se no (metro diverso)
    copia indipendente con le note spostate, perché il suono resti identico."""
    bar, offset, origin = _origin(song.meters, piece.start + delta)
    length = pos(end) - origin
    if length <= 0:
        return
    first_shift = pos(piece.start) - song.segment_span(original)[0]
    if pos(piece.start + delta) - origin == first_shift:
        group_id, clone_of = original.group_id, original.id
    else:
        name = song.groups[original.group_id].name
        group_id, clone_of = _build_group(song, piece, origin, delta, name).id, None
    segments.append(Segment(group_id=group_id, start_bar=bar, length=length, offset=offset,
                            transpose=piece.transpose, label=piece.label, clone_of=clone_of))


def _key_table(keys: list[tuple[int, int, Key]], meters: MeterTable) -> KeyTable:
    """Tonalità globale dai keychange di tutti i segmenti, sulla stanghetta che li contiene;
    in caso di conflitto sulla stessa misura vince la prima (nel tempo, poi per traccia)."""
    table = KeyTable()
    taken: set[int] = set()
    for t, _order, key in sorted(keys, key=lambda k: (k[0], k[1])):
        bar = meters.bar_at(pos(t))
        if bar in taken:
            continue
        taken.add(bar)
        if table.at(bar) != key or bar == 0:
            table.set(bar, key)
    return table
