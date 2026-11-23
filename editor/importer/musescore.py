"""Import di una partitura MuseScore (.mscz compresso, .mscx in chiaro), formati 2.x, 3.x e 4.x.

2026-11-23: richiesto dall'utente, dalla versione 2 in poi (la 1.x si rifiuta con un messaggio).
Come per Rosegarden si importa solo quello che il modello rappresenta; il resto si ignora senza
avvisi: ritornelli, volte, D.C./D.S. (le misure si leggono una volta, in ordine), abbellimenti,
dinamiche, ornamenti, testo, legature di frase, layout, cambi di chiave dopo la prima.

Formato (XML; il .mscz è uno zip con il .mscx dentro):
- `<Score>` contiene le `<Part>` (strumento, nome, pentagrammi) e poi un `<Staff id>` per
  pentagramma con le `<Measure>`. Le partiture estratte (parti staccate) sono `<Score>` annidati
  o file a parte nello zip: si ignorano.
- 2.x: gli elementi stanno direttamente nella misura; la voce è in `<track>` (pentagramma × 4 +
  voce) e `<tick>` sposta la posizione (tick assoluti, `<Division>` per semiminima). Le terzine
  sono definite con `<Tuplet id>` e gli accordi le richiamano con `<Tuplet>id</Tuplet>`.
- 3.x e 4.x: la misura ha un `<voice>` per voce; `<location><fractions>` sposta la posizione;
  una terzina è un `<Tuplet>` seguito dai suoi accordi fino a `<endTuplet/>` (anche annidate).
- Note: `<pitch>` è l'altezza reale (anche negli strumenti traspositori), `<tpc>` la grafia sulla
  linea delle quinte (14 = Do). Legatura di valore: `<Tie>` (2.x) o `<Spanner type="Tie">` con
  `<next>` (3.x+). Velocity solo se `<veloType>user</veloType>` (altrimenti è uno scarto relativo).
- Tempo: `<Tempo><tempo>` in semiminime al secondo. Tonalità: `<KeySig>` con `<accidental>`
  (2.x, 3.x) o `<concertKey>` (4.x), più `<mode>`. Sigle: `<Harmony>` con `<root>` e `<base>`
  in tpc e `<name>` (il suffisso).
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
import zipfile
from fractions import Fraction
from pathlib import Path

from ..core.clef import ALTO, BASS, PERCUSSION, TENOR, TREBLE, TREBLE_8VB, Clef
from ..core.key import MAJOR, MINOR, Key
from ..core.pitch import Spelling
from ..core.timeline import TimeSignature
from .score import Measures, ScoreNote, ScoreStaff, build_song, chord_text

EXTENSIONS = (".mscz", ".mscx")

# durata scritta (durationType) in semibrevi
DURATIONS = {"long": Fraction(4), "breve": Fraction(2), "whole": Fraction(1), "half": Fraction(1, 2),
             "quarter": Fraction(1, 4), "eighth": Fraction(1, 8), "16th": Fraction(1, 16), "32nd": Fraction(1, 32),
             "64th": Fraction(1, 64), "128th": Fraction(1, 128), "256th": Fraction(1, 256),
             "512th": Fraction(1, 512), "1024th": Fraction(1, 1024)}
GRACE_TAGS = {"acciaccatura", "appoggiatura", "grace4", "grace8after", "grace16", "grace16after", "grace32",
              "grace32after"}
CLEFS = {"G": TREBLE, "G8vb": TREBLE_8VB, "F": BASS, "C3": ALTO, "C4": TENOR, "PERC": PERCUSSION, "PERC2": PERCUSSION}


def _clef(name: str | None) -> Clef | None:
    """Nome di chiave MuseScore → la nostra. Le varianti d'ottava che non abbiamo (F8vb del
    contrabbasso, G8va…) diventano la chiave base: le note restano giuste, come per Rosegarden."""
    name = (name or "").strip()
    if not name:
        return None
    if name in CLEFS:
        return CLEFS[name]
    if name.startswith("PERC"):
        return PERCUSSION
    return {"G": TREBLE, "F": BASS}.get(name[0])


def is_musescore(path: str | Path) -> bool:
    return Path(path).suffix.lower() in EXTENSIONS


def import_musescore(path: str | Path):
    """Legge un .mscz/.mscx e restituisce il brano (core.model.Song)."""
    root = _read_xml(path)
    version = root.get("version", "0")
    if _major(version) < 2:
        raise ValueError(f"MuseScore {version} files are not supported: save the score with MuseScore 2 or later")
    score = root.find("Score")
    if score is None:
        raise ValueError("MuseScore file without <Score>")
    division = int(score.findtext("Division", "480"))
    parts = _parts(score)
    staff_xml = {s.get("id"): s for s in score.findall("Staff")}
    staves = [(staff_id, info) for staff_id, info in parts if staff_id in staff_xml]
    if not staves:
        return build_song(Path(path).stem, [], Measures().meters, [], [], _meta(score, "copyright"))

    # prima passata sul primo pentagramma: metro, durata reale delle misure (levare)
    measures, lengths = _measure_grid(staff_xml[staves[0][0]])
    # tonalità dal primo pentagramma che non è batteria; tempo da tutti (di solito sta nel primo)
    key_staff = next((sid for sid, info in staves if not info["percussion"]), None)
    tempos: dict[Fraction, float] = {}
    keys: list[tuple[int, Key]] = []
    out = []
    for staff_id, info in staves:
        staff = ScoreStaff(name=info["name"], part=info["part"], program=info["program"],
                           percussion=info["percussion"], channel=info["channel"], clef=info["clef"])
        reader = _StaffReader(staff, measures, lengths, division, tempos,
                              keys if staff_id == key_staff else None)
        reader.read(staff_xml[staff_id])
        out.append(staff)
    return build_song(Path(path).stem, out, measures.meters, sorted(tempos.items()), keys, _meta(score, "copyright"))


# --- lettura del file --------------------------------------------------------


def _read_xml(path: str | Path) -> ET.Element:
    """XML della partitura: dal .mscz (zip, file indicato da META-INF/container.xml o il primo
    .mscx nella radice) o dal .mscx."""
    p = Path(path)
    if p.suffix.lower() != ".mscz":
        return ET.fromstring(p.read_bytes())
    with zipfile.ZipFile(p) as z:
        names = z.namelist()
        name = None
        if "META-INF/container.xml" in names:
            container = ET.fromstring(z.read("META-INF/container.xml"))
            name = next((r.get("full-path") for r in container.iter("rootfile")
                         if (r.get("full-path") or "").endswith(".mscx")), None)
        name = name or next((n for n in names if n.endswith(".mscx") and "/" not in n), None)
        if name is None:
            raise ValueError("no .mscx score inside the .mscz file")
        return ET.fromstring(z.read(name))


def _major(version: str) -> int:
    """Versione principale del formato ("2.06" → 2, "4.20" → 4)."""
    try:
        return int(version.split(".")[0])
    except ValueError:
        return 0


def _meta(score: ET.Element, name: str) -> str:
    """Valore di un <metaTag name="…"> della partitura ("" se manca)."""
    tag = next((m for m in score.findall("metaTag") if m.get("name") == name), None)
    return (tag.text or "").strip() if tag is not None else ""


def _parts(score: ET.Element) -> list[tuple[str, dict]]:
    """Pentagrammi nell'ordine della partitura: (id del pentagramma, dati della parte)."""
    out = []
    for i, part in enumerate(score.findall("Part"), start=1):
        inst = part.find("Instrument")
        name = (part.findtext("trackName") or "").strip()
        if not name and inst is not None:
            name = (inst.findtext("longName") or inst.findtext("trackName") or "").strip()
        name = name or f"Track {i}"
        channel_xml = inst.find("Channel") if inst is not None else None
        program = 0
        channel = None
        if channel_xml is not None:
            prog = channel_xml.find("program")
            program = min(max(int(prog.get("value", 0)), 0), 127) if prog is not None else 0
            if channel_xml.findtext("midiChannel") is not None:  # 3.x: canale 0-based salvato nel file
                channel = int(channel_xml.findtext("midiChannel")) % 16 + 1
        drums = inst is not None and "1" in (inst.findtext("useDrumset"), inst.findtext("Drumset"))
        staves = part.findall("Staff")
        for k, staff in enumerate(staves):
            percussion = drums or any(t.get("group") == "percussion" for t in staff.findall("StaffType"))
            # chiave predefinita: del pentagramma, se no dello strumento (per pentagramma: <clef staff="2">)
            inst_clefs = [c for tag in ("concertClef", "clef") for c in (inst.findall(tag) if inst is not None else [])
                          if int(c.get("staff", 1)) == k + 1]
            clef = (_clef(staff.findtext("defaultClef")) or _clef(staff.findtext("defaultConcertClef"))
                    or next((_clef(c.text) for c in inst_clefs if _clef(c.text)), None)
                    or (PERCUSSION if percussion else TREBLE))
            out.append((staff.get("id"), {
                "name": name if k == 0 else f"{name} ({k + 1})",  # pianoforte: "Piano", "Piano (2)"
                "part": str(i), "program": program, "percussion": percussion, "channel": channel, "clef": clef,
            }))
    return out


