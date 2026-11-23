"""Parte comune degli import da programmi di notazione (MuseScore, MusicXML).

2026-11-23: i due formati descrivono una partitura per pentagrammi e misure; ognuno ha il suo
lettore (importer.musescore, importer.musicxml), che produce i pentagrammi in questa forma
neutra (`ScoreStaff` con `ScoreNote` in posizioni assolute). Qui si costruisce il brano, una
volta sola per entrambi.

Decisione dell'utente (2026-11-23): come per Rosegarden si importa solo quello che il modello
sa rappresentare; il resto si ignora senza avvisi.

Mappatura verso il modello:
- ogni pentagramma → una Track (un pianoforte su due pentagrammi → due tracce);
- le note si dividono in segmenti sulle misure vuote, come nell'import MIDI;
- le sigle d'accordo vanno nel segmento che le contiene (quelle fuori dai segmenti si perdono);
- canale: quello del file se c'è; se no batteria sul 10 e gli altri in ordine (1, 2, …, saltando il 10),
  uno per parte (i pentagrammi della stessa parte condividono il canale).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from fractions import Fraction

from ..core.clef import TREBLE, Clef
from ..core.instrument import GMInstrument
from ..core.key import Key
from ..core.model import Group, Note, Segment, Song, Track
from ..core.pitch import Spelling
from ..core.timeline import KeyTable, MeterTable, TempoTable, TimeSignature

PERCUSSION_CHANNEL = 10  # 1-based


@dataclass
class ScoreNote:
    """Nota letta da una partitura: posizione assoluta e durata in semibrevi."""

    start: Fraction
    duration: Fraction
    midi: int
    velocity: int = 80
    voice: int = 1
    spelling: Spelling | None = None
    tie: bool = False
    staccato: bool = False


@dataclass
class ScoreStaff:
    """Un pentagramma della partitura, pronto a diventare una traccia."""

    name: str
    part: str  # identificativo della parte (pentagrammi della stessa parte: stesso canale)
    program: int = 0  # 0–127
    percussion: bool = False
    channel: int | None = None  # 1–16 se il file lo dice
    clef: Clef = TREBLE
    notes: list[ScoreNote] = field(default_factory=list)
    chords: list[tuple[Fraction, str]] = field(default_factory=list)  # (posizione assoluta, sigla)


def chord_text(root: Spelling, suffix: str, bass: Spelling | None = None) -> str:
    """Sigla in testo semplice ("F#m7/C#"): alterazioni come # e b, come le scrive l'utente."""

    def name(s: Spelling) -> str:
        """Nome della nota con # e b (non i simboli tipografici)."""
        return s.step + ("#" * s.alter if s.alter > 0 else "b" * -s.alter)

    return name(root) + suffix + (f"/{name(bass)}" if bass is not None else "")


