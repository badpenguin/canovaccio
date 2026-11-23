"""Comandi sulle note selezionate, comuni a griglia e notazione (menu Notes, tasti,
appunti, strumenti Split/Delete). La vista che li usa fornisce:

- `doc`, `transport`, `on_message`, `selection` (note selezionate), `edit_tracks`
  (tracce mostrate), `_placed` (note posizionate mostrate) e `_by_note`
  (id della nota → nota posizionata, solo note modificabili, non dei cloni);
- `snap` (Fraction o None = Off; `unit` e `snapped` sono qui), `redraw()`, `paste_target()`
  ((traccia, posizione) sotto il puntatore, o None);
- attributi `_fail`, `_fail_timer`, `_preview_key`, `_preview_track` (inizializzati
  da `init_note_editing`).
"""

from __future__ import annotations

from fractions import Fraction

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import GLib  # noqa: E402

from ..core.detect import detect_staccato, tie_targets  # noqa: E402
from ..core.chords import dedupe_symbols, detect_chord, place_symbol  # noqa: E402
from ..core.document import AddNotes, Compound, DeleteNotes, EditNotes, SetChords, SetSlurs  # noqa: E402
from ..core.key import diatonic_shift  # noqa: E402
from ..core.model import Note, PlacedNote, Segment, Track  # noqa: E402
from ..core.quantize import START_LENGTH, quantize_span  # noqa: E402
from .velocity_dialog import velocities  # noqa: E402

# appunti delle note, comuni a tutti gli editor: (inizio relativo, altezza suonata, copia della nota)
NOTE_CLIPBOARD: list[tuple] = []
MIN_HALVED = Fraction(1, 32)  # Ctrl − del tastierino non accorcia sotto la biscroma
SNAPS = [("1/1", Fraction(1)), ("1/2", Fraction(1, 2)), ("1/4", Fraction(1, 4)), ("1/8", Fraction(1, 8)),
         ("1/16", Fraction(1, 16)), ("1/32", Fraction(1, 32)), ("Off", None)]
DEFAULT_SNAP = 3  # 1/8
FREE_GRID = Fraction(1, 128)  # Snap Off: posizioni libere (su 1/128)


def is_dotted(duration: Fraction) -> bool:
    """Durata puntata: 3/2 di un valore semplice (1/2^k di semibreve), es. 3/8 = semiminima puntata."""
    base = duration / 3
    return base.numerator == 1 and base.denominator & (base.denominator - 1) == 0


