"""Documento aperto: brano + undo/redo a comandi + notifiche di modifica.

Le viste si iscrivono con `subscribe` e si ridisegnano a ogni modifica.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import replace
from typing import Any, Callable

from .key import Key
from .model import Group, Note, Segment, Song, Track, remap_slurs


def _restore_groups(song: Song, groups: list[Group], order: list[str]) -> None:
    """Undo: rimette i gruppi tolti al loro posto nell'ordine di `order` (le chiavi di
    song.groups prima del comando). 2026-11-20: prima tornavano in fondo, e dopo un Undo di
    Delete, Split o Delete Track cambiavano l'ordine nel file salvato e i numeri dei cloni."""
    for g in groups:
        song.groups[g.id] = g
    current = dict(song.groups)
    song.groups.clear()  # stesso dizionario: altri possono tenerne il riferimento
    for gid in order:  # prima nell'ordine di allora...
        if gid in current:
            song.groups[gid] = current.pop(gid)
    song.groups.update(current)  # ...poi quelli nati dopo


def _adopt_orphan_clones(song: Song, removed: list[Segment]) -> list[tuple[Segment, str, str]]:
    """Cloni il cui originale non c'è più (cancellato, tagliato con Split o Merge): il primo
    diventa l'originale (con l'etichetta di quello sparito, se non ne ha una), gli altri suoi
    cloni. `removed`: i segmenti appena tolti. Restituisce [(segmento, clone_of ed etichetta
    di prima)] per l'Undo. 2026-11-20: prima restavano cloni di un segmento sparito, grigi e non
    modificabili (es. Duplicate e poi Delete del primo)."""
    ids = {seg.id for t in song.tracks for seg in t.segments}
    labels = {seg.id: seg.label for seg in removed}
    heirs: dict[str, str | None] = {}  # originale sparito → id del clone promosso
    changed = []
    for track in song.tracks:
        for seg in track.segments:
            if seg.clone_of is None or seg.clone_of in ids:
                continue
            changed.append((seg, seg.clone_of, seg.label))
            heir = heirs.get(seg.clone_of)
            if heir is None:
                heirs[seg.clone_of] = seg.id
                seg.label = seg.label or labels.get(seg.clone_of, "")
            seg.clone_of = heir
    return changed


def _restore_clones(changed: list[tuple[Segment, str, str]]) -> None:
    """Undo di _adopt_orphan_clones."""
    for seg, clone_of, label in changed:
        seg.clone_of, seg.label = clone_of, label


class Command(ABC):
    label: str = ""

    @abstractmethod
    def do(self, song: Song) -> None: ...

    @abstractmethod
    def undo(self, song: Song) -> None: ...

    def is_noop(self) -> bool:
        """Il comando non cambierebbe nulla (es. Velocity con lo stesso valore): Document
        non lo esegue, così non nasce un passo di Undo vuoto né il brano risulta modificato.
        I comandi che non sanno dirlo rispondono False (2026-11-20)."""
        return False


class SetAttribute(Command):
    """Cambia un attributo di un oggetto del modello (es. chiave o strumento di una traccia)."""

    def __init__(self, target: Any, attr: str, value: Any, label: str = "") -> None:
        self.target, self.attr, self.value = target, attr, value
        self.label = label or f"Set {attr}"
        self._old: Any = None

    def do(self, song: Song) -> None:
        self._old = getattr(self.target, self.attr)
        setattr(self.target, self.attr, self.value)

    def undo(self, song: Song) -> None:
        setattr(self.target, self.attr, self._old)

    def is_noop(self) -> bool:
        return getattr(self.target, self.attr) == self.value


class Compound(Command):
    """Più comandi come un solo passo di annulla/ripeti."""

    def __init__(self, commands: list[Command], label: str = "") -> None:
        self.commands = list(commands)
        self.label = label or "Changes"

    def do(self, song: Song) -> None:
        for cmd in self.commands:
            cmd.do(song)

    def undo(self, song: Song) -> None:
        for cmd in reversed(self.commands):
            cmd.undo(song)

    def is_noop(self) -> bool:
        return all(cmd.is_noop() for cmd in self.commands)


