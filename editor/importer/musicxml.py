"""Import di una partitura MusicXML (.musicxml/.xml in chiaro, .mxl compresso).

2026-11-23: richiesto dall'utente insieme a MuseScore: lo esportano MuseScore, Sibelius, Finale,
Dorico e altri. Come per Rosegarden si importa solo quello che il modello rappresenta; il resto si
ignora senza avvisi: ritornelli, volte, D.C./D.S. (le misure si leggono una volta, in ordine),
abbellimenti, note "cue", dinamiche, ornamenti, testo, legature di frase, layout, cambi di chiave
dopo la prima. Solo `score-partwise` (il formato che scrivono tutti); `score-timewise` si rifiuta.

Lettura:
- `<part-list>`: nome di ogni parte, canale e programma MIDI (`<midi-instrument>`, numerati da 1),
  tasto della batteria di ogni strumento (`<midi-unpitched>`, da 1).
- Ogni `<part>` è una sequenza di `<measure>`; dentro, la posizione avanza con le note
  (`<duration>` in `<divisions>` per semiminima, terzine già comprese), torna indietro con
  `<backup>`, avanza con `<forward>`. `<chord/>` = stessa posizione della nota prima.
- Pentagrammi: `<staves>` e `<staff>` delle note: una traccia per pentagramma.
- Altezza: `<step>`, `<alter>`, `<octave>` (scritta); con `<transpose>` si porta all'altezza
  reale (`<chromatic>` + 12 × `<octave-change>`) e la grafia si lascia decidere alla tonalità
  (salvo le trasposizioni di sole ottave, come il contrabbasso: lì il nome della nota non cambia).
- Batteria: `<unpitched>` → tasto dello strumento della nota (`<instrument id>`), canale 10 o
  chiave di percussione.
- Legatura di valore: `<tie type="start"/>`; staccato: `<articulations><staccato/>`.
- Tempo: `<sound tempo>`; tonalità `<key><fifths>` e `<mode>`; metro `<time>`; sigle `<harmony>`.
- Copyright: `<identification><rights>`.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
import zipfile
from fractions import Fraction
from pathlib import Path

from ..core.clef import ALTO, BASS, PERCUSSION, TENOR, TREBLE, TREBLE_8VB, Clef
from ..core.key import MAJOR, MINOR, Key
from ..core.pitch import STEP_SEMITONES, Spelling
from ..core.timeline import TimeSignature
from .score import PERCUSSION_CHANNEL, Measures, ScoreNote, ScoreStaff, build_song, chord_text

EXTENSIONS = (".musicxml", ".mxl", ".xml")

# <kind> della sigla → suffisso (si usa il testo dell'attributo `text` quando c'è)
KINDS = {"major": "", "minor": "m", "augmented": "aug", "diminished": "dim", "dominant": "7",
         "major-seventh": "maj7", "minor-seventh": "m7", "diminished-seventh": "dim7", "augmented-seventh": "7#5",
         "half-diminished": "m7b5", "major-minor": "m(maj7)", "major-sixth": "6", "minor-sixth": "m6",
         "dominant-ninth": "9", "major-ninth": "maj9", "minor-ninth": "m9", "dominant-11th": "11",
         "major-11th": "maj11", "minor-11th": "m11", "dominant-13th": "13", "major-13th": "maj13",
         "minor-13th": "m13", "suspended-second": "sus2", "suspended-fourth": "sus4", "power": "5"}


def is_musicxml(path: str | Path) -> bool:
    """Estensione MusicXML; per ".xml" (generica) guarda anche che sia una partitura."""
    p = Path(path)
    suffix = p.suffix.lower()
    if suffix in (".musicxml", ".mxl"):
        return True
    if suffix == ".xml" and p.is_file():
        head = p.read_bytes()[:2000]
        return b"<score-partwise" in head or b"<score-timewise" in head
    return False


def import_musicxml(path: str | Path):
    """Legge un file MusicXML e restituisce il brano (core.model.Song)."""
    root = _read_xml(path)
    if root.tag == "score-timewise":
        raise ValueError("MusicXML score-timewise is not supported: export the score as score-partwise")
    if root.tag != "score-partwise":
        raise ValueError("not a MusicXML score")
    copyright = (root.findtext("identification/rights") or "").strip()
    info = _part_list(root)
    parts = root.findall("part")
    if not parts:
        return build_song(Path(path).stem, [], Measures().meters, [], [], copyright)

    measures = _measure_grid(parts[0])
    tempos: dict[Fraction, float] = {}
    keys: list[tuple[int, Key]] = []
    staves: list[ScoreStaff] = []
    keys_from = next((p for p in parts if not info.get(p.get("id"), {}).get("percussion")), parts[0])
    for part in parts:
        staves += _PartReader(part, info.get(part.get("id"), {}), measures, tempos,
                              keys if part is keys_from else None).read()
    return build_song(Path(path).stem, staves, measures.meters, sorted(tempos.items()), keys, copyright)


# --- lettura del file --------------------------------------------------------


def _strip_ns(root: ET.Element) -> ET.Element:
    """Toglie gli eventuali namespace dai tag (alcuni programmi li scrivono)."""
    for e in root.iter():
        if isinstance(e.tag, str) and "}" in e.tag:
            e.tag = e.tag.split("}", 1)[1]
    return root


def _read_xml(path: str | Path) -> ET.Element:
    """XML della partitura: dal .mxl (zip, file indicato da META-INF/container.xml) o in chiaro."""
    p = Path(path)
    data = p.read_bytes()
    if data[:2] == b"PK":  # .mxl (anche se ha un'altra estensione)
        with zipfile.ZipFile(p) as z:
            names = z.namelist()
            name = None
            if "META-INF/container.xml" in names:
                container = _strip_ns(ET.fromstring(z.read("META-INF/container.xml")))
                name = next((r.get("full-path") for r in container.iter("rootfile") if r.get("full-path")), None)
            name = name or next((n for n in names if n.lower().endswith((".musicxml", ".xml"))
                                 and not n.startswith("META-INF/")), None)
            if name is None:
                raise ValueError("no MusicXML score inside the .mxl file")
            data = z.read(name)
    return _strip_ns(ET.fromstring(data))


def _int(text: str | None, default: int = 0) -> int:
    """Intero da un testo MusicXML (anche "2.0"); `default` se manca o non è un numero."""
    try:
        return int(float(text)) if text is not None and text.strip() else default
    except ValueError:
        return default


def _part_list(root: ET.Element) -> dict[str, dict]:
    """Dati di ogni parte da <part-list>: nome, canale (1–16 o None), programma (0–127),
    batteria, tasto MIDI di ogni strumento della batteria (id → 0–127)."""
    out = {}
    for sp in root.iter("score-part"):
        name = (sp.findtext("part-name") or "").strip()
        if not name or name.lower() == "none":  # alcuni programmi scrivono "None" se nascosto
            name = (sp.findtext("part-abbreviation") or "").strip()
        unpitched: dict[str, int] = {}
        channel = None
        program = 0
        for mi in sp.findall("midi-instrument"):
            if mi.findtext("midi-unpitched") is not None:
                unpitched[mi.get("id", "")] = min(max(_int(mi.findtext("midi-unpitched"), 1) - 1, 0), 127)
            if channel is None and mi.findtext("midi-channel") is not None:
                channel = min(max(_int(mi.findtext("midi-channel"), 1), 1), 16)
            if mi.findtext("midi-program") is not None and not program:
                program = min(max(_int(mi.findtext("midi-program"), 1) - 1, 0), 127)
        out[sp.get("id")] = {"name": name, "channel": channel, "program": program, "unpitched": unpitched,
                             "percussion": channel == PERCUSSION_CHANNEL or bool(unpitched)}
    return out


def _time_sig(time: ET.Element) -> TimeSignature | None:
    """<time> → metro; numeratori composti ("3+2") si sommano; None se non leggibile (es. senza metro)."""
    try:
        beats = sum(int(b) for b in (time.findtext("beats") or "").split("+"))
        return TimeSignature(beats, int(time.findtext("beat-type")))
    except (TypeError, ValueError):
        return None


def _measure_grid(part: ET.Element) -> Measures:
    """Metro di ogni misura dalla prima parte; una prima misura più corta del metro è un levare."""
    measures = Measures()
    current = TimeSignature(4, 4)
    divisions = 1
    first_len = None
    for bar, m in enumerate(part.findall("measure")):
        for attrs in m.findall("attributes"):
            divisions = _int(attrs.findtext("divisions"), divisions) or divisions
            time = attrs.find("time")
            sig = _time_sig(time) if time is not None else None
            if sig is not None:
                if sig != current or bar == 0:
                    measures.set_meter(bar, sig)
                current = sig
        if bar == 0:
            first_len = _measure_length(m, divisions)
    if first_len is not None:
        measures.set_pickup(first_len)
    return measures


def _measure_length(m: ET.Element, divisions: int) -> Fraction:
    """Durata reale di una misura: la posizione più avanti raggiunta da note, backup e forward."""
    cursor = longest = Fraction(0)
    for e in m:
        if e.tag == "attributes":
            divisions = _int(e.findtext("divisions"), divisions) or divisions
        elif e.tag == "note" and e.find("grace") is None and e.find("chord") is None:
            cursor += Fraction(_int(e.findtext("duration")), divisions * 4)
        elif e.tag == "backup":
            cursor -= Fraction(_int(e.findtext("duration")), divisions * 4)
        elif e.tag == "forward":
            cursor += Fraction(_int(e.findtext("duration")), divisions * 4)
        longest = max(longest, cursor)
    return longest


def _clef(clef: ET.Element) -> Clef | None:
    """<clef> → la nostra chiave (varianti d'ottava che non abbiamo → la chiave base)."""
    sign = (clef.findtext("sign") or "").strip()
    line = _int(clef.findtext("line"))
    octave = _int(clef.findtext("clef-octave-change"))
    if sign == "G":
        return TREBLE_8VB if octave == -1 else TREBLE
    if sign == "F":
        return BASS
    if sign == "C":
        return TENOR if line == 4 else ALTO
    if sign == "percussion":
        return PERCUSSION
    return None


class _PartReader:
    """Legge una <part>: un ScoreStaff per pentagramma."""

    def __init__(self, part: ET.Element, info: dict, measures: Measures, tempos: dict[Fraction, float],
                 keys: list[tuple[int, Key]] | None) -> None:
        self.part, self.info, self.measures, self.tempos, self.keys = part, info, measures, tempos, keys
        self.staves: dict[int, ScoreStaff] = {}
        self.clef_seen: set[int] = set()
        self.divisions = 1
        self.transpose = 0  # semitoni dalla nota scritta a quella reale
        self.last_start = Fraction(0)  # inizio della nota prima (per <chord/>)

    def _staff(self, number: int) -> ScoreStaff:
        """Pentagramma `number` (1, 2…) della parte, creato al primo uso."""
        if number not in self.staves:
            name = self.info.get("name") or f"Part {self.part.get('id', '')}"
            self.staves[number] = ScoreStaff(
                name=name if number == 1 else f"{name} ({number})", part=self.part.get("id", ""),
                program=self.info.get("program", 0), percussion=self.info.get("percussion", False),
                channel=self.info.get("channel"), clef=PERCUSSION if self.info.get("percussion") else TREBLE)
        return self.staves[number]

    def read(self) -> list[ScoreStaff]:
        self._staff(1)
        for bar, m in enumerate(self.part.findall("measure")):
            origin = self.measures.start(bar)
            cursor = Fraction(0)  # posizione dentro la misura
            for e in m:
                tag = e.tag
                if tag == "attributes":
                    self._attributes(e, bar)
                elif tag == "note":
                    cursor = self._note(e, origin, cursor)
                elif tag == "backup":
                    cursor -= self._dur(e)
                elif tag == "forward":
                    cursor += self._dur(e)
                elif tag == "harmony":
                    self._harmony(e, origin + cursor)
                elif tag in ("direction", "sound"):
                    sound = e if tag == "sound" else e.find("sound")
                    if sound is not None and sound.get("tempo"):
                        try:
                            self.tempos.setdefault(origin + cursor, float(sound.get("tempo")))
                        except ValueError:
                            pass
        return [self.staves[k] for k in sorted(self.staves)]

    def _dur(self, e: ET.Element) -> Fraction:
        """<duration> in semibrevi."""
        return Fraction(_int(e.findtext("duration")), self.divisions * 4)

    def _attributes(self, attrs: ET.Element, bar: int) -> None:
        """divisions, tonalità (solo per la parte scelta), trasposizione, numero di pentagrammi, chiavi."""
        self.divisions = _int(attrs.findtext("divisions"), self.divisions) or self.divisions
        key = attrs.find("key")
        if key is not None and self.keys is not None and key.findtext("fifths") is not None:
            mode = MINOR if (key.findtext("mode") or "").strip() == "minor" else MAJOR
            k = Key.from_fifths(max(min(_int(key.findtext("fifths")), 7), -7), mode)
            if not self.keys or self.keys[-1][1] != k:
                self.keys.append((bar, k))
        tr = attrs.find("transpose")
        if tr is not None:
            self.transpose = _int(tr.findtext("chromatic")) + 12 * _int(tr.findtext("octave-change"))
        for n in range(1, _int(attrs.findtext("staves"), 1) + 1):
            self._staff(n)
        for clef in attrs.findall("clef"):
            number = _int(clef.get("number"), 1)
            if number in self.clef_seen:  # solo la prima chiave di ogni pentagramma
                continue
            self.clef_seen.add(number)
            c = _clef(clef)
            if c is not None:
                self._staff(number).clef = c

    def _note(self, e: ET.Element, origin: Fraction, cursor: Fraction) -> Fraction:
        """Una <note>: la aggiunge al suo pentagramma; ritorna la posizione dopo la nota."""
        if e.find("grace") is not None:
            return cursor  # abbellimento: non occupa tempo, si ignora
        chord = e.find("chord") is not None
        start = self.last_start if chord else cursor
        dur = self._dur(e)
        if not chord:
            self.last_start = cursor
            cursor += dur
        if e.find("rest") is not None or e.find("cue") is not None or dur <= 0:
            return cursor
        staff = self._staff(_int(e.findtext("staff"), 1))
        midi, spelling = self._pitch(e, staff)
        if midi is None:
            return cursor
        voice = (_int(e.findtext("voice"), 1) - 1) % 4 + 1  # MuseScore esporta 5–8 per il 2° pentagramma
        tie = any(t.get("type") == "start" for t in e.findall("tie"))
        staccato = e.find("notations/articulations/staccato") is not None and not staff.percussion
        staff.notes.append(ScoreNote(origin + start, dur, midi, 80, voice, spelling, tie, staccato))
        return cursor

    def _pitch(self, e: ET.Element, staff: ScoreStaff) -> tuple[int | None, Spelling | None]:
        """Altezza reale e grafia della nota (None, None se non leggibile)."""
        pitch = e.find("pitch")
        if pitch is not None:
            step = (pitch.findtext("step") or "").strip().upper()
            if step not in STEP_SEMITONES:
                return None, None
            alter = round(float(pitch.findtext("alter") or 0))
            midi = (_int(pitch.findtext("octave"), 4) + 1) * 12 + STEP_SEMITONES[step] + alter + self.transpose
            # grafia del file; con una trasposizione che non è di ottave (clarinetto in Si♭…) la nota reale
            # ha un altro nome e la grafia la decide la tonalità (2026-11-23: le ottave, es. contrabbasso, no)
            same_name = self.transpose % 12 == 0
            spelling = Spelling(step, alter) if -2 <= alter <= 2 and same_name and not staff.percussion else None
            return (midi if 0 <= midi <= 127 else None), spelling
        unpitched = e.find("unpitched")
        if unpitched is not None:
            inst = e.find("instrument")
            key = self.info.get("unpitched", {}).get(inst.get("id", "")) if inst is not None else None
            if key is None:  # senza strumento: dalla posizione sul pentagramma (approssimata)
                step = (unpitched.findtext("display-step") or "C").strip().upper()
                key = (_int(unpitched.findtext("display-octave"), 4) + 1) * 12 + STEP_SEMITONES.get(step, 0)
            return min(max(key, 0), 127), None
        return None, None

    def _harmony(self, e: ET.Element, at: Fraction) -> None:
        """<harmony> → sigla: fondamentale, tipo (testo dell'attributo `text` o tabella KINDS), basso."""
        step = (e.findtext("root/root-step") or "").strip().upper()
        if step not in STEP_SEMITONES:
            return  # senza fondamentale (N.C., funzioni armoniche): si ignora
        try:
            root = Spelling(step, round(float(e.findtext("root/root-alter") or 0)))
            bass_step = (e.findtext("bass/bass-step") or "").strip().upper()
            bass = Spelling(bass_step, round(float(e.findtext("bass/bass-alter") or 0))) if bass_step else None
        except ValueError:
            return
        kind = e.find("kind")
        suffix = ""
        if kind is not None:
            suffix = kind.get("text") if kind.get("text") is not None else KINDS.get((kind.text or "").strip(), "")
        self._staff(_int(e.findtext("staff"), 1)).chords.append((at, chord_text(root, suffix.strip(), bass)))