def _streams(measure: ET.Element) -> list[tuple[int, ET.Element]]:
    """Flussi di elementi della misura: 3.x+ un <voice> per voce (voce 1, 2…); 2.x la misura
    stessa (voce letta da <track> di ogni elemento, voce 0 = da decidere elemento per elemento)."""
    voices = measure.findall("voice")
    if voices:
        return [(v, voice) for v, voice in enumerate(voices, start=1)]
    return [(0, measure)]


def _duration(e: ET.Element, measure_len: Fraction) -> Fraction:
    """Durata scritta di un Chord/Rest (senza terzine): tipo + punti; "measure" = misura intera."""
    kind = (e.findtext("durationType") or "quarter").strip()
    if kind == "measure":
        d = e.find("duration")
        if d is not None and d.get("z"):  # 2.x: <duration z="4" n="4"/>
            return Fraction(int(d.get("z")), int(d.get("n")))
        if d is not None and d.text and "/" in d.text:  # 3.x+: <duration>4/4</duration>
            num, den = d.text.strip().split("/")
            return Fraction(int(num), int(den))
        return measure_len
    base = DURATIONS.get(kind, Fraction(1, 4))
    dots = int(e.findtext("dots") or 0)
    return base * (2 - Fraction(1, 2 ** dots))


def _ratio(t: ET.Element) -> Fraction:
    """Rapporto di una terzina (gruppo irregolare): normali / effettive (terzina = 2/3)."""
    actual = int(t.findtext("actualNotes") or 1)
    normal = int(t.findtext("normalNotes") or 1)
    return Fraction(normal, actual) if actual > 0 and normal > 0 else Fraction(1)