class MoveSegments(Command):
    """Sposta segmenti: nuova posizione d'inizio (misura + spostamento sul movimento)
    ed eventualmente un'altra traccia.
    moves = [(segmento, traccia di partenza, traccia di arrivo, nuova misura, nuovo spostamento)]."""

    label = "Move segments"

    def __init__(self, moves: list) -> None:
        self.moves = list(moves)
        self._old = [(seg.start_bar, seg.offset) for seg, *_ in self.moves]

    @staticmethod
    def fits(song: Song, moves: list) -> bool:
        """True se nessun segmento spostato si sovrappone ad altri nella traccia di arrivo."""
        moving = {id(seg) for seg, *_ in moves}
        placed: dict[int, list] = {}
        for seg, _src, dst, bar, offset in moves:
            if bar < 0:
                return False
            if id(dst) not in placed:
                placed[id(dst)] = [song.segment_span(s) for s in dst.segments if id(s) not in moving]
            start = song.meters.bar_start(bar) + offset
            span = (start, start + seg.length)
            if any(a < span[1] and span[0] < b for a, b in placed[id(dst)]):
                return False
            placed[id(dst)].append(span)
        return True

    def do(self, song: Song) -> None:
        for seg, src, dst, bar, offset in self.moves:
            src.segments.remove(seg)
            seg.start_bar, seg.offset = bar, offset
            dst.segments.append(seg)
            dst.segments.sort(key=lambda s: (s.start_bar, s.offset))

    def undo(self, song: Song) -> None:
        for (seg, src, dst, _bar, _off), (old_bar, old_off) in zip(reversed(self.moves), reversed(self._old)):
            dst.segments.remove(seg)
            seg.start_bar, seg.offset = old_bar, old_off
            src.segments.append(seg)
            src.segments.sort(key=lambda s: (s.start_bar, s.offset))


