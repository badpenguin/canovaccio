"""Import di uno stile Yamaha (.sty, .prs, .sst, .fps, .bcs): un MIDI di tipo 0 con le
otto parti sui canali 9–16 e, dopo la traccia, il blocco CASM con le regole delle parti.

- Le sezioni (marker "Main A", "Fill In AA", "Intro A"…) sono attaccate: ogni sezione
  diventa un segmento per traccia (non si divide sulle misure vuote come nei .mid).
  La misura iniziale di impostazione ("SInt") si toglie: la prima sezione parte dalla misura 1.
- Nome delle tracce: canale + nome della parte dal CASM (es. "11 Bass").
- Strumenti: GM dal nome della parte (importer.gm_names), altrimenti dal Program Change.
- Velocity: per parte, se la media è sotto 100 e nessuna nota è a 127, media portata a 100
  (`normalize_velocities`); mai abbassate.
- Batteria: i canali con un kit Yamaha (Bank MSB 127, o 126 per i kit SFX) oltre al 10,
  quindi anche le percussioni del canale 9. Gli altri suoni si approssimano al GM
  (Program Change del file).
- Regole CASM: come dati in ogni gruppo (core.style.StyleRule, per sezione e parte) e
  come testo leggibile nelle note del brano.
"""

from __future__ import annotations

import struct
from functools import lru_cache
from pathlib import Path

import mido

from ..core.model import Song
from ..core.style import StyleRule, Zone
from .midi import import_midi

EXTENSIONS = (".sty", ".prs", ".sst", ".fps", ".bcs")
SETUP_MARKERS = {"SFF1", "SFF2", "SInt"}


def is_style(path: str | Path) -> bool:
    return Path(path).suffix.lower() in EXTENSIONS


def _chunks(data: bytes):
    """Blocchi (tag, contenuto) di una sequenza "TAG + lunghezza a 4 byte + dati"."""
    p = 0
    while p + 8 <= len(data):
        tag, length = data[p:p + 4], struct.unpack(">I", data[p + 4:p + 8])[0]
        yield tag, data[p + 8:p + 8 + length]
        p += 8 + length


def _casm(data: bytes) -> bytes:
    i = data.find(b"CASM")
    return data[i + 8:i + 8 + struct.unpack(">I", data[i + 4:i + 8])[0]] if i >= 0 else b""


def _rule(tag: bytes, b: bytes) -> StyleRule | None:
    """Una tabella Ctab (SFF1, una zona) o Ctb2 (SFF2, tre zone d'altezza)."""
    if len(b) < 27:
        return None
    common = dict(part=b[1:9].decode("latin-1").strip(), source_channel=b[0] + 1, dest_channel=b[9] + 1,
                  editable=bool(b[10]), note_mute=int.from_bytes(b[11:13], "big"),
                  chord_mute=int.from_bytes(b[13:18], "big"), source_root=b[18], source_type=b[19])
    if tag == b"Ctab":
        zone = Zone(0, 127, b[20], b[21], b[22], b[23], b[24], b[25])
        return StyleRule(zones=(zone,), sff2=False, **common)
    if len(b) < 40:
        return None
    mid_low, mid_high = b[20], b[21]
    blocks = [b[22 + 6 * k:28 + 6 * k] for k in range(3)]  # grave, media, acuta: NTR NTT HighKey Lo Hi RTR
    ranges = [(0, mid_low - 1), (mid_low, mid_high), (mid_high + 1, 127)]
    zones = tuple(Zone(lo, hi, *blk) for (lo, hi), blk in zip(ranges, blocks) if lo <= hi)
    return StyleRule(zones=zones, sff2=True, **common)


def casm_tables(data: bytes) -> list[tuple[list[str], dict[int, StyleRule]]]:
    """Gruppi del CASM: (sezioni a cui valgono, regole per canale d'origine 1–16)."""
    out = []
    for tag, body in _chunks(_casm(data)):
        if tag != b"CSEG":
            continue
        names: list[str] = []
        rules: dict[int, StyleRule] = {}
        for sub, chunk in _chunks(body):
            if sub == b"Sdec":
                names = [n.strip() for n in chunk.decode("latin-1").split(",") if n.strip()]
            elif sub in (b"Ctab", b"Ctb2"):
                rule = _rule(sub, chunk)
                if rule is not None:
                    rules.setdefault(rule.source_channel, rule)
        out.append((names, rules))
    return out