def _time_sig(e: ET.Element) -> TimeSignature | None:
    """<TimeSig> → metro (None se non leggibile)."""
    try:
        return TimeSignature(int(e.findtext("sigN")), int(e.findtext("sigD")))
    except (TypeError, ValueError):
        return None


def _key_sig(e: ET.Element) -> Key | None:
    """<KeySig> → tonalità: alterazioni in chiave (accidental 2.x/3.x, concertKey 4.x) e modo."""
    raw = e.findtext("accidental") or e.findtext("concertKey") or e.findtext("key")
    if raw is None:
        return None
    try:
        fifths = int(raw)
    except ValueError:
        return None
    mode = MINOR if (e.findtext("mode") or "").strip() == "minor" else MAJOR
    return Key.from_fifths(max(min(fifths, 7), -7), mode)


def _measure_grid(staff: ET.Element) -> tuple[Measures, list[Fraction]]:
    """Metro di ogni misura (dai TimeSig del pentagramma) e durata reale nel file (attributo
    `len`, se no la misura intera). La prima misura corta è un levare (Measures)."""
    measures = Measures()
    lengths = []
    current = TimeSignature(4, 4)
    for bar, m in enumerate(staff.findall("Measure")):
        for _v, stream in _streams(m):
            sig_xml = stream.find("TimeSig")
            sig = _time_sig(sig_xml) if sig_xml is not None else None
            if sig is not None:
                if sig != current or bar == 0:
                    measures.set_meter(bar, sig)
                current = sig
                break
        length = current.bar_length
        if m.get("len") and "/" in m.get("len"):
            num, den = m.get("len").split("/")
            length = Fraction(int(num), int(den))
        lengths.append(length)
    if lengths:
        measures.set_pickup(lengths[0])
    return measures, lengths


