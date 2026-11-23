"""Impaginazione in notazione (sola lettura, una voce per rigo, niente travature).

Una o più tracce (un rigo ciascuna, colonne allineate fra i righi), su un
intervallo di misure; su un solo rigo continuo (width=None) o a capo.

Ingresso: modello. Uscita: primitive grafiche in spazi del rigo (y verso il basso),
che la UI disegna con Cairo. Nessuna dipendenza da GTK: testabile.

Arrotondamento solo a video: le note che stanno già su una griglia scrivibile
fine (multipli di 1/64 o 1/48) restano esatte; le altre vengono agganciate alla
griglia più vicina fra 1/32 e 1/24 e disegnate in rosso.
"""

from __future__ import annotations

from bisect import bisect_right
from dataclasses import dataclass, field
from fractions import Fraction as F

from ..core.clef import Clef
from ..core.model import Song, Track
from ..core.pitch import Pitch
from .smufl import ACCIDENTAL_GLYPH, metadata

FINE_GRIDS = (F(1, 64), F(1, 48))
COARSE_GRIDS = (F(1, 32), F(1, 24))
STAFF_HEIGHT = 4.0
# Righello di misure/movimenti sopra ogni sistema (lo disegna la UI): in spazi.
# Fra righello e rigo c'è posto almeno per l'indicazione di tempo senza toccare la chiave;
# di più se le note salgono sopra il rigo. Gli spazi fra righi seguono le note reali.
RULER_BOTTOM = 5.0  # distanza minima del bordo inferiore del righello dalla quinta linea
RULER_HEIGHT = 2.2
MIN_EXTENT = 2.5  # spazio minimo sopra/sotto ogni rigo (gambi, terzine, legature)
EXTENT_PAD = 1.0  # aria fra ingombri di righi vicini
TEMPO_BASELINE = 2.2  # sopra la quinta linea: fra righello e chiave di violino
STEM_LENGTH = 3.5
LABEL_W = 9.0  # colonna dei nomi traccia quando i righi sono più di uno
BAR_PAD = 1.2
MIN_BAR_PER_WHOLE = 20.8  # spazi: larghezza minima di una misura per semibreve (4/4 → 20.8 spazi)

# --- primitive ------------------------------------------------------------


@dataclass
class Glyph:
    name: str
    x: float
    y: float
    red: bool = False
    scale: float = 1.0
    staff: int = 0  # rigo di appartenenza (la UI può spostare i righi in verticale)
    keys: tuple = ()  # teste: note rappresentate, ((id segmento, id nota), …) per selezione e click


@dataclass
class Line:
    x1: float
    y1: float
    x2: float
    y2: float
    thickness: float
    red: bool = False
    staff: int = 0
    staff2: int | None = None  # per linee fra due righi (stanghette): y2 segue questo rigo


@dataclass
class Tie:
    x1: float
    x2: float
    y: float
    below: bool
    red: bool = False
    staff: int = 0


@dataclass
class Slur:
    """Legatura di frase: arco da (x1, y1) a (x2, y2), alto `height` spazi (verso l'esterno)."""

    x1: float
    y1: float
    x2: float
    y2: float
    below: bool
    height: float = 1.0
    red: bool = False
    staff: int = 0


@dataclass
class Text:
    text: str
    x: float
    y: float
    size: float = 1.2  # in spazi
    staff: int = 0


Item = Glyph | Line | Tie | Text


@dataclass
class System:
    top: float  # y della quinta linea del primo rigo
    first_bar: int
    last_bar: int  # esclusa
    staff_tops: list[float] = field(default_factory=list)
    bottom: float = 0.0  # y della prima linea dell'ultimo rigo
    ruler_top: float = 0.0  # y del bordo superiore del righello
    staff_boxes: list[tuple[float, float]] = field(default_factory=list)  # ingombro (alto, basso) di ogni rigo
    x_map: list[tuple[F, float]] = field(default_factory=list)  # posizione → x (cursore)
    bars: list[tuple[int, float]] = field(default_factory=list)  # (misura, x della stanghetta iniziale)
    right: float = 0.0  # x della fine del rigo

    def _index(self) -> tuple[list[float], list[float]]:
        """Posizioni (float) e x della mappa, calcolate una volta: ricerca binaria a ogni ridisegno."""
        if getattr(self, "_idx_len", -1) != len(self.x_map):
            self._idx_pos = [float(p) for p, _x in self.x_map]
            self._idx_x = [x for _p, x in self.x_map]
            self._idx_len = len(self.x_map)
        return self._idx_pos, self._idx_x

    def x_at(self, pos: F | float) -> float | None:
        """x di una posizione dentro questo sistema (None se fuori)."""
        ps, xs = self._index()
        p = float(pos)
        if not ps or p < ps[0] or p > ps[-1]:
            return None
        i = bisect_right(ps, p) - 1
        while i > 0 and ps[i] == p and ps[i - 1] == p:  # posizione ripetuta: la prima x
            i -= 1
        if i >= len(ps) - 1:
            return xs[-1]
        p0, p1 = ps[i], ps[i + 1]
        if p1 <= p0:
            return xs[i]
        return xs[i] + (p - p0) / (p1 - p0) * (xs[i + 1] - xs[i])

    def bars_between(self, x0: float, x1: float) -> tuple[int, int]:
        """Misure (inizio, fine esclusa) visibili fra x0 e x1."""
        xs = [x for _b, x in self.bars]
        i = max(bisect_right(xs, x0) - 1, 0)
        j = bisect_right(xs, x1)
        return self.bars[i][0] if self.bars else self.first_bar, (self.bars[j - 1][0] + 1) if self.bars and j else self.first_bar


@dataclass
class ScoreLayout:
    width: float
    height: float
    items: list[Item]
    systems: list[System]

    def locate(self, pos: F) -> tuple[System, float] | None:
        """Sistema e x per una posizione musicale (cursore di playback)."""
        for sys in self.systems:
            x = sys.x_at(pos)
            if x is not None:
                return sys, x
        return None


    def position_at(self, x: float, y: float) -> F | None:
        """Posizione musicale nel punto (x, y) in spazi del rigo (click per spostare il cursore)."""
        if not self.systems:
            return None
        sys = min(self.systems, key=lambda s: abs(y - (s.top + s.bottom) / 2))
        xm = sys.x_map
        if not xm:
            return None
        if x <= xm[0][1]:
            return xm[0][0]
        for (p0, x0), (p1, x1) in zip(xm, xm[1:]):
            if x0 <= x <= x1 and x1 > x0:
                return p0 + (p1 - p0) * F(round((x - x0) / (x1 - x0) * 960), 960)
        return xm[-1][0]