def build_song(title: str, staves: list[ScoreStaff], meters: MeterTable, tempos: list[tuple[Fraction, float]],
               keys: list[tuple[int, Key]], copyright: str = "") -> Song:
    """Brano dai pentagrammi letti: tabelle globali, una traccia per pentagramma con note,
    segmenti divisi sulle misure vuote. `tempos`: (posizione, BPM); `keys`: (misura, tonalità)."""
    tempo_table = TempoTable()
    for i, (at, bpm) in enumerate(sorted(tempos)):
        # il primo tempo vale dall'inizio: con un levare l'indicazione sta sulla sua prima nota,
        # non a 0, e prima di lei suonerebbe il tempo predefinito
        tempo_table.set(at if i else Fraction(0), round(bpm, 3))
    key_table = KeyTable()
    for bar, key in sorted(keys, key=lambda k: k[0]):
        key_table.set(bar, key)
    song = Song(title=title, tempos=tempo_table, meters=meters, keys=key_table, copyright=copyright)

    channels = _channels(staves)
    for staff in staves:
        if not staff.notes:  # come l'import MIDI e Rosegarden: niente tracce vuote
            continue
        runs = _runs(staff.notes, meters)
        segments = []
        for k, (first_bar, end_bar, notes) in enumerate(runs, start=1):
            origin = meters.bar_start(first_bar)
            length = meters.bar_start(end_bar) - origin
            group = Group(
                name=staff.name if len(runs) == 1 else f"{staff.name} · {k}",
                length_bars=end_bar - first_bar,
                notes=[Note(start=n.start - origin, duration=n.duration, midi=n.midi, velocity=n.velocity,
                            voice=n.voice, spelling=n.spelling, tie_next=n.tie, staccato=n.staccato)
                       for n in notes],
                chords=[(at - origin, text) for at, text in staff.chords if origin <= at < origin + length],
            )
            song.groups[group.id] = group
            segments.append(Segment(group_id=group.id, start_bar=first_bar, length=length))
        song.tracks.append(Track(
            name=staff.name,
            instrument=GMInstrument(program=0 if staff.percussion else staff.program, percussion=staff.percussion),
            clef=staff.clef,
            segments=segments,
            channel=channels[id(staff)],
        ))
    return song


def _channels(staves: list[ScoreStaff]) -> dict[int, int]:
    """Canale (1–16) di ogni pentagramma: quello del file, se no batteria sul 10 e le altre
    parti in ordine saltando il 10 (oltre 16 si ricomincia)."""
    out: dict[int, int] = {}
    by_part: dict[str, int] = {}
    free = [c for c in range(1, 17) if c != PERCUSSION_CHANNEL]
    used = 0
    for staff in staves:
        if staff.channel is not None:
            out[id(staff)] = staff.channel
        elif staff.percussion:
            out[id(staff)] = PERCUSSION_CHANNEL
        else:
            if staff.part not in by_part:  # una parte nuova prende il canale libero successivo
                by_part[staff.part] = free[used % len(free)]
                used += 1
            out[id(staff)] = by_part[staff.part]
    return out


def _runs(notes: list[ScoreNote], meters: MeterTable) -> list[tuple[int, int, list[ScoreNote]]]:
    """Blocchi di misure consecutive con note: [(prima misura, misura dopo l'ultima, note)].
    Una misura è occupata se vi suona una nota (anche tenuta dalla misura prima)."""
    runs: list[list] = []
    for n in sorted(notes, key=lambda n: (n.start, n.midi)):
        first = meters.bar_at(n.start)
        end = max(meters.bar_ceil(n.start + n.duration), first + 1)
        if runs and first <= runs[-1][1]:  # attaccata al blocco precedente (nessuna misura vuota in mezzo)
            runs[-1][1] = max(runs[-1][1], end)
            runs[-1][2].append(n)
        else:
            runs.append([first, end, [n]])
    return [tuple(r) for r in runs]


class Measures:
    """Posizione delle misure lette dal file sulla griglia del brano.

    Ogni misura del file occupa una misura intera del metro in corso. Una prima misura più corta
    (levare) si allinea a destra, così la musica arriva sulla misura 2 al punto giusto; le altre
    misure corte si allineano a sinistra (il resto resta vuoto)."""

    def __init__(self) -> None:
        self.meters = MeterTable()
        self._pickup = Fraction(0)

    def set_meter(self, bar: int, sig: TimeSignature) -> None:
        """Cambio di metro alla misura `bar` (0-based)."""
        self.meters.set(bar, sig)

    def set_pickup(self, actual: Fraction) -> None:
        """Durata reale della prima misura: se è più corta del metro, le note si spostano a destra."""
        full = self.meters.at(0).bar_length
        self._pickup = full - actual if 0 < actual < full else Fraction(0)

    def start(self, bar: int) -> Fraction:
        """Posizione assoluta dove comincia il contenuto della misura `bar` del file."""
        return self.meters.bar_start(bar) + (self._pickup if bar == 0 else 0)