class _StaffReader:
    """Legge le misure di un pentagramma e riempie il suo ScoreStaff."""

    def __init__(self, staff: ScoreStaff, measures: Measures, lengths: list[Fraction], division: int,
                 tempos: dict[Fraction, float], keys: list[tuple[int, Key]] | None) -> None:
        self.staff, self.measures, self.lengths, self.division = staff, measures, lengths, division
        self.tempos, self.keys = tempos, keys
        self.clef_seen = False

    def read(self, staff_xml: ET.Element) -> None:
        file_start = Fraction(0)  # inizio della misura nel tempo del file (per i <tick> della 2.x)
        for bar, m in enumerate(staff_xml.findall("Measure")):
            # misure in più rispetto al primo pentagramma: metro in corso
            length = self.lengths[bar] if bar < len(self.lengths) else self.measures.meters.at(bar).bar_length
            for voice, stream in _streams(m):
                self._read_stream(stream, bar, voice, file_start, length)
            file_start += length

    def _read_stream(self, stream: ET.Element, bar: int, voice: int, file_start: Fraction, length: Fraction) -> None:
        """Elementi di una voce (3.x+) o della misura intera (2.x), con la posizione che avanza."""
        origin = self.measures.start(bar)  # dove comincia la misura sulla griglia del brano
        cursor = Fraction(0)  # posizione dentro la misura (tempo del file)
        tuplets_2x: dict[str, ET.Element] = {}  # 2.x: terzine per id
        stack: list[Fraction] = []  # 3.x+: rapporti delle terzine aperte (annidate)
        for e in stream:
            tag = e.tag
            if tag == "tick":  # 2.x: posizione assoluta in tick
                cursor = Fraction(int(e.text or 0), self.division * 4) - file_start
            elif tag == "location":  # 3.x+: spostamento relativo
                frac = e.findtext("fractions")
                if frac and "/" in frac:
                    num, den = frac.strip().split("/")
                    cursor += Fraction(int(num), int(den))
            elif tag == "Tuplet":
                if e.get("id") is not None:  # 2.x: definizione richiamata dagli accordi
                    tuplets_2x[e.get("id")] = e
                else:  # 3.x+: si apre qui, si chiude con <endTuplet/>
                    stack.append(_ratio(e))
            elif tag == "endTuplet":
                if stack:
                    stack.pop()
            elif tag in ("Chord", "Rest"):
                if tag == "Chord" and any(c.tag in GRACE_TAGS for c in e):
                    continue  # abbellimento: non occupa tempo, si ignora
                dur = _duration(e, length) * self._tuplet_scale(e, tuplets_2x, stack)
                if tag == "Chord":
                    v = voice or int(e.findtext("track") or 0) % 4 + 1  # 2.x: voce da <track>
                    self._chord(e, origin + cursor, dur, v)
                cursor += dur
            elif tag == "Tempo" and e.findtext("tempo"):
                self.tempos.setdefault(origin + cursor, float(e.findtext("tempo")) * 60)
            elif tag == "KeySig" and self.keys is not None:
                key = _key_sig(e)
                if key is not None and (not self.keys or self.keys[-1][1] != key):
                    self.keys.append((bar, key))
            elif tag == "Harmony":
                self._harmony(e, origin + cursor)
            elif tag == "Clef" and not self.clef_seen:
                # solo la prima chiave del pentagramma (i cambi dopo si ignorano)
                self.clef_seen = True
                clef = _clef(e.findtext("concertClefType") or e.findtext("subtype"))
                if clef is not None:
                    self.staff.clef = clef

    def _tuplet_scale(self, chord: ET.Element, tuplets_2x: dict[str, ET.Element], stack: list[Fraction]) -> Fraction:
        """Fattore delle terzine su una durata: 3.x+ tutte quelle aperte; 2.x quella richiamata
        dall'accordo e, se annidata, le terzine che la contengono."""
        scale = Fraction(1)
        for r in stack:
            scale *= r
        ref = chord.findtext("Tuplet")
        seen = set()
        while ref is not None and ref in tuplets_2x and ref not in seen:  # catena 2.x verso l'esterno
            seen.add(ref)
            scale *= _ratio(tuplets_2x[ref])
            ref = tuplets_2x[ref].findtext("Tuplet")
        return scale

    def _chord(self, chord: ET.Element, start: Fraction, dur: Fraction, voice: int) -> None:
        """Note dell'accordo: altezza, grafia (tpc), legatura di valore, velocity, staccato."""
        staccato = any("staccato" in (a.findtext("subtype") or "").lower() for a in chord.findall("Articulation"))
        for n in chord.findall("Note"):
            try:
                midi = int(n.findtext("pitch"))
            except (TypeError, ValueError):
                continue
            spelling = None
            if not self.staff.percussion and n.findtext("tpc") is not None:
                try:
                    spelling = Spelling.from_fifths(int(n.findtext("tpc")) - 14)
                    if spelling.pitch_class != midi % 12:
                        spelling = None
                except ValueError:
                    spelling = None
            tie = n.find("Tie") is not None or any(
                s.get("type") == "Tie" and s.find("next") is not None for s in n.findall("Spanner"))
            velocity = 80
            if (n.findtext("veloType") or "").strip() == "user" and n.findtext("velocity"):
                velocity = min(max(int(n.findtext("velocity")), 1), 127)
            self.staff.notes.append(ScoreNote(start, dur, min(max(midi, 0), 127), velocity, min(max(voice, 1), 4),
                                              spelling, tie, staccato and not self.staff.percussion))

    def _harmony(self, e: ET.Element, at: Fraction) -> None:
        """<Harmony> → sigla d'accordo: fondamentale e basso in tpc, suffisso da <name>."""
        root = next((r.text for r in e.iter("root") if r.text), None)
        if root is None:  # N.C. o sigla senza fondamentale: si ignora
            return
        try:
            base_tpc = next((b.text for b in e.iter("base") if b.text), None)
            bass = Spelling.from_fifths(int(base_tpc) - 14) if base_tpc is not None else None
            text = chord_text(Spelling.from_fifths(int(root) - 14), (e.findtext("name") or "").strip(), bass)
        except ValueError:
            return
        self.staff.chords.append((at, text))