def describe_rules(title: str, tables: list[tuple[list[str], dict[int, StyleRule]]]) -> str:
    """Testo leggibile delle regole, per le note del brano."""
    if not tables:
        return ""
    sff2 = any(r.sff2 for _n, rules in tables for r in rules.values())
    lines = [f"Style «{title}» ({'SFF2' if sff2 else 'SFF1'}): part rules (CASM)",
             "Each part is written on a chord; on another chord its notes adapt as follows."]
    for names, rules in tables:
        lines.append("")
        lines.append("Sections: " + ", ".join(names))
        lines += ["  " + rules[ch].describe() for ch in sorted(rules)]
    return "\n".join(lines)


def part_names(data: bytes) -> dict[int, str]:
    """Nomi delle parti dal CASM (prima tabella): canale 1–16 → nome (es. "Bass")."""
    i = data.find(b"CASM")
    if i < 0:
        return {}
    casm = data[i + 8:i + 8 + struct.unpack(">I", data[i + 4:i + 8])[0]]
    names: dict[int, str] = {}
    for tag, body in _chunks(casm):
        if tag != b"CSEG":
            continue
        for sub, ctab in _chunks(body):
            if sub in (b"Ctab", b"Ctb2") and len(ctab) >= 10:
                names.setdefault(ctab[0] + 1, ctab[1:9].decode("latin-1").strip())
    return names


def sections(mid: mido.MidiFile) -> list[tuple[int, str]]:
    """Sezioni dello stile: (tick d'inizio, nome) dai marker, senza quelli d'impostazione."""
    out = []
    for track in mid.tracks:
        tick = 0
        for msg in track:
            tick += msg.time
            if msg.type == "marker" and msg.text.strip() and msg.text.strip() not in SETUP_MARKERS:
                out.append((tick, msg.text.strip()))
    return sorted(out)


def import_style(path: str | Path) -> Song:
    data = Path(path).read_bytes()
    mid = mido.MidiFile(str(path), clip=True)
    song = import_midi(mid, sections=sections(mid), channel_names=part_names(data))
    tables = casm_tables(data)
    for track in song.tracks:  # regole come dati: nel gruppo di ogni sezione della parte
        for seg in track.segments:
            for names, rules in tables:
                if seg.label in names and track.channel in rules:
                    song.groups[seg.group_id].style = rules[track.channel]
                    break
    sources = _to_destination(song, tables)
    _gm_from_part_names(song)
    _split_mega_noises(song, sources, _bank_msb(mid))
    for track in song.tracks:
        normalize_velocities([song.groups[seg.group_id] for seg in track.segments])
    drums, song.import_warnings = drum_report(song, sources, _bank_sounds(mid))
    song.notes = "\n\n".join(t for t in (drums, describe_rules(song.title, tables)) if t)
    return song


def _bank_sounds(mid: mido.MidiFile) -> dict[int, tuple]:
    """Suono di ogni canale (1–16): (Bank MSB, Bank LSB, Program 0–127), il primo di ognuno."""
    out: dict[int, list] = {}
    for track in mid.tracks:
        for msg in track:
            if msg.type == "control_change" and msg.control in (0, 32):
                slot = out.setdefault(msg.channel + 1, [None, None, None])
                i = 0 if msg.control == 0 else 1
                slot[i] = slot[i] if slot[i] is not None else msg.value
            elif msg.type == "program_change":
                slot = out.setdefault(msg.channel + 1, [None, None, None])
                slot[2] = slot[2] if slot[2] is not None else msg.program
    return {ch: tuple(v) for ch, v in out.items()}


def drum_report(song: Song, sources: dict[str, int], sounds: dict[int, tuple]) -> tuple[str, list[str]]:
    """Kit delle tracce di batteria, per le note del brano, e avvisi: i kit percussioni/SFX
    Yamaha (MSB 126) hanno su ogni tasto suoni diversi dal GM e non si convertono."""
    lines, warnings = [], []
    for track in song.tracks:
        if not track.instrument.is_percussion:
            continue
        msb, lsb, pc = sounds.get(sources.get(track.id, track.channel), (None, None, None))
        kit = f"{msb}-{lsb or 0}-{pc + 1}" if msb is not None and pc is not None else ""
        if msb == 126:
            text = (f"Yamaha percussion/SFX kit {kit} (MSB-LSB-PC): every key has a sound different from GM, "
                    f"NOT converted: it sounds wrong on a GM kit")
            warnings.append(f"{track.name}: Yamaha percussion kit {kit}, not GM")
        elif msb == 127:
            text = f"Yamaha kit {kit} (MSB-LSB-PC): keys 35–81 as in GM, extra keys converted to GM"
        else:
            text = "GM kit"
        lines.append(f"  {track.name} — {text}")
    if not lines:
        return "", warnings
    return "Drums and percussion:\n" + "\n".join(lines), warnings