# --- figure e scomposizione delle durate ----------------------------------


@dataclass(frozen=True)
class Figure:
    base: F  # 1 = semibreve, 1/4 = semiminima
    dots: int = 0
    triplet: bool = False

    @property
    def value(self) -> F:
        v = self.base * (2 - F(1, 2**self.dots))
        return v * F(2, 3) if self.triplet else v


_BASES = [F(1), F(1, 2), F(1, 4), F(1, 8), F(1, 16), F(1, 32), F(1, 64)]
FIGURES = sorted(
    [Figure(b, d) for b in _BASES for d in (0, 1, 2) if not (d == 2 and b < F(1, 16)) and not (d and b == F(1, 64))]
    + [Figure(b, 0, True) for b in _BASES[1:6]],
    key=lambda f: (-f.value, f.dots, f.triplet),
)


def on_grid(x: F, grids=FINE_GRIDS) -> bool:
    return any(x % g == 0 for g in grids)


# valori precalcolati (decompose è il punto caldo dell'impaginazione)
_ALL_VALUES = [(f, f.value) for f in FIGURES]
_STRAIGHT_VALUES = [(f, v) for f, v in _ALL_VALUES if not f.triplet]


def snap(x: F) -> F:
    """Griglia più vicina; la terzina vince solo se è nettamente più vicina."""
    straight, triplet = (F(round(x / g) * g) for g in COARSE_GRIDS)
    return triplet if abs(triplet - x) * 2 < abs(straight - x) else straight


def display_span(start: F, end: F) -> tuple[F, F, bool]:
    """Inizio e fine come mostrati in notazione, più il flag "arrotondata" (rossa)."""
    if on_grid(start) and on_grid(end):
        return start, end, False
    s, e = snap(start), snap(end)
    if e <= s:
        e = s + COARSE_GRIDS[0]
    return s, e, True