class SplitSegment(Command):
    """Divide un segmento nel punto `cut` (semibrevi dall'inizio del segmento).

    Le due parti hanno ciascuna un gruppo nuovo (la parte divisa si scollega da
    eventuali copie collegate); le note a cavallo del taglio diventano due note
    legate, così il suono non cambia."""

    label = "Split segment"

    def __init__(self, track: Track, seg: Segment, cut) -> None:
        if not 0 < cut < seg.length:
            raise ValueError("the cut must be inside the segment, not on its edges")
        self.track, self.seg, self.cut = track, seg, cut
        self.parts: tuple[Segment, Segment] | None = None
        self._groups: tuple[Group, Group] | None = None
        self._removed_group: Group | None = None

    def do(self, song: Song) -> None:
        seg, group = self.seg, song.groups[self.seg.group_id]
        if self._groups is None:  # prima esecuzione: costruisce le due parti
            cut = self.cut
            left, right = [], []
            left_map, right_map = {}, {}  # id vecchio → copia (per ricollegare gli slur)
            for n in group.notes:
                start = n.start - seg.trim  # posizione dentro il segmento
                if start < 0 or start >= seg.length:
                    continue  # nascosta dal Resize: non entra nelle parti
                end = start + n.duration
                if end <= cut:
                    left.append(left_map.setdefault(n.id, n.copy(start=start)))
                elif start >= cut:
                    right.append(right_map.setdefault(n.id, n.copy(start=start - cut)))
                else:  # a cavallo del taglio: due note legate
                    left.append(left_map.setdefault(n.id, n.copy(start=start, duration=cut - start, tie_next=True)))
                    right.append(right_map.setdefault(n.id, n.copy(start=0 * cut, duration=end - cut)))
            start, _end = song.segment_span(seg)
            bar2, off2 = song.split_position(start + cut)
            # gli slur interi da una parte restano; quelli a cavallo del taglio si perdono
            g1 = Group(f"{group.name} (1)", group.length_bars, left, slurs=remap_slurs(group.slurs, left_map),
                       chords=[(p - seg.trim, c) for p, c in group.chords if 0 <= p - seg.trim < cut],
                       style=group.style)
            g2 = Group(f"{group.name} (2)", group.length_bars, right, slurs=remap_slurs(group.slurs, right_map),
                       chords=[(p - seg.trim - cut, c) for p, c in group.chords if cut <= p - seg.trim < seg.length],
                       style=group.style)
            g1.events = [replace(e, start=e.start - seg.trim) for e in group.events if 0 <= e.start - seg.trim < cut]
            g2.events = [replace(e, start=e.start - seg.trim - cut) for e in group.events
                         if cut <= e.start - seg.trim < seg.length]
            if group.chord_events:  # traccia accordi: anche la parte destra parte con l'accordo in vigore
                inside = sorted((replace(e, pos=e.pos - seg.trim) for e in group.chord_events
                                 if 0 <= e.pos - seg.trim < seg.length), key=lambda e: e.pos)
                g1.chord_events = [e for e in inside if e.pos < cut]
                g2.chord_events = [replace(e, pos=e.pos - cut) for e in inside if e.pos >= cut]
                if g1.chord_events and not any(e.pos == 0 for e in g2.chord_events):
                    g2.chord_events.insert(0, replace(g1.chord_events[-1], pos=0 * cut))
            s1 = Segment(g1.id, seg.start_bar, cut, seg.transpose, seg.offset, label=seg.label, **seg.arrangement())
            s2 = Segment(g2.id, bar2, seg.length - cut, seg.transpose, off2, label=seg.label, **seg.arrangement())
            self._groups, self.parts = (g1, g2), (s1, s2)
        g1, g2 = self._groups
        s1, s2 = self.parts
        i = self.track.segments.index(seg)
        self.track.segments[i:i + 1] = [s1, s2]
        self._order = list(song.groups)  # ordine dei gruppi per l'Undo
        song.groups[g1.id], song.groups[g2.id] = g1, g2
        still_used = any(s.group_id == group.id for t in song.tracks for s in t.segments)
        self._removed_group = None if still_used else song.groups.pop(group.id)
        self._adopted = _adopt_orphan_clones(song, [self.seg])

    def undo(self, song: Song) -> None:
        _restore_clones(self._adopted)
        s1, s2 = self.parts
        g1, g2 = self._groups
        i = self.track.segments.index(s1)
        self.track.segments[i:i + 2] = [self.seg]
        del song.groups[g1.id], song.groups[g2.id]
        if self._removed_group is not None:
            _restore_groups(song, [self._removed_group], self._order)


class ResizeSegment(Command):
    """Cambia inizio e/o fine di un segmento (posizioni nel brano, semibrevi).
    Il contenuto resta al suo posto nel tempo: allungando compare altro contenuto
    del gruppo (o spazio vuoto), accorciando una parte viene nascosta (non cancellata)."""

    label = "Resize segment"

    def __init__(self, seg: Segment, start, end) -> None:
        if not end > start:
            raise ValueError("invalid length")
        self.seg, self.start, self.end = seg, start, end
        self._old: tuple | None = None

    def do(self, song: Song) -> None:
        seg = self.seg
        old_start, _old_end = song.segment_span(seg)
        self._old = (seg.start_bar, seg.offset, seg.length, seg.trim)
        seg.start_bar, seg.offset = song.split_position(self.start)
        seg.trim += self.start - old_start
        seg.length = self.end - self.start

    def undo(self, song: Song) -> None:
        self.seg.start_bar, self.seg.offset, self.seg.length, self.seg.trim = self._old