def _gm_from_part_names(song: Song) -> None:
    """Strumento GM dal nome della parte ("Bass" → basso, "ClnGt" → chitarra pulita…): i
    programmi Yamaha non seguono il GM. Se il nome non dice niente resta il programma del file."""
    from ..core.instrument import GMInstrument
    from .gm_names import gm_from_name

    for track in song.tracks:
        if track.instrument.is_percussion:
            continue
        words = track.name.split(" ", 1)
        program = gm_from_name(words[1] if len(words) > 1 and words[0].isdigit() else track.name)
        if program is not None:
            track.instrument = GMInstrument(program)


TARGET_VELOCITY = 100


def normalize_velocities(groups: list) -> None:
    """Velocity di una parte (tutte le sue sezioni insieme): se nessuna nota è a 127 e la
    media è sotto 100, tutte si moltiplicano per lo stesso fattore perché la media arrivi a
    100, senza che la più forte superi 127. Mai abbassate. Gli stili Yamaha hanno velocity basse (la tastiera alza il volume del
    canale): così suonano a un livello normale e fade e accenti restano in proporzione."""
    unique = {id(g): g for g in groups}.values()  # un gruppo una volta sola (i cloni lo condividono)
    notes = [n for g in unique for n in g.notes]
    if not notes:
        return
    top = max(n.velocity for n in notes)
    average = sum(n.velocity for n in notes) / len(notes)
    if top >= 127 or average >= TARGET_VELOCITY:  # già a 127, o già forte: non si tocca (mai abbassare)
        return
    factor = min(TARGET_VELOCITY / average, 127 / top)
    for n in notes:
        n.velocity = max(1, min(127, round(n.velocity * factor)))


MEGA_VOICE_MSB = 8
MEGA_NOISE = 96  # Mega Voice: dal Do6 (96) in su ci sono i rumori dello strumento
# suffisso della traccia dei rumori (2026-11-20: in inglese, prima era "rumori"); le ricerche
# per nome accettano entrambi, per i .stile salvati con il nome italiano
NOISE_SUFFIX = "noises"
NOISE_SUFFIXES = ("· noises", "· rumori")


def _bank_msb(mid: mido.MidiFile) -> dict[int, int]:
    """Primo Bank Select MSB di ogni canale (1–16)."""
    out: dict[int, int] = {}
    for track in mid.tracks:
        for msg in track:
            if msg.type == "control_change" and msg.control == 0:
                out.setdefault(msg.channel + 1, msg.value)
    return out


def _split_mega_noises(song: Song, sources: dict[str, int], msb: dict[int, int]) -> None:
    """Mega Voice (Bank MSB 8): sui tasti alti ci sono i rumori (pennate, sfregamenti, colpi
    sul corpo), che con un suono GM diventerebbero note altissime. Vanno in una traccia a
    parte subito sotto, "… · rumori", in Mute: non si perdono (per la tastiera vera)."""
    from ..core.clef import TREBLE
    from ..core.model import Group, Segment, Track

    out = []
    for track in song.tracks:
        out.append(track)
        if track.instrument.is_percussion or msb.get(sources.get(track.id, track.channel)) != MEGA_VOICE_MSB:
            continue
        segments = []
        for seg in track.segments:
            g = song.groups[seg.group_id]
            high = [n for n in g.notes if n.midi >= MEGA_NOISE]
            if not high:
                continue
            g.notes = [n for n in g.notes if n.midi < MEGA_NOISE]
            kept = {n.id for n in g.notes}
            g.slurs = [s for s in g.slurs if s[0] in kept and s[1] in kept]
            # 2026-11-20: nome in inglese (UI in inglese); i .stile salvati prima hanno "· rumori"
            noise = Group(f"{g.name} · {NOISE_SUFFIX}", g.length_bars, high, style=g.style)
            song.groups[noise.id] = noise
            segments.append(Segment(noise.id, seg.start_bar, seg.length, seg.transpose, seg.offset, seg.trim,
                                    seg.label))
        if segments:
            out.append(Track(f"{track.name} · {NOISE_SUFFIX}", track.instrument, TREBLE, segments,
                             channel=track.channel, mute=True))
    song.tracks[:] = out