def decompose(offset: F, length: F, beat: F) -> list[tuple[Figure, F]]:
    """Scompone una durata (da `offset` dentro la misura) in figure scrivibili.
    Un valore che parte fuori dal movimento si ferma al movimento successivo;
    le figure di terzina si usano solo se servono."""
    straight = offset % F(1, 64) == 0 and length % F(1, 64) == 0
    figures = _STRAIGHT_VALUES if straight else _ALL_VALUES
    out: list[tuple[Figure, F]] = []
    while length > 0:
        into_beat = offset % beat
        limit = min(length, beat - into_beat) if into_beat else length
        pick = pick_value = None
        if into_beat:
            # 2026-11-20: partendo fuori dal movimento, se il tratto fino al limite (fine della nota
            # o del movimento) è una sola figura semplice o puntata, si usa quella: croma dopo una
            # semicroma (sincope), croma puntata dopo una semicroma (ritmo lombardo). Prima veniva
            # scelta una semicroma doppiamente puntata legata a una 1/64.
            pick, pick_value = next(((f, v) for f, v in figures if v == limit and not f.triplet and f.dots < 2),
                                    (None, None))
        for f, value in figures if pick is None else ():
            if f.triplet:
                # una figura di terzina sta nel suo gruppo (3 figure = 2 valori normali), anche se
                # il gruppo è più lungo del movimento: le terzine di semiminima occupano 2/4
                group = 3 * value
                fits_group = not offset % value and value <= min(length, (offset // group + 1) * group - offset)
                # 2026-11-20: o dentro la terzina del movimento (3 crome di terzina in un quarto):
                # la semiminima di terzina dello shuffle (1/6 + 1/12) anche sul 2° e 4° movimento
                fits_beat = (value < beat and not into_beat % (beat / 3) and not value % (beat / 3)
                             and into_beat + value <= beat and value <= length)
                if not (fits_group or fits_beat):
                    continue
                pick, pick_value = f, value
                break
            if value > limit:
                continue
            if f.dots == 2 and into_beat:  # 2026-11-20: niente doppie puntate che partono fuori dal movimento
                continue
            if offset % min(f.base, beat) == 0:
                pick, pick_value = f, value
                break
        if pick is None:  # posizione non allineata: la più grande che sta
            pick, pick_value = next(((f, v) for f, v in figures if v <= limit), (Figure(F(1, 64)), F(1, 64)))
        v = pick_value if pick.triplet else min(pick_value, limit)  # la terzina non si ferma al movimento
        out.append((pick, v))
        offset += v
        length -= v
    return out


# --- eventi per misura ----------------------------------------------------


@dataclass
class Head:
    midi: int
    pitch: Pitch
    step: int
    red: bool
    accidental: int | None = None
    keys: list = field(default_factory=list)  # (id segmento, id nota) delle note di questa testa
    tie: bool = False  # 2026-11-20: la nota di questa testa è legata (nel modello) alla successiva


@dataclass
class Piece:
    start: F
    figure: F | Figure
    heads: list[Head]  # vuoto = pausa
    tie_next: bool = False
    # 2026-11-20: legatura su tutte le teste (la figura è spezzata o l'accordo prosegue nella
    # misura dopo); le legature del modello valgono invece solo per le teste con Head.tie
    tie_all: bool = False
    continuation: bool = False  # proseguimento legato (niente alterazione)
    staccato: bool = False  # punto di staccato (sull'attacco, non sui proseguimenti legati)
    strum: str | None = None  # pennata: arpeggiato davanti all'accordo ("down" = freccia in su)
    whole_bar_rest: bool = False
    beam: "_Beam | None" = field(default=None, compare=False, repr=False)  # travatura (solo visualizzazione)
    # calcolati in impaginazione
    x: float = 0.0
    pre: float = 0.0
    width: float = 0.0


@dataclass
class _Beam:
    """Gruppo di note unite da travatura: gambi tutti nella stessa direzione, fino alla
    stessa altezza (travatura orizzontale)."""

    pieces: list[Piece]
    up: bool = True
    step: float = 0.0  # posizione (in passi del rigo) dell'estremità dei gambi
    levels: int = 1  # numero di travature (1 = crome, 2 = semicrome, …)


def _beamable(p: Piece) -> bool:
    return bool(p.heads) and isinstance(p.figure, Figure) and p.figure.base <= F(1, 8)


def _levels(p: Piece) -> int:
    return {F(1, 8): 1, F(1, 16): 2, F(1, 32): 3, F(1, 64): 4}.get(p.figure.base, 1)


def beam_groups(song: Song, bar: int, pieces: list[Piece]) -> list[list[Piece]]:
    """Gruppi di travatura di una misura (solo visualizzazione).
    Crome e valori più brevi consecutivi dentro lo stesso movimento (nei tempi composti
    6/8, 9/8, 12/8: la semiminima puntata); una pausa o un valore più lungo interrompe.
    In 4/4 le crome di mezza misura si uniscono a 4 se riempiono i due movimenti."""
    ts = song.meters.at(bar)
    bs = song.meters.bar_start(bar)
    window = F(3, 8) if ts.denominator == 8 and ts.numerator in (6, 9, 12) else ts.beat
    groups: list[list[Piece]] = []
    current: list[Piece] = []
    key = None
    for p in sorted(pieces, key=lambda q: q.start):
        k = (p.start - bs) // window
        if not _beamable(p) or k != key:
            if len(current) > 1:
                groups.append(current)
            current, key = [], k
        if _beamable(p):
            current.append(p)
    if len(current) > 1:
        groups.append(current)
    if (ts.numerator, ts.denominator) == (4, 4):  # mezza misura: due movimenti di sole crome
        plain = lambda g: len(g) == 2 and all(q.figure == Figure(F(1, 8)) for q in g)  # noqa: E731
        merged, i = [], 0
        while i < len(groups):
            a = groups[i]
            b = groups[i + 1] if i + 1 < len(groups) else None
            if (b is not None and plain(a) and plain(b) and (a[0].start - bs) % F(1, 2) == 0
                    and b[0].start == a[0].start + F(1, 4)):
                merged.append(a + b)
                i += 2
            else:
                merged.append(a)
                i += 1
        groups = merged
    return groups


def assign_beams(song: Song, bar: int, pieces: list[Piece]) -> None:
    """Prepara le travature della misura: direzione comune dei gambi e altezza della travatura."""
    for p in pieces:
        p.beam = None
    for group in beam_groups(song, bar, pieces):
        steps = [h.step for p in group for h in p.heads]
        up = (min(steps) + max(steps)) / 2 < 4
        levels = max(_levels(p) for p in group)
        extra = (levels - 1) * 1.5  # passi in più per le travature aggiuntive
        if up:  # gambi su: travatura sopra la nota più alta, almeno alla linea centrale
            step = max(max(steps) + 7 + extra, 4)
        else:
            step = min(min(steps) - 7 - extra, 4)
        beam = _Beam(group, up, step, levels)
        for p in group:
            p.beam = beam


@dataclass
class _Chord:
    start: F
    end: F
    heads: list[Head]
    staccato: bool = False
    tie: bool = False  # una nota dell'accordo è legata (di valore) alla successiva
    strum: str | None = None  # pennata dell'accordo


def _chords(song: Song, track: Track) -> list[_Chord]:
    clef = track.clef
    by_onset: dict[F, list[tuple[F, Head]]] = {}
    staccato: set[F] = set()
    tied: set[F] = set()
    strums: dict[F, str] = {}
    for p in song.placed_notes(track):
        s, e, red = display_span(p.start, p.end)
        pitch = song.spell(p)
        by_onset.setdefault(s, []).append((e, Head(p.midi, pitch, clef.staff_step(pitch), red,
                                                   keys=[(p.segment.id, p.note.id)], tie=p.note.tie_next)))
        if p.note.staccato:
            staccato.add(s)
        if p.note.tie_next:
            tied.add(s)
        if p.note.strum:
            strums[s] = p.note.strum
    onsets = sorted(by_onset)
    chords = []
    for i, s in enumerate(onsets):
        notes = by_onset[s]
        end = max(e for e, _ in notes)
        if i + 1 < len(onsets):
            end = min(end, onsets[i + 1])
        for e, h in notes:  # durata mostrata diversa da quella vera (voce unica): rossa
            if e != end:
                h.red = True
        heads: dict[int, Head] = {}  # stessa altezza ripetuta: una testa (con tutte le sue note)
        for _, h in notes:
            if h.midi in heads:
                heads[h.midi].keys += h.keys
                heads[h.midi].tie |= h.tie
            else:
                heads[h.midi] = h
        chords.append(_Chord(s, end, sorted(heads.values(), key=lambda h: h.step), s in staccato, s in tied,
                             strums.get(s)))
    return chords


def _key_alters(fifths: int) -> dict[str, int]:
    alters = dict.fromkeys("CDEFGAB", 0)
    if fifths > 0:
        for letter in "FCGDAEB"[:fifths]:
            alters[letter] = 1
    elif fifths < 0:
        for letter in "BEADGCF"[: -fifths]:
            alters[letter] = -1
    return alters


def _bar_pieces(song: Song, chords: list[_Chord], bar: int, start_idx: int) -> tuple[list[Piece], int]:
    meters = song.meters
    bs, be = meters.bar_start(bar), meters.bar_start(bar + 1)
    beat = meters.at(bar).beat
    spans: list[tuple[F, F, _Chord | None]] = []
    cursor, i = bs, start_idx
    while i < len(chords) and chords[i].end <= bs:
        i += 1
    j = i
    while j < len(chords) and chords[j].start < be:
        c = chords[j]
        s, e = max(c.start, bs), min(c.end, be)
        if s > cursor:
            spans.append((cursor, s, None))
        spans.append((s, e, c))
        cursor = e
        j += 1
    if cursor < be:
        spans.append((cursor, be, None))

    pieces: list[Piece] = []
    if len(spans) == 1 and spans[0][2] is None:
        return [Piece(bs, Figure(F(1)), [], whole_bar_rest=True)], i
    mid = bs + F(1, 2)  # metà della misura di 4/4
    for s, e, chord in spans:
        # 2026-11-20: in 4/4 una pausa non scavalca la metà della misura (regola di grafia):
        # sul 2° movimento due pause di semiminima, non una pausa di minima
        if chord is None and be - bs == 1 and beat == F(1, 4) and s < mid < e:
            figs = decompose(s - bs, mid - s, beat) + decompose(mid - bs, e - mid, beat)
        else:
            figs = decompose(s - bs, e - s, beat)
        pos = s
        for k, (fig, v) in enumerate(figs):
            last = k == len(figs) - 1
            if chord is None:
                pieces.append(Piece(pos, fig, []))
            else:
                heads = [Head(h.midi, h.pitch, h.step, h.red, keys=list(h.keys), tie=h.tie) for h in chord.heads]
                pieces.append(
                    Piece(
                        pos, fig, heads,
                        tie_next=(not last) or chord.end > e or chord.tie,  # anche le legature del modello
                        tie_all=(not last) or chord.end > e,
                        continuation=k > 0 or chord.start < bs,
                        staccato=chord.staccato and k == 0 and chord.start >= bs,
                        strum=chord.strum if k == 0 and chord.start >= bs else None,
                    )
                )
            pos += v

    # alterazioni: armatura + alterazioni già comparse nella misura
    key_alters = _key_alters(song.keys.at(bar).fifths)
    state: dict[tuple[str, int], int] = {}
    # 2026-11-20: le note che arrivano legate dalla misura prima (proseguimento o legatura del
    # modello, es. dopo uno Split) non mostrano l'alterazione e non contano come "già
    # comparsa": prima un Fa♯ legato oltre la stanghetta toglieva il diesis al Fa♯ seguente,
    # e dopo uno Split la nota legata lo ripeteva
    tied_in = {h.midi for c in chords[max(0, i - 16):i] if c.end == bs for h in c.heads if h.tie}
    for p in pieces:
        if p.continuation:
            continue
        for h in p.heads:
            if p.start == bs and h.midi in tied_in:
                continue
            slot = (h.pitch.spelling.step, h.pitch.octave)
            current = state.get(slot, key_alters[slot[0]])
            if h.pitch.spelling.alter != current and not p.continuation:
                h.accidental = h.pitch.spelling.alter
            state[slot] = h.pitch.spelling.alter
    return pieces, i


# --- impaginazione --------------------------------------------------------


@dataclass
class _Staff:
    track: Track
    chords: list[_Chord]
    idx: int = 0  # prossimo accordo da considerare


@dataclass
class _BarCols:
    """Colonne di una misura, comuni a tutti i righi (stesse posizioni → stessa x)."""

    onsets: list[F]
    widths: list[float]
    pre: list[float]  # spazio per le alterazioni prima delle teste, per colonna
    pieces: list[list[Piece]]  # per rigo

    @property
    def width(self) -> float:
        return BAR_PAD + sum(self.widths)


def _ideal(gap: F) -> float:
    return 3.2 * float(gap * 4) ** 0.6


class _Engraver:
    def __init__(self, song: Song, tracks: list[Track], first_bar: int, last_bar: int, width: float | None) -> None:
        self.song, self.width = song, width
        self.first_bar, self.last_bar = first_bar, last_bar
        self.staves = [_Staff(t, _chords(song, t)) for t in tracks]
        self.left = LABEL_W if len(tracks) > 1 else 0.0
        self.md = metadata()
        self.eng = self.md.engraving
        self.head_w = self.md.bbox("noteheadBlack").width
        self.items: list[Item] = []

    def y(self, top: float, step: int) -> float:
        return top + STAFF_HEIGHT - step / 2

    def extents(self, st: _Staff) -> tuple[float, float]:
        """Ingombro (spazi) sopra la quinta linea e sotto la prima, per le note nelle misure mostrate."""
        lo_pos = self.song.meters.bar_start(self.first_bar)
        hi_pos = self.song.meters.bar_start(self.last_bar)
        steps = [h.step for c in st.chords if c.end > lo_pos and c.start < hi_pos for h in c.heads]
        if not steps:
            return MIN_EXTENT, MIN_EXTENT
        above = (max(steps) - 8) / 2 + 2.0  # tagli addizionali + gambo/cediglia/legatura
        below = -min(steps) / 2 + 2.0
        return max(MIN_EXTENT, above), max(MIN_EXTENT, below)

    def need(self, p: Piece) -> float:
        """Larghezza minima di un evento (alterazioni + teste + punti + cediglia)."""
        fig = p.figure
        if p.whole_bar_rest:
            return 3.0
        acc = [h for h in p.heads if h.accidental is not None]
        p.pre = (self.md.bbox("accidentalFlat").width + 0.25) * min(len(acc), 2) if acc else 0.0
        if not p.heads:
            return 1.8 + (fig.dots * 0.5 if isinstance(fig, Figure) else 0)
        seconds = any(b.step - a.step == 1 for a, b in zip(p.heads, p.heads[1:]))
        dots = fig.dots * 0.5 + 0.3 if isinstance(fig, Figure) and fig.dots else 0.0
        flag = 1.0 if isinstance(fig, Figure) and fig.base <= F(1, 8) else 0.0
        return p.pre + self.head_w * (2 if seconds else 1) + dots + flag + 0.6

    def bar_columns(self, bar: int) -> _BarCols:
        song = self.song
        bs, be = song.meters.bar_start(bar), song.meters.bar_start(bar + 1)
        per_staff = []
        for st in self.staves:
            pieces, st.idx = _bar_pieces(song, st.chords, bar, st.idx)
            per_staff.append(pieces)
        onsets = sorted({bs} | {p.start for ps in per_staff for p in ps if not p.whole_bar_rest})
        gaps = [b - a for a, b in zip(onsets, onsets[1:] + [be])]
        widths, pre = [], []
        for onset, gap in zip(onsets, gaps):
            at = [p for ps in per_staff for p in ps if p.start == onset]
            needs = [self.need(p) for p in at]
            widths.append(max([_ideal(gap)] + needs))
            pre.append(max([p.pre for p in at if not p.whole_bar_rest] + [0.0]))
        # larghezza minima (misure vuote o con poche note restano comode da modificare):
        # le colonne si allargano in proporzione
        minimum = float(be - bs) * MIN_BAR_PER_WHOLE
        total = sum(widths)
        if 0 < total < minimum:
            widths = [w * minimum / total for w in widths]
        return _BarCols(onsets, widths, pre, per_staff)

    def header_width(self, bar: int, first: bool) -> float:
        n = abs(self.song.keys.at(bar).fifths)
        w = 3.4 + (n * 1.05 + 0.6 if n else 0.0)
        if first or self.meter_changes(bar):
            w += 2.8
        return w

    def meter_changes(self, bar: int) -> bool:
        return any(e.at == bar for e in self.song.meters) and bar > 0

    def key_changes(self, bar: int) -> bool:
        """Cambio di tonalità con armatura diversa all'inizio di `bar` (2026-11-20: prima i cambi
        dentro un sistema non si disegnavano, ma le alterazioni seguivano già la nuova armatura)."""
        return bar > 0 and self.song.keys.at(bar).fifths != self.song.keys.at(bar - 1).fifths

    def _cancelled(self, bar: int) -> tuple[int, int]:
        """(alterazioni della vecchia armatura da annullare con il bequadro, segno della vecchia):
        tutte se cambia il segno (o si torna a Do), solo quelle in più se ne restano meno."""
        old, new = self.song.keys.at(bar - 1).fifths, self.song.keys.at(bar).fifths
        if old == 0:
            return 0, 0
        same_sign = (old > 0) == (new > 0) and new != 0
        n = max(abs(old) - abs(new), 0) if same_sign else abs(old)
        return n, (1 if old > 0 else -1)

    def key_change_width(self, bar: int) -> float:
        """Spazio orizzontale di un cambio di tonalità dentro il sistema (bequadri + nuova armatura)."""
        naturals, _sign = self._cancelled(bar)
        new = abs(self.song.keys.at(bar).fifths)
        return 0.4 + (naturals * 1.05 + 0.6 if naturals else 0.0) + (new * 1.05 + 0.6 if new else 0.0)

    def draw_key_change(self, top: float, x: float, bar: int, clef: Clef) -> float:
        """Cambio di tonalità dentro il sistema: bequadri sulle alterazioni che spariscono
        (nelle posizioni della vecchia armatura), poi la nuova armatura."""
        naturals, sign = self._cancelled(bar)
        if naturals:
            delta = (30 - clef.bottom_line) % 7
            adj = delta if delta <= 3 else delta - 7
            steps = [8, 5, 9, 6, 3, 7, 4] if sign > 0 else [4, 7, 3, 6, 2, 5, 1]
            old = abs(self.song.keys.at(bar - 1).fifths)
            for s in steps[old - naturals: old]:  # le ultime alterazioni della vecchia armatura
                self.items.append(Glyph("accidentalNatural", x, self.y(top, s + adj)))
                x += 1.05
            x += 0.6
        return self.draw_key_signature(top, x, self.song.keys.at(bar).fifths, clef)

    # disegno
    def draw_header(self, top: float, bar: int, first: bool, clef: Clef) -> float:
        glyph = {"G": "gClef", "F": "fClef", "C": "cClef"}.get(clef.sign, "unpitchedPercussionClef1")
        line = clef.line if clef.sign in "GFC" else 3
        self.items.append(Glyph(glyph, self.left + 0.6, top + STAFF_HEIGHT - (line - 1)))
        x = self.left + 3.4
        fifths = self.song.keys.at(bar).fifths
        x = self.draw_key_signature(top, x, fifths, clef)
        if first or self.meter_changes(bar):
            x = self.draw_time_signature(top, x, bar)
        return x

    def draw_key_signature(self, top: float, x: float, fifths: int, clef: Clef) -> float:
        if not fifths:
            return x
        delta = (30 - clef.bottom_line) % 7
        adj = delta if delta <= 3 else delta - 7
        steps = [8, 5, 9, 6, 3, 7, 4] if fifths > 0 else [4, 7, 3, 6, 2, 5, 1]
        glyph = "accidentalSharp" if fifths > 0 else "accidentalFlat"
        for s in steps[: abs(fifths)]:
            self.items.append(Glyph(glyph, x, self.y(top, s + adj)))
            x += 1.05
        return x + 0.6

    def draw_time_signature(self, top: float, x: float, bar: int) -> float:
        ts = self.song.meters.at(bar)
        for value, y in ((ts.numerator, top + 1), (ts.denominator, top + 3)):
            digits = str(value)
            w = sum(self.md.bbox(f"timeSig{d}").width for d in digits)
            cx = x + (2.0 - w) / 2
            for d in digits:
                self.items.append(Glyph(f"timeSig{d}", cx, y))
                cx += self.md.bbox(f"timeSig{d}").width
        return x + 2.8

    def draw_piece(self, top: float, p: Piece) -> None:
        fig = p.figure
        if not p.heads:
            self.draw_rest(top, p)
            return
        assert isinstance(fig, Figure)
        x = p.x + p.pre
        red_any = any(h.red for h in p.heads)
        head = "noteheadWhole" if fig.base >= 1 else "noteheadHalf" if fig.base == F(1, 2) else "noteheadBlack"
        hw = self.md.bbox(head).width
        steps = [h.step for h in p.heads]
        up = (min(steps) + max(steps)) / 2 < 4 if p.beam is None else p.beam.up
        th = self.eng["stemThickness"]

        # teste (seconde: una testa dall'altra parte del gambo)
        xs: dict[int, float] = {}
        order = p.heads if up else list(reversed(p.heads))
        prev, prev_shift = None, False
        for h in order:
            shift = prev is not None and abs(h.step - prev) == 1 and not prev_shift
            xs[id(h)] = x + ((hw - th) if up else -(hw - th)) * shift
            prev, prev_shift = h.step, shift
        for h in p.heads:
            self.items.append(Glyph(head, xs[id(h)], self.y(top, h.step), h.red, keys=tuple(h.keys)))

        # tagli addizionali
        ext = self.eng["legerLineExtension"]
        lo, hi = min(steps), max(steps)
        x0, x1 = min(xs.values()) - ext, max(xs.values()) + hw + ext
        for s in list(range(-2, lo - 1, -2)) + list(range(10, hi + 1, 2)):
            self.items.append(Line(x0, self.y(top, s), x1, self.y(top, s), self.eng["legerLineThickness"]))

        # pennata: segno ⊓ (giù) o V (su) sopra l'accordo, centrato sulle teste, fuori dal rigo e
        # oltre il gambo se va su
        if p.strum:
            glyph = "stringsDownBow" if p.strum == "down" else "stringsUpBow"
            gx = min(xs.values()) + hw / 2 - self.md.bbox(glyph).width / 2
            above = self.y(top, hi) - (STEM_LENGTH + 0.6 if up and fig.base < 1 else 1.2)
            self.items.append(Glyph(glyph, gx, min(above, top - 0.8), red_any))

        # alterazioni (fino a due colonne)
        acc = [h for h in reversed(p.heads) if h.accidental is not None]
        for i, h in enumerate(acc):
            g = ACCIDENTAL_GLYPH[h.accidental]
            col = i % 2 if len(acc) > 1 else 0
            ax = min(xs.values()) - (self.md.bbox(g).width + 0.2) * (col + 1)
            self.items.append(Glyph(g, ax, self.y(top, h.step), h.red))

        # punti
        for h in p.heads:
            dy = 0.5 if h.step % 2 == 0 else 0.0
            for d in range(fig.dots):
                dx = max(xs.values()) + hw + 0.3 + d * 0.5
                self.items.append(Glyph("augmentationDot", dx, self.y(top, h.step) - dy, h.red))

        # staccato: punto dalla parte opposta al gambo, in uno spazio (non su una linea)
        if p.staccato:
            above = not up
            target = hi + 2 if above else lo - 2
            if 0 <= target <= 8 and target % 2 == 0:
                target += 1 if above else -1
            glyph = "articStaccatoAbove" if above else "articStaccatoBelow"
            gx = x + hw / 2 - self.md.bbox(glyph).width / 2
            gy = self.y(top, target) + (0.168 if above else -0.168)  # centro del punto sulla posizione
            self.items.append(Glyph(glyph, gx, gy, red_any))

        # gambo e cediglia
        if fig.base < 1:
            extra = {F(1, 32): 0.75, F(1, 64): 1.5}.get(fig.base, 0.0)
            mid = top + 2
            if up:
                sx = x + hw - th / 2
                y0 = self.y(top, lo) - 0.168
                y1 = min(self.y(top, hi) - STEM_LENGTH - extra, mid)
            else:
                sx = x + th / 2
                y0 = self.y(top, hi) + 0.168
                y1 = max(self.y(top, lo) + STEM_LENGTH + extra, mid)
            if p.beam is not None:  # con travatura: il gambo arriva alla travatura, niente cediglia
                y1 = self.y(top, p.beam.step)
            self.items.append(Line(sx, y0, sx, y1, th, red_any))
            p._sx = sx  # type: ignore[attr-defined]
            flag = {F(1, 8): "8th", F(1, 16): "16th", F(1, 32): "32nd", F(1, 64): "64th"}.get(fig.base)
            if flag and p.beam is None:
                self.items.append(Glyph(f"flag{flag}{'Up' if up else 'Down'}", sx - th / 2, y1, red_any))
        p._up = up  # type: ignore[attr-defined]
        p._xs = xs  # type: ignore[attr-defined]
        p._hw = hw  # type: ignore[attr-defined]

    def draw_rest(self, top: float, p: Piece) -> None:
        fig = p.figure
        assert isinstance(fig, Figure)
        if p.whole_bar_rest:
            self.items.append(Glyph("restWhole", p.x + p.width / 2 - 0.6, top + 1))
            return
        names = {F(1): "restWhole", F(1, 2): "restHalf", F(1, 4): "restQuarter", F(1, 8): "rest8th",
                 F(1, 16): "rest16th", F(1, 32): "rest32nd", F(1, 64): "rest64th"}
        name = names[fig.base]
        y = top + 1 if fig.base == 1 else top + 2
        self.items.append(Glyph(name, p.x, y))
        for d in range(fig.dots):
            self.items.append(Glyph("augmentationDot", p.x + self.md.bbox(name).width + 0.3 + d * 0.5, top + 1.5))

    def draw_ties(self, placed: list[tuple[Piece, float, System]]) -> None:
        """Legature di valore di un rigo: (pezzo, y del rigo, sistema)."""
        for (p, top, sys), (q, top2, sys2) in zip(placed, placed[1:]):
            if not p.tie_next or not p.heads or not q.heads:
                continue
            for h in p.heads:
                if not (p.tie_all or h.tie):  # legatura del modello: solo sulle note legate davvero
                    continue
                h2 = next((k for k in q.heads if k.midi == h.midi), None)
                if h2 is None:
                    continue
                below = p._up  # type: ignore[attr-defined]
                y = self.y(top, h.step) + (0.6 if below else -0.6)
                x1 = p._xs[id(h)] + p._hw + 0.1  # type: ignore[attr-defined]
                x2 = q._xs[id(h2)] - 0.1  # type: ignore[attr-defined]
                red = h.red or h2.red
                if sys is sys2:
                    self.items.append(Tie(x1, x2, y, below, red))
                else:
                    self.items.append(Tie(x1, sys.right, y, below, red))
                    y2 = self.y(top2, h2.step) + (0.6 if below else -0.6)
                    self.items.append(Tie(x2 - 2.0, x2, y2, below, red))

    def draw_beams(self, top: float, pieces: list[Piece]) -> None:
        """Travature orizzontali dei gruppi della misura (solo visualizzazione).
        Livelli successivi (semicrome…) fra note vicine che li hanno entrambe; una nota
        sola con più livelli dei vicini ha un mozzicone verso la nota accanto."""
        thick = self.eng["beamThickness"]
        gap = thick + self.eng.get("beamSpacing", 0.25)
        seen = set()
        for p in pieces:
            beam = p.beam
            if beam is None or id(beam) in seen:
                continue
            seen.add(id(beam))
            group = [q for q in beam.pieces if hasattr(q, "_sx")]
            if len(group) < 2:
                continue
            sign = 1 if beam.up else -1  # le travature stanno dalla parte interna dei gambi
            y_end = self.y(top, beam.step)
            red = any(h.red for q in group for h in q.heads)
            for level in range(beam.levels):
                y = y_end + sign * (thick / 2 + level * gap)
                if level == 0:
                    self.items.append(Line(group[0]._sx, y, group[-1]._sx, y, thick, red))
                    continue
                for i, q in enumerate(group):
                    if _levels(q) <= level:
                        continue
                    nxt = group[i + 1] if i + 1 < len(group) else None
                    prv = group[i - 1] if i > 0 else None
                    if nxt is not None and _levels(nxt) > level:
                        self.items.append(Line(q._sx, y, nxt._sx, y, thick, red))
                    elif prv is None or _levels(prv) <= level:  # mozzicone verso il vicino
                        towards = 1 if nxt is not None else -1
                        self.items.append(Line(q._sx, y, q._sx + towards * 1.0, y, thick, red))

    def draw_slurs(self, track: Track, placed: list[tuple[Piece, float, System]]) -> None:
        """Legature di frase della traccia: dalla testa della prima nota a quella dell'ultima,
        dalla parte delle teste (sotto se i gambi vanno tutti su, altrimenti sopra), alte
        abbastanza da non toccare le note in mezzo."""
        song = self.song
        attacks = [(i, p) for i, (p, _t, _s) in enumerate(placed) if p.heads and not p.continuation]
        by_start = {p.start: i for i, p in attacks}
        # 2026-11-20: inizio mostrato di ogni nota, per segmento, in una sola passata sulle note della
        # traccia (prima una passata per ogni segmento con slur: lento con molti cloni)
        slurred = {id(seg) for seg in track.segments if song.groups[seg.group_id].slurs}
        starts_by_seg: dict[int, dict] = {}
        if slurred:
            for pn in song.placed_notes(track):
                if id(pn.segment) in slurred:
                    starts_by_seg.setdefault(id(pn.segment), {})[pn.note.id] = display_span(pn.start, pn.end)[0]
        for seg in track.segments:
            group = song.groups[seg.group_id]
            if not group.slurs:
                continue
            starts = starts_by_seg.get(id(seg), {})
            for first, last in group.slurs:
                if first not in starts or last not in starts:
                    continue
                i, j = by_start.get(starts[first]), by_start.get(starts[last])
                if i is None or j is None or j <= i:
                    continue
                run = [placed[k] for k in range(i, j + 1) if placed[k][0].heads]
                below = all(getattr(p, "_up", True) for p, _t, _s in run)
                (p, top, sys), (q, top2, sys2) = placed[i], placed[j]

                def anchor(piece: Piece, t: float) -> tuple[float, float]:
                    steps = [h.step for h in piece.heads]
                    x = min(piece._xs.values()) + piece._hw / 2  # type: ignore[attr-defined]
                    y = self.y(t, min(steps)) + 1.0 if below else self.y(t, max(steps)) - 1.0
                    return x, y

                (x1, y1), (x2, y2) = anchor(p, top), anchor(q, top2)
                if sys is not sys2:  # su due sistemi: due archi aperti
                    self.items.append(Slur(x1, y1, sys.right, y1, below, 1.0))
                    self.items.append(Slur(x2 - 3.0, y2, x2, y2, below, 1.0))
                    continue
                # altezza: le note in mezzo non devono toccare l'arco
                height = min(0.6 + (x2 - x1) * 0.06, 2.0)
                for r, t, _s in run[1:-1]:
                    rx = min(r._xs.values()) + r._hw / 2  # type: ignore[attr-defined]
                    frac = (rx - x1) / (x2 - x1) if x2 > x1 else 0.5
                    line_y = y1 + (y2 - y1) * frac
                    steps = [h.step for h in r.heads]
                    note_y = self.y(t, min(steps)) + 1.0 if below else self.y(t, max(steps)) - 1.0
                    need = (note_y - line_y) if below else (line_y - note_y)
                    curve = 4 * frac * (1 - frac) * 0.75  # quota della curva rispetto al punto più alto
                    if curve > 0 and need > 0:
                        height = max(height, need / curve)
                self.items.append(Slur(x1, y1, x2, y2, below, min(height, 4.0)))

    def draw_chord_symbols(self, k: int, systems: list) -> None:
        """Sigle d'accordo dei segmenti della traccia, sopra il rigo (sopra le note più alte),
        all'altezza della loro posizione; ♯/♭ al posto di #/b."""
        song, track = self.song, self.staves[k].track
        lo, hi = song.meters.bar_start(self.first_bar), song.meters.bar_start(self.last_bar)
        for seg in track.segments:
            start, _end = song.segment_span(seg)
            for pos, text in song.groups[seg.group_id].chords:
                rel = pos - seg.trim
                if not 0 <= rel < seg.length or not lo <= start + rel < hi:
                    continue
                for sys in systems:
                    x = sys.x_at(start + rel)
                    if x is None:
                        continue
                    top = sys.staff_boxes[k][0]  # sopra tutto ciò che sporge dal rigo
                    self.items.append(Text(_pretty_symbol(text), x, top + 0.3, 1.5))
                    break

    def draw_triplets(self, top: float, pieces: list[Piece], beat: F = F(1, 4)) -> None:
        run: list[Piece] = []
        total, unit = F(0), F(0)
        shortest = longest = F(0)  # gruppo (base × 2) della figura più breve e più lunga del gruppo

        def flush() -> None:
            if not run:
                return
            xa, xb = run[0].x + run[0].pre, run[-1].x + run[-1].pre + self.head_w
            mid = (xa + xb) / 2
            notes = [p for p in run if p.heads]
            beam = run[0].beam
            if beam is not None and all(p.beam is beam for p in run):  # con travatura: solo il 3
                y = self.y(top, beam.step)
                self.items.append(Glyph("tuplet3", mid - 0.4, y - 0.9 if beam.up else y + 1.9, scale=0.8))
                return
            # senza travatura: parentesi con il 3 al centro, dalla parte dei gambi
            up = sum(1 for p in notes if getattr(p, "_up", True)) * 2 >= len(notes)
            ends = []
            for p in notes:
                steps = [h.step for h in p.heads]
                stem = p.figure.base < 1 if isinstance(p.figure, Figure) else False
                if up:
                    ends.append(self.y(top, max(steps)) - (STEM_LENGTH if stem and getattr(p, "_up", True) else 0.6))
                else:
                    ends.append(self.y(top, min(steps)) + (STEM_LENGTH if stem and not getattr(p, "_up", True) else 0.6))
            if up:
                y = min(ends + [top - 0.5]) - 1.0
                hook = y + 0.6
            else:
                y = max(ends + [top + STAFF_HEIGHT + 0.5]) + 1.0
                hook = y - 0.6
            th = 0.12
            gap = 0.9  # spazio per il 3
            for x1, x2 in ((xa, mid - gap), (mid + gap, xb)):
                self.items.append(Line(x1, y, x2, y, th))
            self.items.append(Line(xa, y, xa, hook, th))
            self.items.append(Line(xb, y, xb, hook, th))
            self.items.append(Glyph("tuplet3", mid - 0.4, y + 0.55, scale=0.8))

        for p in pieces:
            fig = p.figure
            if isinstance(fig, Figure) and fig.triplet:
                # 2026-11-20: il gruppo si chiude quando il totale è un multiplo del valore più breve
                # incontrato: nello shuffle (semiminima + croma di terzina) un "3" per movimento,
                # prima uno ogni due; semiminima, semiminima, croma, croma resta un gruppo solo
                # la più breve non scende sotto il movimento se nel gruppo c'è una figura da un movimento
                # o più: croma, semicroma, semicroma, croma di terzina restano un solo "3"
                group = fig.base * 2
                shortest, longest = (group, group) if not run else (min(shortest, group), max(longest, group))
                unit = max(shortest, min(beat, longest))
                run.append(p)
                total += fig.value
                if total % unit == 0:
                    flush()
                    run, total = [], F(0)
            else:
                flush()
                run, total = [], F(0)
        flush()

    def _tag(self, start: int, staff: int) -> None:
        """Assegna al rigo `staff` gli elementi aggiunti da `start` in poi."""
        for it in self.items[start:]:
            it.staff = staff

    # impaginazione completa
    def run(self) -> ScoreLayout:
        song = self.song
        n = len(self.staves)
        bar_range = list(range(self.first_bar, self.last_bar))
        # 2026-11-20: bar_count scorre tutti i segmenti del brano: una volta sola, non a ogni misura
        last_song_bar = song.bar_count - 1
        cols = {b: self.bar_columns(b) for b in bar_range}
        # posizione verticale dei righi in base all'ingombro reale delle note
        ext = [self.extents(st) for st in self.staves]
        offsets = [0.0]
        for k in range(1, n):
            offsets.append(offsets[-1] + STAFF_HEIGHT + ext[k - 1][1] + ext[k][0] + EXTENT_PAD)
        ruler_gap = max(RULER_BOTTOM, ext[0][0] + EXTENT_PAD)
        system_h = offsets[-1] + STAFF_HEIGHT
        systems: list[System] = []
        placed: list[list[tuple[Piece, float, System]]] = [[] for _ in self.staves]
        top = RULER_HEIGHT + ruler_gap  # il righello è il primo elemento in alto
        tempo_y = top - TEMPO_BASELINE
        self.items.append(Glyph("metNoteQuarterUp", self.left + 0.6, tempo_y, scale=0.8))
        self.items.append(Text(f"= {song.tempos.initial:g}", self.left + 1.8, tempo_y))
        i = 0
        while i < len(bar_range):
            first = not systems
            bar = bar_range[i]
            header = self.header_width(bar, first)
            end, used = i, 0.0
            while end < len(bar_range):
                b = bar_range[end]
                w = cols[b].width + (2.8 if end != i and self.meter_changes(b) else 0) \
                    + (self.key_change_width(b) if end != i and self.key_changes(b) else 0)
                if self.width is not None and end > i and used + w > self.width - self.left - header:
                    break
                used += w
                end += 1
            sys_bars = bar_range[i:end]
            tops = [top + off for off in offsets]
            sys = System(top, sys_bars[0], sys_bars[-1] + 1, staff_tops=tops, bottom=tops[-1] + STAFF_HEIGHT,
                         ruler_top=top - ruler_gap - RULER_HEIGHT)
            for k in range(n):
                above = max(ext[k][0], TEMPO_BASELINE + 2.4) if k == 0 else ext[k][0]  # il tempo sta sopra il primo rigo
                sys.staff_boxes.append((tops[k] - above, tops[k] + STAFF_HEIGHT + ext[k][1]))
            systems.append(sys)
            stretch = 1.0
            if self.width is not None:
                avail = self.width - self.left - header
                total = sum(sum(cols[b].widths) for b in sys_bars)
                last = end >= len(bar_range)
                if total and not (last and used < avail * 0.75):
                    stretch = 1 + (avail - used) / total
            right = self.left + header + used + sum(sum(cols[b].widths) for b in sys_bars) * (stretch - 1)
            sys.right = right

            # righi, nomi, intestazioni
            for k, st in enumerate(self.staves):
                mark = len(self.items)
                for line in range(5):
                    self.items.append(Line(self.left, tops[k] + line, right, tops[k] + line, self.eng["staffLineThickness"]))
                if n > 1:
                    self.items.append(Text(st.track.name[:14], 0.0, tops[k] + 2.4, 1.1))
                x = self.draw_header(tops[k], bar, first, st.track.clef)
                self._tag(mark, k)
            if n > 1:  # parentesi che unisce i righi
                self.items.append(Line(self.left, tops[0], self.left, sys.bottom, self.eng["thickBarlineThickness"], staff2=n - 1))

            bl = self.eng["thinBarlineThickness"]
            for b in sys_bars:
                if b != sys_bars[0] and (self.key_changes(b) or self.meter_changes(b)):
                    # cambio di tonalità (bequadri + armatura) e poi di metro, prima della misura
                    for k, t in enumerate(tops):
                        mark = len(self.items)
                        nx = x
                        if self.key_changes(b):
                            nx = self.draw_key_change(t, nx + 0.4, b, self.staves[k].track.clef)
                        if self.meter_changes(b):
                            nx = self.draw_time_signature(t, nx + 0.4, b)
                        self._tag(mark, k)
                    x = nx
                bc = cols[b]
                for pieces in bc.pieces:  # travature: direzione dei gambi decisa prima di disegnare
                    assign_beams(song, b, pieces)
                sys.bars.append((b, x))
                bar_x0 = x
                x += BAR_PAD
                bar_w = sum(bc.widths) * stretch
                for onset, width, pre in zip(bc.onsets, bc.widths, bc.pre):
                    head_x = x + pre
                    for k, pieces in enumerate(bc.pieces):
                        for p in pieces:
                            if p.whole_bar_rest and onset == bc.onsets[0]:
                                p.x, p.width = bar_x0 + BAR_PAD, bar_w
                            elif p.start == onset and not p.whole_bar_rest:
                                p.x = head_x - p.pre
                            else:
                                continue
                            mark = len(self.items)
                            self.draw_piece(tops[k], p)
                            self._tag(mark, k)
                            placed[k].append((p, tops[k], sys))
                    sys.x_map.append((onset, head_x))
                    x += width * stretch
                for k, pieces in enumerate(bc.pieces):
                    mark = len(self.items)
                    self.draw_beams(tops[k], pieces)
                    self.draw_triplets(tops[k], pieces, self.song.meters.at(b).beat)
                    self._tag(mark, k)
                if b == last_song_bar:
                    self.items.append(Line(x - 0.7, tops[0], x - 0.7, sys.bottom, bl, staff2=n - 1))
                    self.items.append(Line(x - 0.25, tops[0], x - 0.25, sys.bottom, self.eng["thickBarlineThickness"], staff2=n - 1))
                else:
                    self.items.append(Line(x, tops[0], x, sys.bottom, bl, staff2=n - 1))
                sys.x_map.append((song.meters.bar_start(b + 1), x))
            top += system_h + ext[-1][1] + EXTENT_PAD + RULER_HEIGHT + ruler_gap
            i = end
        for k, staff_placed in enumerate(placed):
            mark = len(self.items)
            self.draw_ties(staff_placed)
            self.draw_slurs(self.staves[k].track, staff_placed)
            self.draw_chord_symbols(k, systems)
            self._tag(mark, k)
        width = self.width if self.width is not None else max((s.right for s in systems), default=0.0) + 1.0
        height = (systems[-1].bottom if systems else 0.0) + ext[-1][1] + EXTENT_PAD
        return ScoreLayout(width, height, self.items, systems)


def engrave(
    song: Song,
    tracks: Track | list[Track],
    width: float | None = None,
    first_bar: int = 0,
    last_bar: int | None = None,
) -> ScoreLayout:
    """Impagina una o più tracce (un rigo ciascuna) sulle misure first_bar..last_bar-1.
    width=None: un solo rigo continuo; altrimenti sistemi larghi `width` spazi."""
    track_list = [tracks] if isinstance(tracks, Track) else list(tracks)
    last = song.bar_count if last_bar is None else last_bar
    return _Engraver(song, track_list, first_bar, last, width).run()


def _pretty_symbol(text: str) -> str:
    """Sigla da mostrare: # → ♯ e b → ♭ solo come alterazione (subito dopo la nota, o dopo "/")."""
    out = []
    for i, ch in enumerate(text):
        after_note = i > 0 and text[i - 1] in "ABCDEFG"
        if ch == "#" and after_note:
            out.append("♯")
        elif ch == "b" and after_note:
            out.append("♭")
        else:
            out.append(ch)
    return "".join(out)