class NoteEditing:
    """Mixin dei comandi sulle note (vedi la docstring del modulo)."""

    def init_note_editing(self) -> None:
        self.selection: list[Note] = []
        self._fail: list[tuple[Fraction, Fraction, int]] = []  # Duplicate/Paste falliti: dove (in rosso)
        self._fail_timer = 0
        self._preview_key: int | None = None  # nota che sta suonando (ascolto)
        self._preview_track: Track | None = None

    @property
    def unit(self) -> Fraction:
        """Passo dell'aggancio (e durata minima di una nota)."""
        return self.snap or FREE_GRID

    def snapped(self, pos: Fraction, how: str = "round", step: Fraction | None = None) -> Fraction:
        """Posizione agganciata alla griglia dello Snap (o di `step`), contata dall'inizio
        della misura."""
        step = step or self.unit
        meters = self.doc.song.meters
        start = meters.bar_start(meters.bar_at(max(pos, Fraction(0))))
        steps = (pos - start) / step
        n = {"round": round(steps), "floor": steps.__floor__(), "ceil": steps.__ceil__()}[how]
        return start + n * step

    def _track_of_segment(self, seg: Segment) -> Track | None:
        return next((t for t in self.edit_tracks if seg in t.segments), None)

    def _tracks_of(self, placed: list[PlacedNote]) -> list[Track]:
        tracks = {id(t): t for p in placed if (t := self._track_of_segment(p.segment)) is not None}
        return list(tracks.values())

    def note_key(self, name: str, ctrl: bool) -> bool:
        """Tasti dei comandi sulle note (gli stessi del menu Notes). True se gestito."""
        command = {
            (False, "Up"): lambda: self.semitone(1), (False, "Down"): lambda: self.semitone(-1),
            (True, "Up"): lambda: self.octave(1), (True, "Down"): lambda: self.octave(-1),
            (False, "Left"): lambda: self.move_step(-1), (False, "Right"): lambda: self.move_step(1),
            (True, "KP_Add"): self.double_lengths, (True, "KP_Subtract"): self.halve_lengths,
            (True, "period"): self.toggle_dot, (True, "KP_Decimal"): self.toggle_dot,
        }.get((ctrl, name))
        if command is None:
            return False
        command()
        return True

    def segment_at(self, track: Track, pos: Fraction) -> Segment | None:
        """Segmento modificabile (non clone) della traccia che contiene la posizione."""
        for seg in track.segments:
            a, b = self.doc.song.segment_span(seg)
            if a <= pos < b and seg.clone_of is None:
                return seg
        return None

    def _tracks_gone(self) -> bool:
        """Una traccia dell'editor non è più nel brano (Delete Track): la scheda sta per
        essere chiusa dalla finestra, intanto non si ricalcola nulla (i gruppi sono usciti)."""
        alive = {id(t) for t in self.doc.song.tracks}
        return any(id(t) not in alive for t in self.edit_tracks)

    def _group(self, seg: Segment):
        return self.doc.song.groups[seg.group_id]

    def _fits(self, seg: Segment, start: Fraction, end: Fraction, midi: int) -> bool:
        """La nota (posizioni assolute) sta dentro il segmento e nell'estensione MIDI.
        2026-11-20: sulla batteria conta solo l'attacco (la durata scritta è fissa e al play
        non conta): prima nell'ultimo quarto del segmento non si potevano mettere colpi."""
        a, b = self.doc.song.segment_span(seg)
        if self.is_drums:
            return a <= start < b and end > start and 0 <= midi <= 127
        return a <= start and end <= b and end > start and 0 <= midi <= 127

    def _overlap(self, q: PlacedNote, start: Fraction, end: Fraction) -> bool:
        """La nota `q` occupa il tratto [start, end); sulla batteria solo se il colpo è nello
        stesso punto (due colpi vicini dello stesso suono non si sovrappongono)."""
        if self.is_drums:
            return q.start == start
        return q.start < end and start < q.end

    def _audition(self, midi: int | None, velocity: int = 0, track: Track | None = None) -> None:
        """Suona `midi` sulla traccia (fermando quella di prima); None = ferma."""
        track = track or self.edit_tracks[0]
        if midi == self._preview_key and track is self._preview_track:
            return
        if self._preview_key is not None:
            self.transport.preview(self._preview_track.id, self._preview_key, None)
        self._preview_key = midi if midi is not None and 0 <= midi <= 127 else None
        self._preview_track = track
        if self._preview_key is not None:
            self.transport.preview(track.id, self._preview_key, velocity)
        self.redraw()  # tasto illuminato (tastiera della griglia)

    def _note_start(self, seg: Segment, pos: Fraction) -> Fraction:
        """Da posizione assoluta a inizio della nota nel suo gruppo."""
        return pos - self.doc.song.segment_span(seg)[0] + seg.trim

    def _split(self, p: PlacedNote, cut: Fraction) -> None:
        if not p.start < cut < p.end:
            self.on_message("There's already a note boundary here: nothing to split.")
            return
        n, first = p.note, cut - p.start
        second = n.copy(start=n.start + first, duration=n.duration - first)
        group = self._group(p.segment)
        self.doc.execute(Compound([EditNotes([(group, n, {"duration": first, "tie_next": False})]),
                                   AddNotes([(group, second)])], "Split note"))

    def _delete(self, notes: list[Note]) -> None:
        items = [(self._group(self._by_note[id(n)].segment), n) for n in notes if id(n) in self._by_note]
        if items:
            self.doc.execute(DeleteNotes(items))
        self.selection = [n for n in self.selection if n not in notes]

    def _selected_placed(self) -> list[PlacedNote]:
        return [self._by_note[id(n)] for n in self.selection if id(n) in self._by_note]

    def duplicate_selection(self) -> None:
        """Grid > Duplicate: copia le note selezionate subito dopo la fine della selezione,
        mantenendo le distanze, nel loro segmento. Se non entrano: rosso e messaggio."""
        placed = self._selected_placed()
        if not placed:
            self.on_message("No notes selected.")
            return
        shift = max(p.end for p in placed) - min(p.start for p in placed)
        if any(not self._fits(p.segment, p.start + shift, p.end + shift, p.midi) for p in placed):
            self.on_message("The copies don't fit in the segment: Duplicate cancelled.")
            self._flash_fail([(p.start + shift, p.end + shift, p.midi) for p in placed])
            return
        clash = self._same_pitch_clashes([(self._track_of_segment(p.segment), p.start + shift, p.end + shift, p.midi)
                                          for p in placed])
        if clash:
            self.on_message("There's already a note of the same pitch there: Duplicate cancelled.")
            self._flash_fail(clash)
            return
        items = []
        for p in placed:
            n = p.note
            items.append((self._group(p.segment), n.copy(start=n.start + shift)))
        self.doc.execute(AddNotes(items))
        self.selection = [n for _g, n in items]
        self.redraw()

    def merge_selection(self) -> None:
        """Grid > Merge: le note selezionate (stessa altezza, stesso segmento) diventano una
        nota sola, dal primo inizio all'ultima fine."""
        placed = sorted(self._selected_placed(), key=lambda p: p.start)
        if len(placed) < 2:
            self.on_message("Select at least two notes to merge.")
            return
        if len({p.midi for p in placed}) > 1:
            self.on_message("Merge only joins notes of the same pitch.")
            return
        if len({id(p.segment) for p in placed}) > 1:
            self.on_message("Merge only joins notes in the same segment.")
            return
        first, group = placed[0], self._group(placed[0].segment)
        end = max(p.end for p in placed)
        last = max(placed, key=lambda p: p.end)
        # 2026-11-20: la nota unita non deve coprire note della stessa altezza non selezionate in mezzo
        clash = self._same_pitch_clashes([(self._track_of_segment(first.segment), first.start, end, first.midi)],
                                         ignore={id(p.note) for p in placed})
        if clash:
            self.on_message("A note of the same pitch lies in between: Merge cancelled.")
            self._flash_fail(clash)
            return
        self.doc.execute(Compound([
            EditNotes([(group, first.note, {"duration": end - first.start, "tie_next": last.note.tie_next})]),
            DeleteNotes([(group, p.note) for p in placed[1:]]),
        ], "Merge notes"))
        self.selection = [first.note]
        self.redraw()

    def quantize_selection(self, step: Fraction | None = None, mode: str = START_LENGTH) -> None:
        """Notes > Quantize: allinea le note selezionate alla griglia (`step`, di default lo
        Snap). Restano nel loro segmento; se una nota finisce sopra la successiva della
        stessa altezza, la prima viene accorciata fin dove inizia l'altra."""
        placed = self._selected_placed()
        if not placed:
            self.on_message("No notes selected.")
            return
        song = self.doc.song
        step = step or self.unit
        spans: dict[int, tuple[PlacedNote, Fraction, Fraction]] = {}
        for p in placed:
            start, end = quantize_span(p.start, p.end, step, song.meters, mode)
            a, b = song.segment_span(p.segment)  # dentro il segmento
            # 2026-11-20: il limite dell'inizio del segmento si applica per ultimo; prima, con
            # un segmento più corto del passo, b - step cadeva prima del segmento e la nota spariva.
            start = max(min(start, b - step), a)
            end = min(max(end, start + min(step, b - start)), b)
            spans[id(p.note)] = (p, start, end)
        # sovrapposizioni sulla stessa altezza: la nota prima finisce dove inizia la dopo
        moving = set(spans)
        others = [(q, q.start, q.end) for t in self._tracks_of(placed) for q in song.placed_notes(t)
                  if id(q.note) not in moving]
        by_pitch: dict[tuple, list] = {}  # (segmento della traccia → traccia, altezza)
        for p, start, end in list(spans.values()) + others:
            by_pitch.setdefault((id(self._track_of_segment(p.segment)), p.midi), []).append([p, start, end])
        # 2026-11-20: due note della stessa altezza che finirebbero nello stesso punto (es. una nota
        # e il suo abbellimento) non si possono accorciare l'una sull'altra: niente Quantize,
        # messaggio e rosso, invece di lasciare un doppione nascosto
        for notes in by_pitch.values():
            notes.sort(key=lambda n: n[1])
        doubles = [(nxt[1], max(cur[2], nxt[2]), nxt[0].midi)
                   for notes in by_pitch.values() for cur, nxt in zip(notes, notes[1:])
                   if cur[1] == nxt[1] and (id(cur[0].note) in moving or id(nxt[0].note) in moving)]
        if doubles:
            self.on_message("Two notes of the same pitch would start at the same point: Quantize cancelled.")
            self._flash_fail(doubles)
            return
        for notes in by_pitch.values():
            notes.sort(key=lambda n: n[1])
            for cur, nxt in zip(notes, notes[1:]):
                if cur[2] > nxt[1] > cur[1] and cur[0].segment.clone_of is None:
                    cur[2] = nxt[1]
                    spans[id(cur[0].note)] = (cur[0], cur[1], cur[2])
        changes = []
        for p, start, end in spans.values():
            values = {}
            if start != p.start:
                values["start"] = self._note_start(p.segment, start)
            if end - start != p.duration:
                values["duration"] = end - start
            if values:
                changes.append((self._group(p.segment), p.note, values))
        if changes:
            self.doc.execute(EditNotes(changes))
            self.on_message(f"Quantized {len(placed)} notes.", error=False)
        self.redraw()

    def _flash_fail(self, spans: list) -> None:
        """Mostra per un attimo, in rosso, dove sarebbero finite le note."""
        self._fail = spans
        if self._fail_timer:
            GLib.source_remove(self._fail_timer)

        def clear() -> bool:
            self._fail, self._fail_timer = [], 0
            self.redraw()
            return False

        self._fail_timer = GLib.timeout_add(1200, clear)
        self.redraw()

    def semitone(self, direction: int) -> None:
        """↑ / ↓: ±1 semitono (salta le altezze occupate)."""
        self.shift_selection(Fraction(0), direction)

    def octave(self, direction: int) -> None:
        """Ctrl+↑ / Ctrl+↓: ±1 ottava."""
        self.shift_selection(Fraction(0), 12 * direction)

    def move_step(self, direction: int) -> None:
        """← / →: ±1 passo di Snap, senza agganciare alla griglia."""
        self.shift_selection(self.unit * direction, 0)

    def double_lengths(self) -> None:
        """Ctrl + del tastierino: durata doppia."""
        self.change_durations(lambda d: d * 2)

    def halve_lengths(self) -> None:
        """Ctrl − del tastierino: metà durata, non sotto 1/32 (una nota già più corta resta com'è)."""
        self.change_durations(lambda d: max(d / 2, min(d, MIN_HALVED)))

    def toggle_dot(self) -> None:
        """Ctrl+.: punto (allunga del 50%); su una nota già puntata lo toglie."""
        self.change_durations(lambda d: d * 2 / 3 if is_dotted(d) else d * Fraction(3, 2))

    def shift_selection(self, dpos: Fraction, dmidi: int) -> None:
        """Frecce: sposta le note selezionate se c'è spazio. Le note devono restare nel loro
        segmento, fra 0 e 127, e non sovrapporsi ad altre note della stessa altezza.
        Se il posto è occupato saltano al primo libero nella stessa direzione (si può
        "scavalcare" una nota): su/giù di semitono (o ottava), sinistra/destra di passo di Snap."""
        placed = self._selected_placed()
        if not placed:
            return
        if dmidi:
            step, dmidi = dmidi, 0
            while True:
                dmidi += step
                if not all(0 <= p.midi + dmidi <= 127 for p in placed):
                    self.on_message("No room: the notes would go out of range (0–127).")
                    return
                if self._free(placed, dpos, dmidi):
                    break
        else:
            # di esattamente un passo di Snap, senza quantizzare: una nota fuori griglia
            # resta fuori griglia dello stesso scarto
            step, dpos = dpos, Fraction(0)
            while True:
                dpos += step
                if not all(self._fits(p.segment, p.start + dpos, p.end + dpos, p.midi) for p in placed):
                    self.on_message("No room: the notes would leave the segment.")
                    return
                if self._free(placed, dpos, 0):
                    break
        self.doc.execute(EditNotes([(self._group(p.segment), p.note, self._moved(p, dpos, p.midi + dmidi))
                                    for p in placed]))
        if dmidi and len(placed) == 1:
            self._audition(placed[0].midi + dmidi, placed[0].note.velocity,
                           self._track_of_segment(placed[0].segment))  # si sente la nuova altezza
            GLib.timeout_add(250, lambda: self._audition(None) or False)

    def selected_notes(self) -> list[Note]:
        return [p.note for p in sorted(self._selected_placed(), key=lambda p: (p.start, p.midi))]

    def set_velocities(self, value: int, spread: int = 0) -> None:
        """Notes > Velocity…: velocity fissa, con `spread` > 0 umanizzata (±spread a caso)."""
        placed = self._selected_placed()
        if not placed:
            return
        values = velocities(value, len(placed), spread)
        self.doc.execute(EditNotes([(self._group(p.segment), p.note, {"velocity": v})
                                    for p, v in zip(placed, values) if p.note.velocity != v]))
        self.redraw()

    def toggle_slur(self) -> None:
        """Notes > Articulations > Slur: arco dalla prima all'ultima nota selezionata (stesso
        segmento). Se le note selezionate sono già sotto un arco, lo toglie; un arco nuovo
        sostituisce quelli che toccano le stesse note."""
        placed = sorted(self._selected_placed(), key=lambda p: (p.start, p.midi))
        if not placed or self.is_drums:
            return
        seg = placed[0].segment
        if any(p.segment is not seg for p in placed):
            self.on_message("A slur only joins notes in the same segment.")
            return
        group = self._group(seg)
        starts = {n.id: n.start for n in group.notes}
        lo, hi = placed[0].note.start, placed[-1].note.start
        inside = [(a, b) for a, b in group.slurs if a in starts and b in starts]
        covering = [s for s in inside if starts[s[0]] <= lo and hi <= starts[s[1]]]
        if covering:  # già sotto un arco: lo toglie
            self.doc.execute(SetSlurs(group, [s for s in group.slurs if s not in covering]))
        else:
            if lo == hi:
                self.on_message("Select at least two notes (at different times) for the slur.")
                return
            keep = [s for s in group.slurs if s not in inside or starts[s[1]] < lo or starts[s[0]] > hi]
            self.doc.execute(SetSlurs(group, keep + [(placed[0].note.id, placed[-1].note.id)]))
        self.redraw()

    def toggle_tie(self) -> None:
        """Notes > Length > Tie / Untie: lega ogni nota selezionata alla successiva della stessa
        altezza che inizia esattamente dove lei finisce (suonano come una nota sola). Se le
        note selezionate sono già tutte legate, toglie le legature."""
        placed = self._selected_placed()
        if not placed:
            return
        tied = [p for p in placed if p.note.tie_next]
        selected = {id(p.note) for p in placed}
        pairs = [pair for t in self._tracks_of(placed)
                 for pair in tie_targets(list(self.doc.song.placed_notes(t)), selected)]
        if not pairs and not tied:
            self.on_message("No note of the same pitch right after the selected ones: nothing to tie.")
            return
        if pairs and any(not p.note.tie_next for p, _n in pairs):  # c'è qualcosa da legare
            changes = [(self._group(p.segment), p.note, {"tie_next": True}) for p, _n in pairs if not p.note.tie_next]
        else:  # già tutto legato: slega
            changes = [(self._group(p.segment), p.note, {"tie_next": False}) for p in tied]
        self.doc.execute(EditNotes(changes))
        self.redraw()

    @property
    def is_drums(self) -> bool:
        """Una traccia di batteria fra quelle dell'editor: niente staccato né slur."""
        return any(t.instrument.is_percussion or t.channel == 10 for t in self.edit_tracks)

    def toggle_staccato(self) -> None:
        """Notes > Articulations > Staccato: lo mette alle note selezionate; se ce l'hanno
        già tutte, lo toglie. La durata scritta non cambia (al play suonano la metà)."""
        placed = self._selected_placed()
        if not placed or self.is_drums:
            return
        on = not all(p.note.staccato for p in placed)
        self.doc.execute(EditNotes([(self._group(p.segment), p.note, {"staccato": on})
                                    for p in placed if p.note.staccato != on]))
        self.redraw()

    def detect_staccato(self) -> None:
        """Notes > Detect > Staccato: fra le note selezionate, quelle che durano esattamente
        metà dello spazio fino all'attacco successivo (o alla fine del segmento) tornano
        alla durata scritta e prendono il segno di staccato. Gli inizi non si spostano."""
        placed = self._selected_placed()
        if not placed or self.is_drums:
            return
        changes = []
        by_segment: dict[int, list[PlacedNote]] = {}
        for p in placed:
            by_segment.setdefault(id(p.segment), []).append(p)
        for group_placed in by_segment.values():
            seg = group_placed[0].segment
            group = self._group(seg)
            found = detect_staccato(group.notes, [p.note for p in group_placed], end=seg.trim + seg.length)
            changes += [(group, note, {"duration": written, "staccato": True}) for note, written in found]
        if changes:
            self.doc.execute(EditNotes(changes))
        self.on_message(f"Staccato detected on {len(changes)} notes." if changes else "No staccato detected.",
                        error=False)
        self.redraw()

    def set_lengths(self, length: Fraction) -> None:
        """Palette dei valori: le note selezionate prendono questa durata (inizio fermo),
        solo se c'è spazio (vedi change_durations)."""
        self.change_durations(lambda _d: length)

    def change_durations(self, new_duration) -> None:
        """Cambia la durata delle note selezionate (inizio fermo), solo se c'è spazio: ogni
        nota resta nel suo segmento e non tocca altre note della stessa altezza."""
        placed = self._selected_placed()
        if not placed:
            return
        ends = {id(p.note): p.start + new_duration(p.duration) for p in placed}
        selected = set(ends)
        others = [q for t in self._tracks_of(placed) for q in self.doc.song.placed_notes(t)
                  if id(q.note) not in selected]
        spans = [(p, p.start, ends[id(p.note)]) for p in placed]
        for p, start, end in spans:
            if not self._fits(p.segment, start, end, p.midi):
                self.on_message("No room: the note would leave its segment.")
                return
            track = self._track_of_segment(p.segment)
            near = [(q.start, q.end) for q in others if q.midi == p.midi and self._track_of_segment(q.segment) is track]
            near += [(s, e) for q, s, e in spans if q is not p and q.midi == p.midi
                     and self._track_of_segment(q.segment) is track]
            if any(s < end and start < e for s, e in near):
                self.on_message("No room: the note would touch another note of the same pitch.")
                return
        self.doc.execute(EditNotes([(self._group(p.segment), p.note, {"duration": end - start})
                                    for p, start, end in spans if end - start != p.duration]))

    def _same_pitch_clashes(self, spans: list[tuple[Track, Fraction, Fraction, int]],
                            ignore: set[int] | None = None) -> list[tuple[Fraction, Fraction, int]]:
        """Le note nuove o modificate (traccia, inizio, fine, altezza) che finirebbero sopra una
        nota della stessa altezza della stessa traccia; `ignore`: id delle note che cambiano.
        2026-11-20: usato da Duplicate, Merge e dai gesti del mouse, che prima non controllavano
        la stessa altezza (lo facevano solo frecce, Paste e Composer)."""
        ignore = ignore or set()
        existing: dict[int, list[PlacedNote]] = {}
        clashes = []
        for track, start, end, midi in spans:
            if track is None:
                continue
            # list(): placed_notes è un iteratore, consumato dal primo any() (2026-11-20)
            notes = existing.setdefault(id(track), list(self.doc.song.placed_notes(track)))
            if any(q.midi == midi and self._overlap(q, start, end) and id(q.note) not in ignore for q in notes):
                clashes.append((start, end, midi))
        return clashes

    def _free(self, placed: list[PlacedNote], dpos: Fraction, dmidi: int, targets: dict | None = None) -> bool:
        """Le note spostate stanno nel loro segmento e non toccano altre note della stessa altezza.
        `targets` (id della nota → nuova altezza) sostituisce `dmidi` quando ogni nota ha la sua
        (spostamento verticale per righe/spazi nella notazione)."""
        moving = {id(p.note) for p in placed}
        others = [q for t in self._tracks_of(placed) for q in self.doc.song.placed_notes(t) if id(q.note) not in moving]
        for p in placed:
            start, end = p.start + dpos, p.end + dpos
            midi = targets[id(p.note)] if targets else p.midi + dmidi
            if not self._fits(p.segment, start, end, midi):
                return False
            track = self._track_of_segment(p.segment)
            if any(q.midi == midi and self._overlap(q, start, end) and self._track_of_segment(q.segment) is track
                   for q in others):
                return False
        return True

    def _moved(self, p: PlacedNote, dpos: Fraction, midi: int) -> dict:
        values = {}
        if dpos:
            values["start"] = p.note.start + dpos
        if midi != p.midi:
            values["midi"] = p.note.midi + (midi - p.midi)
            if p.note.spelling is not None:
                values["spelling"] = None  # la grafia forzata non vale più per la nuova altezza
        return values

    def transpose_selection(self, amount: int, diatonic: bool) -> bool:
        """Notes > Transpose: di `amount` semitoni, o di `amount` gradi della scala della
        tonalità in vigore dove sta ogni nota. False (con messaggio) se uscirebbe dall'estensione."""
        placed = self._selected_placed()
        if not placed:
            self.on_message("No notes selected.")
            return False
        song = self.doc.song
        targets = []
        for p in placed:
            if diatonic:
                key = song.keys.at(song.meters.bar_at(p.start))
                targets.append(diatonic_shift(p.midi, key, amount))
            else:
                targets.append(p.midi + amount)
        if not all(0 <= m <= 127 for m in targets):
            self.on_message("Cannot transpose: some notes would go out of the MIDI range (0–127).")
            return False
        changes = [(self._group(p.segment), p.note, self._moved(p, Fraction(0), m)) for p, m in zip(placed, targets)]
        changes = [c for c in changes if c[2]]
        if changes:
            self.doc.execute(EditNotes(changes))
        return True

    def copy_selection(self) -> bool:
        """Edit > Copy: le note selezionate negli appunti (distanze e altezze comprese)."""
        placed = sorted(self._selected_placed(), key=lambda p: (p.start, p.midi))
        if not placed:
            self.on_message("No notes selected.")
            return False
        origin = placed[0].start
        NOTE_CLIPBOARD[:] = [(p.start - origin, p.midi, p.note.copy()) for p in placed]
        self.on_message(f"Copied {len(placed)} notes.", error=False)
        return True

    def cut_selection(self) -> None:
        """Edit > Cut: copia e cancella le note selezionate."""
        if self.copy_selection():
            self.delete_selection()

    def paste(self) -> None:
        """Edit > Paste: incolla le note nel punto dato dalla vista (`paste_target`: il punto di play,
        in griglia e in notazione), con le loro altezze. Ogni nota va nel segmento che la
        contiene. Niente incolla (rosso e messaggio) se una nota non entra in un segmento
        modificabile o finirebbe sopra una nota della stessa altezza."""
        if not NOTE_CLIPBOARD:
            self.on_message("The note clipboard is empty.")
            return
        target = self.paste_target()  # (traccia, punto di play agganciato allo Snap)
        if target is None:
            return
        track, at = target
        items, spans = [], []
        for rel, midi, note in NOTE_CLIPBOARD:
            start = at + rel
            spans.append((start, start + note.duration, midi))
            seg = self.segment_at(track, start)
            if seg is None or not self._fits(seg, start, start + note.duration, midi):
                continue
            items.append((self._group(seg), note.copy(start=self._note_start(seg, start), midi=midi - seg.transpose)))
        if len(items) < len(NOTE_CLIPBOARD):
            self.on_message("The notes don't fit in a segment of the track here: paste cancelled.")
            self._flash_fail(spans)
            return
        existing = list(self.doc.song.placed_notes(track))  # niente note sopra altre della stessa altezza
        clash = [(s, e, m) for s, e, m in spans
                 if any(q.midi == m and self._overlap(q, s, e) for q in existing)]
        if clash:
            self.on_message("There's already a note of the same pitch here: paste cancelled.")
            self._flash_fail(clash)
            return
        self.doc.execute(AddNotes(items))
        self.selection = [n for _g, n in items]
        self.redraw()

    # --- Composer: accordi sulla linea rossa ---------------------------------------

    def insert_chord(self, tones: list, strum: str | None = None, symbol: str | None = None) -> bool:
        """Composer: inserisce le note dell'accordo (`tones`: oggetti con .midi e .spelling)
        nel punto d'inserimento della vista (`insert_point()`: traccia, inizio, durata),
        cioè sulla linea rossa; le si sente e la linea avanza alla fine dell'accordo.
        Solo dentro un segmento modificabile e senza sovrapporsi a note della stessa
        altezza: altrimenti messaggio e niente. Un solo passo di Ctrl+Z."""
        target = self.insert_point()
        if target is None:
            return False
        track, start, length = target
        song = self.doc.song
        seg = self.segment_at(track, start)
        existing = list(song.placed_notes(track))
        bad = seg is None or any(
            not self._fits(seg, start, start + length, t.midi)
            or any(q.midi == t.midi and self._overlap(q, start, start + length) for q in existing)
            for t in tones)
        if bad:
            self.on_message("The chord doesn't fit here (outside a segment or over other notes): not inserted.")
            self._flash_fail([(start, start + length, t.midi) for t in tones])
            return False
        group = self._group(seg)
        notes = [Note(self._note_start(seg, start), length, t.midi - seg.transpose, velocity=track.velocity,
                      spelling=t.spelling, strum=strum) for t in tones]
        commands = [AddNotes([(group, n) for n in notes])]
        if symbol:  # sigla d'accordo, solo dove l'accordo cambia (un solo passo di Ctrl+Z con le note)
            chords = place_symbol(group.chords, self._note_start(seg, start), symbol)
            if chords != sorted(group.chords):
                commands.append(SetChords(group, chords))
        self.doc.execute(commands[0] if len(commands) == 1 else Compound(commands, "Insert chord"))
        for t in tones:  # si sente l'accordo per un attimo
            self.transport.preview(track.id, t.midi, track.velocity)
        GLib.timeout_add(400, lambda: [self.transport.preview(track.id, t.midi, None) for t in tones] and False)
        self.transport.seek(song.tempos.seconds(start + length))
        self.redraw()
        return True

    def detect_chords(self) -> None:
        """Composer > Detect Chords: per ogni movimento, l'accordo formato dalle note
        selezionate che suonano in quel movimento diventa la sigla del loro segmento, solo
        dove l'accordo cambia (le sigle già presenti nel tratto vengono rifatte). Rivolti
        con la barra (C/E); niente sigla dove le note non formano un accordo."""
        placed = self._selected_placed()
        if not placed or self.is_drums:  # 2026-11-20: niente accordi sulla batteria (come staccato e slur)
            return
        song = self.doc.song
        by_segment: dict[int, list[PlacedNote]] = {}
        for p in placed:
            by_segment.setdefault(id(p.segment), []).append(p)
        commands, found = [], 0
        for notes in by_segment.values():
            seg = notes[0].segment
            group = self._group(seg)
            lo, hi = min(p.start for p in notes), max(p.end for p in notes)
            bar = song.meters.bar_at(lo)
            pos, detected = song.meters.bar_start(bar), []
            while pos < hi:
                beat = song.meters.at(song.meters.bar_at(pos)).beat
                sounding = [p.midi for p in notes if p.start < pos + beat and p.end > pos]
                flats = song.keys.at(song.meters.bar_at(pos)).fifths < 0
                name = detect_chord(sounding, flats=flats)
                if name:
                    detected.append((self._note_start(seg, max(pos, lo)), name))
                pos += beat
            a, b = self._note_start(seg, lo), self._note_start(seg, hi)
            kept = [(p, c) for p, c in group.chords if not a <= p < b]  # fuori dal tratto restano
            chords = dedupe_symbols(kept + detected)
            found += len(detected)
            if chords != sorted(group.chords):
                commands.append(SetChords(group, chords))
        if commands:
            self.doc.execute(commands[0] if len(commands) == 1 else Compound(commands, "Detect chords"))
        self.on_message(f"Chords detected on {found} beats." if found else "No chords detected.",
                        error=False)
        self.redraw()

    def symbol_at_insert_point(self) -> tuple | None:
        """Chord Symbol…: (gruppo, posizione nel gruppo, sigla esistente o "") sulla linea
        rossa, nel segmento modificabile che la contiene; None se lì non c'è un segmento."""
        track, start, _length = self.insert_point()
        seg = self.segment_at(track, start)
        if seg is None:
            return None
        group, pos = self._group(seg), self._note_start(seg, start)
        return group, pos, next((c for p, c in group.chords if p == pos), "")

    def set_symbol(self, text: str) -> bool:
        """Chord Symbol…: scrive (o cancella, se vuoto) la sigla sulla linea rossa, così com'è."""
        found = self.symbol_at_insert_point()
        if found is None:
            self.on_message("No editable segment here: no chord symbol.")
            return False
        group, pos, _old = found
        chords = [(p, c) for p, c in group.chords if p != pos] + ([(pos, text.strip())] if text.strip() else [])
        self.doc.execute(SetChords(group, chords))
        self.redraw()
        return True

    def select_all(self) -> None:
        """Edit > Select All (Ctrl+A): tutte le note modificabili dell'editor (non quelle dei cloni)."""
        self.selection = [p.note for p in self._placed if p.segment.clone_of is None]
        self.redraw()

    def delete_selection(self) -> None:
        """Tasto Canc: cancella le note selezionate."""
        if self.selection:
            self._delete(list(self.selection))
            self.redraw()

    def _open_note(self, hit: PlacedNote) -> None:
        """Doppio click su una nota: proprietà della nota."""
        from .note_dialog import NoteDialog

        self.selection = [hit.note]
        NoteDialog(self.get_root(), self.doc, hit, self._group(hit.segment)).present()