def _to_destination(song: Song, tables: list[tuple[list[str], dict[int, StyleRule]]]) -> dict[str, int]:
    """Le parti sono registrate su canali "di lavoro" (nei Pro style anche 1–8) e suonano sul
    canale di destinazione del CASM (9–16): la traccia prende quello. Più parti sulla stessa
    destinazione (varianti per tipo d'accordo, o kit + piatti) restano tracce distinte: a
    nomi uguali si aggiunge "(2)", "(3)"…; il canale 10 è sempre batteria.
    Ritorna id della traccia → canale di lavoro (quello del file)."""
    from ..core.instrument import GMInstrument

    seen: dict[str, int] = {}
    sources = {t.id: t.channel for t in song.tracks}
    for track in song.tracks:
        rule = next((rules[track.channel] for _n, rules in tables if track.channel in rules), None)
        if rule is None:
            continue
        track.channel = rule.dest_channel
        if track.channel == 10 and not track.instrument.is_percussion:
            track.instrument = GMInstrument(0, percussion=True)
        name = f"{rule.dest_channel} {rule.part}"
        seen[name] = seen.get(name, 0) + 1
        track.name = name if seen[name] == 1 else f"{name} ({seen[name]})"
    return sources


STYLE_FILE = ".stile"  # stile importato e personalizzato (core.project.STYLE_EXTENSION)


def style_sections(path: str | Path) -> list[tuple[str, int]]:
    """Sezioni di uno stile (.sty… o .stile) con la loro durata in misure: [("Main A", 4),
    ("Intro A", 2)…]. Lette dal file (con una piccola cache per file e data di modifica)."""
    p = Path(path)
    return list(_style_sections(str(p), p.stat().st_mtime))


@lru_cache(maxsize=32)
def _style_sections(path: str, _mtime: float) -> tuple[tuple[str, int], ...]:
    if path.lower().endswith(STYLE_FILE):  # .stile: dalle etichette dei segmenti
        from ..core.project import load_project

        song = load_project(path)
        found: dict[str, tuple] = {}
        for track in song.tracks:
            for seg in track.segments:
                start, _end = song.segment_span(seg)
                bars = round(seg.length / song.meters.at(song.meters.bar_at(start)).bar_length)
                if seg.label and (seg.label not in found or start < found[seg.label][0]):
                    found[seg.label] = (start, max(bars, 1))
        return tuple((name, bars) for name, (_s, bars) in sorted(found.items(), key=lambda kv: kv[1][0]))
    mid = mido.MidiFile(path, clip=True)
    marks = sections(mid)
    end = max(sum(m.time for m in tr) for tr in mid.tracks)
    num, den = 4, 4
    for msg in mid.tracks[0]:
        if msg.type == "time_signature":
            num, den = msg.numerator, msg.denominator
            break
    bar = mid.ticks_per_beat * 4 * num // den
    out = []
    for i, (tick, name) in enumerate(marks):
        stop = marks[i + 1][0] if i + 1 < len(marks) else end
        out.append((name, max(round((stop - tick) / bar), 1)))
    return tuple(out)


def load_style_data(path: str | Path):
    """Lo stile come pattern per sezione e parte (core.arrange.StyleData), per generare.
    Da un .sty… (importato) o da un .stile (con le personalizzazioni: note, strumento e
    suoni per uscita di ogni parte). Cache per file e data di modifica. I rumori delle
    Mega Voice no."""
    p = Path(path)
    return _load_style_data(str(p), p.stat().st_mtime)


@lru_cache(maxsize=8)
def _load_style_data(path: str, _mtime: float):
    from ..core.arrange import Part, Pattern, StyleData

    if path.lower().endswith(STYLE_FILE):
        from ..core.project import load_project

        song = load_project(path)
    else:
        song = import_style(path)
    parts, sections = [], {}
    for track in song.tracks:
        if track.mute and track.name.endswith(NOISE_SUFFIXES):  # anche "· rumori" dei .stile salvati prima
            continue
        part = Part(track.name, track.channel, track.instrument.is_percussion,
                    getattr(track.instrument, "program", 0), instrument=track.instrument,
                    sounds=dict(track.sounds))
        placed = list(song.placed_notes(track))
        for seg in track.segments:
            start, _end = song.segment_span(seg)
            notes = [p.note.copy(start=p.start - start, midi=p.midi) for p in placed if p.segment is seg]
            part.patterns[seg.label] = Pattern(seg.length, notes, song.groups[seg.group_id].style)
            sections[seg.label] = max(sections.get(seg.label, seg.length), seg.length)
        parts.append(part)
    return StyleData(path, parts, sections)