class DeleteSegments(Command):
    """Cancella segmenti; i gruppi che restano senza segmenti escono dal brano."""

    label = "Delete segments"

    def __init__(self, items: list) -> None:
        self.items = list(items)  # [(traccia, segmento)]
        self._where: list[tuple[Track, Segment, int]] = []
        self._groups: list[Group] = []

    def do(self, song: Song) -> None:
        self._where = []
        for track, seg in self.items:
            if seg in track.segments:
                self._where.append((track, seg, track.segments.index(seg)))
                track.segments.remove(seg)
        used = {s.group_id for t in song.tracks for s in t.segments}
        self._order = list(song.groups)  # ordine dei gruppi per l'Undo
        self._groups = [song.groups.pop(gid) for _t, seg, _i in self._where
                        for gid in [seg.group_id] if gid not in used and gid in song.groups]
        self._adopted = _adopt_orphan_clones(song, [seg for _t, seg, _i in self._where])

    def undo(self, song: Song) -> None:
        _restore_clones(self._adopted)
        _restore_groups(song, self._groups, self._order)
        for track, seg, i in reversed(self._where):
            track.segments.insert(i, seg)


class AddSegments(Command):
    """Aggiunge segmenti: con un gruppo nuovo (Incolla) o con un gruppo già esistente
    (copia collegata, Duplica)."""

    label = "Paste segments"

    def __init__(self, items: list) -> None:
        self.items = list(items)  # [(traccia, segmento, gruppo)]

    @staticmethod
    def fits(song: Song, items: list) -> bool:
        """True se i segmenti nuovi non si sovrappongono a quelli esistenti né fra loro."""
        spans: dict[int, list] = {}
        for track, seg, _group in items:
            if id(track) not in spans:
                spans[id(track)] = [song.segment_span(s) for s in track.segments]
            start = song.meters.bar_start(seg.start_bar) + seg.offset
            span = (start, start + seg.length)
            if any(a < span[1] and span[0] < b for a, b in spans[id(track)]):
                return False
            spans[id(track)].append(span)
        return True

    def do(self, song: Song) -> None:
        self._new_groups = []  # solo i gruppi che non c'erano (le copie collegate riusano quello esistente)
        for track, seg, group in self.items:
            if group.id not in song.groups:
                song.groups[group.id] = group
                self._new_groups.append(group.id)
            track.segments.append(seg)
            track.segments.sort(key=lambda s: (s.start_bar, s.offset))

    def undo(self, song: Song) -> None:
        for track, seg, _group in self.items:
            track.segments.remove(seg)
        for gid in self._new_groups:
            song.groups.pop(gid, None)


class UnlinkSegments(Command):
    """Rende indipendenti dei cloni: ognuno riceve una copia propria delle note."""

    label = "Make independent"

    def __init__(self, segments: list[Segment]) -> None:
        self.segments = [s for s in segments if s.clone_of is not None]
        self._old: list[tuple[str, str | None, str]] = []
        self._groups: list[Group] = []

    def do(self, song: Song) -> None:
        if not self._groups:  # prima esecuzione: crea le copie delle note
            for seg in self.segments:
                g = song.groups[seg.group_id]
                self._groups.append(g.copy())
        self._old = []
        for seg, group in zip(self.segments, self._groups):
            original = song.segment_by_id(seg.clone_of)
            self._old.append((seg.group_id, seg.clone_of, seg.label))
            if not seg.label and original is not None:
                seg.label = original.label  # tiene l'etichetta che mostrava, senza "(CLONE)"
            song.groups[group.id] = group
            seg.group_id, seg.clone_of = group.id, None

    def undo(self, song: Song) -> None:
        for seg, group, (gid, clone_of, label) in zip(self.segments, self._groups, self._old):
            seg.group_id, seg.clone_of, seg.label = gid, clone_of, label
            song.groups.pop(group.id, None)


class AddTrack(Command):
    """Inserisce una traccia alla posizione `index` (con i gruppi nuovi dei suoi segmenti)."""

    label = "Add track"

    def __init__(self, track: Track, index: int, groups: list[Group] | None = None) -> None:
        self.track, self.index, self.groups = track, index, list(groups or [])

    def do(self, song: Song) -> None:
        for g in self.groups:
            song.groups[g.id] = g
        song.tracks.insert(self.index, self.track)

    def undo(self, song: Song) -> None:
        song.tracks.remove(self.track)
        for g in self.groups:
            song.groups.pop(g.id, None)


