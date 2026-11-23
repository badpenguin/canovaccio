"""Modello del brano: unica fonte di verità.

Song ─ tabelle globali (tempo, metro, tonalità)
     ─ tracks[]  ─ segments[] (posizione in misure intere) ─▶ Group (note)
     ─ groups{}
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field, replace
from fractions import Fraction
from typing import Iterator

from .clef import TREBLE, Clef
from .instrument import GMInstrument, Instrument
from .key import DEFAULT_SPELLER, Speller
from .pitch import Pitch, Spelling
from .timeline import KeyTable, MeterTable, TempoTable


def new_id() -> str:
    return uuid.uuid4().hex[:10]


@dataclass
class Note:
    start: Fraction  # relativo all'inizio del segmento
    duration: Fraction
    midi: int  # il suono
    velocity: int = 80
    voice: int = 1
    spelling: Spelling | None = None  # grafia forzata a mano; None = dalla tonalità
    tie_next: bool = False  # legata alla nota successiva (stessa altezza)
    staccato: bool = False  # articolazione: la durata scritta resta, al play suona la metà
    strum: str | None = None  # pennata dell'accordo: "down" (dal grave) / "up" (dall'acuto); al play le note sfasate
    id: str = field(default_factory=new_id)

    @property
    def end(self) -> Fraction:
        return self.start + self.duration

    def copy(self, **changes) -> "Note":
        """Copia con un id nuovo (e i campi cambiati)."""
        return replace(self, id=new_id(), **changes)


CC, BEND, PRESSURE, POLY_PRESSURE = "cc", "bend", "pressure", "poly"  # tipi di evento di controllo


@dataclass(frozen=True)
class ControlEvent:
    """Evento di controllo nel gruppo (si vede solo nell'Event Editor): controller (CC,
    `number` 0–127, `value` 0–127), pitch bend (`value` -8192…8191), aftertouch di canale
    (`value`) o polifonico (`number` = tasto, `value`). Del file MIDI si tiene solo ciò che
    riguarda il suono: note, controller (non il Bank Select), pitch bend, aftertouch; niente
    sysex, Program Change, Bank Select (il suono della traccia) né meta-eventi testuali."""

    start: Fraction  # come Note.start
    kind: str
    number: int
    value: int
    id: str = field(default_factory=new_id)


@dataclass
class Group:
    """Contenuto riutilizzabile: più segmenti possono puntare allo stesso gruppo."""

    name: str
    length_bars: int
    notes: list[Note] = field(default_factory=list)
    id: str = field(default_factory=new_id)
    # legature di frase (slur): (id della prima nota, id dell'ultima); coprono le note che
    # iniziano fra le due. Stanno nel gruppo: i cloni le condividono come le note.
    slurs: list[tuple[str, str]] = field(default_factory=list)
    # sigle d'accordo: (posizione come Note.start, testo), in ordine; solo dove l'accordo cambia
    chords: list[tuple[Fraction, str]] = field(default_factory=list)
    # regole dello stile Yamaha da cui viene il gruppo (core.style.StyleRule), o None
    style: object | None = None
    # traccia accordi: eventi accordo (core.chord_track.ChordEvent) invece di note
    chord_events: list = field(default_factory=list)
    # controller, pitch bend, aftertouch (ControlEvent), in ordine di posizione
    events: list = field(default_factory=list)

    def copy(self, name: str | None = None) -> "Group":
        """Copia indipendente: note con id nuovi, slur ricollegati alle copie, sigle e regole copiate."""
        id_map = {n.id: n.copy() for n in self.notes}
        return Group(name or self.name, self.length_bars, list(id_map.values()),
                     slurs=remap_slurs(self.slurs, id_map), chords=list(self.chords), style=self.style,
                     chord_events=list(self.chord_events), events=[replace(e, id=new_id()) for e in self.events])


def remap_slurs(slurs: list[tuple[str, str]], id_map: dict) -> list[tuple[str, str]]:
    """Slur ricollegati a note copiate (`id_map`: id vecchio → nota nuova); si perdono
    quelli con un estremo che non è stato copiato."""
    return [(id_map[a].id, id_map[b].id) for a, b in slurs if a in id_map and b in id_map]


@dataclass
class Segment:
    """Istanza di un gruppo su una traccia.

    Inizio = misura + spostamento sul movimento; durata esatta in semibrevi
    (all'import è un numero intero di misure; Split e Move lavorano sulle battute)."""

    group_id: str
    start_bar: int
    length: Fraction  # durata in semibrevi
    transpose: int = 0  # semitoni
    offset: Fraction = Fraction(0)  # spostamento dall'inizio della misura (multiplo del movimento)
    trim: Fraction = Fraction(0)  # da che punto del gruppo parte il contenuto mostrato (Resize dell'inizio)
    label: str = ""  # etichetta mostrata sul segmento (Segment > Label)
    clone_of: str | None = None  # id del segmento originale se è una copia collegata (clone)
    id: str = field(default_factory=new_id)
    # traccia accordi: stile (percorso del file), sezione e fill finale da generare
    style: str = ""
    section: str = ""
    fill: str = ""

    def arrangement(self) -> dict:
        """Stile/sezione/fill, da ricopiare nei segmenti nati da questo (copia, split…)."""
        return {"style": self.style, "section": self.section, "fill": self.fill}


@dataclass(frozen=True)
class Sound:
    """Suono di una traccia su una certa uscita, al posto dello strumento GM.
    Interno: `soundfont` (file; "" = quello dell'uscita), `bank` (banco FluidSynth,
    128 = kit di batteria) e `program`. Porta MIDI: `bank` = Bank Select MSB, `lsb` =
    Bank Select LSB, `program` (0–127)."""

    program: int = 0
    bank: int = 0
    lsb: int = 0
    soundfont: str = ""


@dataclass
class Track:
    name: str
    instrument: Instrument = field(default_factory=GMInstrument)
    clef: Clef = TREBLE
    segments: list[Segment] = field(default_factory=list)
    output: str = "default"  # destinazione d'uscita (MVP: una sola)
    channel: int = 1  # canale MIDI 1–16 usato dal playback (10 = batteria)
    velocity: int = 100  # velocità di default 1–127 (per le note nuove)
    mute: bool = False  # interruttori d'ascolto (non entrano in Ctrl+Z)
    solo: bool = False
    id: str = field(default_factory=new_id)
    sounds: dict[str, Sound] = field(default_factory=dict)  # nome dell'uscita → suono al posto del GM
    kind: str = "notes"  # "chords" = traccia accordi (core.chord_track): una per brano, suona solo in Preview
    styles: list[str] = field(default_factory=list)  # traccia accordi: stili del brano (percorsi dei file)


@dataclass(frozen=True)
class PlacedNote:
    """Nota in posizione assoluta nel brano (dopo segmento e trasposizione)."""

    note: Note
    segment: Segment
    start: Fraction
    duration: Fraction
    midi: int

    @property
    def end(self) -> Fraction:
        return self.start + self.duration


@dataclass
class Song:
    title: str = "Untitled"
    notes: str = ""  # note del brano, testo libero (File > Song Notes…)
    # 2026-11-23: avviso di copyright (meta-evento MIDI "Copyright Notice"): letto all'import,
    # salvato nel .music, scritto nell'export MIDI, modificabile in Song Properties
    copyright: str = ""
    import_warnings: list[str] = field(default_factory=list, repr=False)  # avvisi dell'import (non si salvano)
    tempos: TempoTable = field(default_factory=TempoTable)
    meters: MeterTable = field(default_factory=MeterTable)
    keys: KeyTable = field(default_factory=KeyTable)
    tracks: list[Track] = field(default_factory=list)
    groups: dict[str, Group] = field(default_factory=dict)
    speller: Speller = field(default=DEFAULT_SPELLER, repr=False)

    # --- interrogazioni -------------------------------------------------

    @property
    def chord_track(self) -> Track | None:
        """La traccia accordi (una sola per brano), se c'è."""
        return next((t for t in self.tracks if t.kind == "chords"), None)

    def track(self, track_id: str) -> Track:
        return next(t for t in self.tracks if t.id == track_id)

    @property
    def bar_count(self) -> int:
        ends = [self.segment_bars(s)[1] for t in self.tracks for s in t.segments]
        return max(ends, default=1)

    @property
    def end_position(self) -> Fraction:
        return self.meters.bar_start(self.bar_count)

    @property
    def duration_seconds(self) -> float:
        return self.tempos.seconds(self.end_position)

    def segment_by_id(self, seg_id: str) -> Segment | None:
        return next((s for t in self.tracks for s in t.segments if s.id == seg_id), None)

    def group_number(self, group_id: str) -> int:
        """Numero breve (1, 2, 3…) del gruppo, per indicarlo come #ID."""
        ids = list(self.groups)
        return ids.index(group_id) + 1 if group_id in ids else 0

    def silent_track_ids(self) -> set[str]:
        """Tracce da non suonare. Se c'è almeno un Solo suonano solo le tracce in Solo
        (anche se in Mute: Solo vince sempre); altrimenti tacciono quelle in Mute.
        La traccia accordi suona solo in Preview (il suo Solo); se no tace sempre."""
        chords = {t.id for t in self.tracks if t.kind == "chords" and not t.solo}
        if any(t.solo for t in self.tracks):
            return {t.id for t in self.tracks if not t.solo}
        return {t.id for t in self.tracks if t.mute} | chords

    def display_label(self, seg: Segment) -> str:
        """Testo mostrato sul segmento. Un clone (copia collegata) mostra l'etichetta
        dell'originale + " (CLONE)", oppure "#ID (CLONE)" se l'originale non ha etichetta.
        Traccia accordi: dopo l'etichetta la sezione dello stile e il fill ("· Main A + Fill In AA")."""
        if seg.clone_of is None:
            text = seg.label
        else:
            original = self.segment_by_id(seg.clone_of)
            base = original.label if original is not None and original.label else f"#{self.group_number(seg.group_id)}"
            text = f"{base} (CLONE)"
        if seg.section:
            text += f" · {seg.section}" + (f" + {seg.fill}" if seg.fill else "")
        return text

    def segment_span(self, seg: Segment) -> tuple[Fraction, Fraction]:
        """Inizio e fine del segmento nel brano (semibrevi)."""
        start = self.meters.bar_start(seg.start_bar) + seg.offset
        return start, start + seg.length

    def segment_bars(self, seg: Segment) -> tuple[int, int]:
        """Prima misura toccata dal segmento e misura dopo l'ultima toccata."""
        start, end = self.segment_span(seg)
        return self.meters.bar_at(start), self.meters.bar_ceil(end)

    def split_position(self, pos: Fraction) -> tuple[int, Fraction]:
        """Posizione → (misura, spostamento dall'inizio della misura)."""
        bar = self.meters.bar_at(pos)
        return bar, pos - self.meters.bar_start(bar)

    def snap_to_beat(self, pos: Fraction) -> Fraction:
        """Movimento (battuta) più vicino alla posizione."""
        pos = max(pos, Fraction(0))
        bar, off = self.split_position(pos)
        beat = self.meters.at(bar).beat
        return self.meters.bar_start(bar) + (off / beat + Fraction(1, 2)).__floor__() * beat  # metà: verso l'alto

    def placed_notes(self, track: Track) -> Iterator[PlacedNote]:
        """Note della traccia in posizione assoluta, ordinate per inizio."""
        out = []
        for seg in track.segments:
            group = self.groups[seg.group_id]
            origin, _limit = self.segment_span(seg)
            for n in group.notes:
                rel = n.start - seg.trim  # posizione dentro il segmento
                if rel < 0 or rel >= seg.length:
                    continue  # fuori dalla parte mostrata (segmento accorciato)
                # 2026-11-20: la coda oltre la fine del segmento (fine accorciata col Resize) è
                # nascosta come il resto: prima suonava e si vedeva sopra il segmento successivo.
                # La nota nel gruppo resta intera (riallungando il segmento torna).
                duration = min(n.duration, seg.length - rel)
                out.append(PlacedNote(n, seg, origin + rel, duration, n.midi + seg.transpose))
        out.sort(key=lambda p: (p.start, p.midi))
        return iter(out)

    def placed_events(self, track: Track) -> list[tuple[Fraction, ControlEvent]]:
        """Eventi di controllo della traccia in posizione assoluta, in ordine."""
        out = []
        for seg in track.segments:
            origin, _limit = self.segment_span(seg)
            for e in self.groups[seg.group_id].events:
                rel = e.start - seg.trim
                if 0 <= rel < seg.length:
                    out.append((origin + rel, e))
        out.sort(key=lambda pe: pe[0])
        return out

    def spell(self, placed: PlacedNote) -> Pitch:
        """Grafia della nota: forzata se presente e coerente, altrimenti dalla tonalità attiva."""
        forced = placed.note.spelling
        if forced is not None and forced.pitch_class == placed.midi % 12:
            return Pitch.from_midi(placed.midi, forced)
        key = self.keys.at(self.meters.bar_at(placed.start))
        return self.speller.spell(placed.midi, key)