class DeleteTrack(Command):
    """Elimina una traccia con i suoi segmenti; i gruppi rimasti senza segmenti escono dal brano."""

    label = "Delete track"

    def __init__(self, track: Track) -> None:
        self.track = track
        self._index = 0
        self._groups: list[Group] = []

    def do(self, song: Song) -> None:
        self._index = song.tracks.index(self.track)
        song.tracks.remove(self.track)
        used = {s.group_id for t in song.tracks for s in t.segments}
        gids = dict.fromkeys(s.group_id for s in self.track.segments)
        self._order = list(song.groups)  # ordine dei gruppi per l'Undo
        self._groups = [song.groups.pop(g) for g in gids if g not in used and g in song.groups]
        self._adopted = _adopt_orphan_clones(song, self.track.segments)

    def undo(self, song: Song) -> None:
        _restore_clones(self._adopted)
        _restore_groups(song, self._groups, self._order)
        song.tracks.insert(self._index, self.track)


class MoveTrack(Command):
    """Sposta una traccia alla posizione `index` (indice nella lista finale)."""

    label = "Reorder tracks"

    def __init__(self, track: Track, index: int) -> None:
        self.track, self.index = track, index
        self._old = 0

    def do(self, song: Song) -> None:
        self._old = song.tracks.index(self.track)
        song.tracks.remove(self.track)
        song.tracks.insert(self.index, self.track)

    def undo(self, song: Song) -> None:
        song.tracks.remove(self.track)
        song.tracks.insert(self._old, self.track)


class AddNotes(Command):
    """Aggiunge note ai gruppi: [(gruppo, nota)]."""

    label = "Add notes"

    def __init__(self, items: list) -> None:
        self.items = list(items)

    def do(self, song: Song) -> None:
        for group, note in self.items:
            group.notes.append(note)
            group.notes.sort(key=lambda n: (n.start, n.midi))

    def undo(self, song: Song) -> None:
        for group, note in self.items:
            group.notes.remove(note)


class DeleteNotes(Command):
    """Cancella note dai gruppi: [(gruppo, nota)]."""

    label = "Delete notes"

    def __init__(self, items: list) -> None:
        self.items = list(items)
        self._where: list = []

    def do(self, song: Song) -> None:
        self._where = []
        for group, note in self.items:
            if note in group.notes:
                self._where.append((group, note, group.notes.index(note)))
                group.notes.remove(note)

    def undo(self, song: Song) -> None:
        for group, note, i in reversed(self._where):
            group.notes.insert(i, note)


class EditNotes(Command):
    """Cambia campi delle note: [(gruppo, nota, {campo: valore})]; riordina i gruppi toccati."""

    label = "Edit notes"

    def __init__(self, changes: list) -> None:
        self.changes = list(changes)
        self._old: list = []

    def do(self, song: Song) -> None:
        self._old = [(note, {k: getattr(note, k) for k in values}) for _g, note, values in self.changes]
        for _group, note, values in self.changes:
            for k, v in values.items():
                setattr(note, k, v)
        self._sort()

    def undo(self, song: Song) -> None:
        for note, values in self._old:
            for k, v in values.items():
                setattr(note, k, v)
        self._sort()

    def is_noop(self) -> bool:
        return all(getattr(note, k) == v for _g, note, values in self.changes for k, v in values.items())

    def _sort(self) -> None:
        for group in {id(g): g for g, _n, _v in self.changes}.values():
            group.notes.sort(key=lambda n: (n.start, n.midi))


class SetChords(Command):
    """Sostituisce le sigle d'accordo di un gruppo."""

    label = "Chord symbol"

    def __init__(self, group: Group, chords: list) -> None:
        self.group, self.new = group, sorted(chords)
        self._old: list = []

    def do(self, song: Song) -> None:
        self._old = list(self.group.chords)
        self.group.chords = list(self.new)

    def undo(self, song: Song) -> None:
        self.group.chords = list(self._old)

    def is_noop(self) -> bool:
        return sorted(self.group.chords) == self.new


class SetSlurs(Command):
    """Sostituisce le legature di frase (slur) di un gruppo."""

    label = "Slur"

    def __init__(self, group: Group, slurs: list) -> None:
        self.group, self.new = group, list(slurs)
        self._old: list = []

    def do(self, song: Song) -> None:
        self._old = list(self.group.slurs)
        self.group.slurs = list(self.new)

    def undo(self, song: Song) -> None:
        self.group.slurs = list(self._old)

    def is_noop(self) -> bool:
        return self.group.slurs == self.new


class SetSongTables(Command):
    """Sostituisce le tabelle di tempo, metro e tonalità (finestra Song Properties)."""

    label = "Song parameters"

    def __init__(self, tempos, meters, keys) -> None:
        self.new = (tempos, meters, keys)
        self._old = None

    def do(self, song: Song) -> None:
        self._old = (song.tempos, song.meters, song.keys)
        song.tempos, song.meters, song.keys = self.new

    def undo(self, song: Song) -> None:
        song.tempos, song.meters, song.keys = self._old


class SetInitialKey(Command):
    label = "Set key"

    def __init__(self, key: Key) -> None:
        self.key = key
        self._old: Key | None = None

    def do(self, song: Song) -> None:
        self._old = song.keys.initial
        song.keys.set(0, self.key)

    def undo(self, song: Song) -> None:
        assert self._old is not None
        song.keys.set(0, self._old)


Listener = Callable[["Document"], None]


class Document:
    def __init__(self, song: Song, path: str | None = None, project_path: str | None = None) -> None:
        self.song = song
        self.path = path  # file da cui è stato aperto (.mid o .music)
        self.project_path = project_path  # file .music su cui salva Save (None: non ancora salvato)
        self._undo: list[Command] = []
        self._redo: list[Command] = []
        self._listeners: list[Listener] = []
        self._saved_top: Command | None = None  # ultimo comando eseguito al momento del salvataggio
        self._touched = False  # modifiche fuori da Ctrl+Z (es. Mute / Solo)
        # 2026-11-20: cresce a ogni modifica del contenuto (execute/undo/redo), non con touch()
        # né mark_saved(): chi ricostruisce dati derivati (es. l'esecuzione per il play) lo
        # confronta per non rifarli quando cambiano solo Mute/Solo o lo stato di salvataggio.
        self.version = 0

    @property
    def modified(self) -> bool:
        """Ci sono modifiche non salvate (anche tornando indietro con Ctrl+Z dopo un salvataggio)."""
        top = self._undo[-1] if self._undo else None
        return self._touched or top is not self._saved_top

    def touch(self) -> None:
        """Una modifica che non passa da un comando (non entra in Ctrl+Z) ma va salvata."""
        self._touched = True
        self._changed()

    def mark_saved(self, project_path: str) -> None:
        self.project_path = project_path
        self._saved_top = self._undo[-1] if self._undo else None
        self._touched = False
        self._changed()

    def subscribe(self, fn: Listener) -> None:
        self._listeners.append(fn)

    def unsubscribe(self, fn: Listener) -> None:
        if fn in self._listeners:
            self._listeners.remove(fn)

    def _changed(self) -> None:
        for fn in list(self._listeners):
            fn(self)

    def execute(self, cmd: Command) -> None:
        if cmd.is_noop():  # niente da cambiare: nessun passo di Undo vuoto
            return
        cmd.do(self.song)
        self._undo.append(cmd)
        self._redo.clear()
        self.version += 1
        self._changed()

    def undo(self) -> None:
        if self._undo:
            cmd = self._undo.pop()
            cmd.undo(self.song)
            self._redo.append(cmd)
            self.version += 1
            self._changed()

    def redo(self) -> None:
        if self._redo:
            cmd = self._redo.pop()
            cmd.do(self.song)
            self._undo.append(cmd)
            self.version += 1
            self._changed()

    @property
    def can_undo(self) -> bool:
        return bool(self._undo)

    @property
    def can_redo(self) -> bool:
        return bool(self._redo)
