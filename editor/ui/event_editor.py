"""Event Editor: note ed eventi di controllo dei segmenti selezionati (una traccia), come
lista. È l'unico posto dove si vedono controller, pitch bend e aftertouch.

Colonne: posizione nel brano (misura.movimento.tick, 480 tick per semiminima), tipo,
numero (nota: altezza; controller: numero e nome se noto; aftertouch polifonico: tasto),
valore (nota: velocity; controller e aftertouch 0–127; pitch bend -8192…8191), durata
(solo note). Tutti i controller 0–127 (anche quelli assegnabili del Fantom: si vedono col
numero). Filtro per tipo; sotto, i campi della riga scelta con Applica; Aggiungi (tipo
scelto, alla posizione della riga scelta o all'inizio) ed Elimina (righe scelte). Ogni
modifica è un passo di Ctrl+Z. Il contenuto è dei gruppi: i cloni cambiano insieme.
"""

from __future__ import annotations

from dataclasses import replace
from fractions import Fraction

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gtk  # noqa: E402

from ..core.document import AddNotes, Compound, DeleteNotes, Document, EditNotes, SetAttribute  # noqa: E402
from ..core.model import BEND, CC, POLY_PRESSURE, PRESSURE, ControlEvent, Note, Segment, Track  # noqa: E402

TICKS = 1920  # tick per semibreve
NOTE = "note"
KIND_NAMES = {NOTE: "Note", CC: "Controller", BEND: "Pitch Bend", PRESSURE: "Aftertouch", POLY_PRESSURE: "Poly Aftertouch"}
FILTERS = [("All", None), ("Notes", {NOTE}), ("Controller", {CC}), ("Pitch Bend", {BEND}),
           ("Aftertouch", {PRESSURE, POLY_PRESSURE})]
ADD_KINDS = [NOTE, CC, BEND, PRESSURE, POLY_PRESSURE]
CC_NAMES = {1: "Modulation", 2: "Breath", 4: "Foot", 5: "Portamento Time", 6: "Data Entry", 7: "Volume",
            8: "Balance", 10: "Pan", 11: "Expression", 12: "Effect 1", 13: "Effect 2", 16: "General 1",
            17: "General 2", 18: "General 3", 19: "General 4", 38: "Data Entry LSB", 64: "Sustain",
            65: "Portamento", 66: "Sostenuto", 67: "Soft", 68: "Legato", 69: "Hold 2", 71: "Resonance",
            72: "Release", 73: "Attack", 74: "Cutoff", 75: "Decay", 76: "Vibrato Rate", 77: "Vibrato Depth",
            78: "Vibrato Delay", 80: "General 5", 81: "General 6", 82: "General 7", 83: "General 8",
            84: "Portamento Control", 91: "Reverb", 92: "Tremolo", 93: "Chorus", 94: "Variation", 95: "Phaser",
            96: "Data +", 97: "Data −", 98: "NRPN LSB", 99: "NRPN MSB", 100: "RPN LSB", 101: "RPN MSB",
            120: "All Sound Off", 121: "Reset All Controllers", 123: "All Notes Off"}
NOTE_NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]


def note_name(midi: int) -> str:
    return f"{NOTE_NAMES[midi % 12]}{midi // 12 - 1} ({midi})"


def note_problem(group, item, seg: Segment, start: Fraction, duration: Fraction, midi: int,
                 drums: bool) -> str | None:
    """Controlla una nota (posizioni nel gruppo) prima di Apply o Add: deve stare nel
    segmento e non sovrapporsi a una nota della stessa altezza del gruppo (`item` è la nota
    che si sta modificando, None per una nuova). Restituisce il messaggio d'errore o None.
    2026-11-20: prima Apply accettava note oltre la fine del segmento o sopra un'altra nota
    uguale; le regole sono quelle della griglia (sulla batteria conta solo l'attacco)."""
    end = start + duration
    seg_end = seg.trim + seg.length
    inside = seg.trim <= start < seg_end if drums else seg.trim <= start and end <= seg_end
    if not inside:
        return "The note must stay inside its segment."
    for n in group.notes:
        if n is item or n.midi != midi:
            continue
        clash = n.start == start if drums else n.start < end and start < n.start + n.duration
        if clash:
            return "The note would overlap a note of the same pitch."
    return None


class EventEditorView(Gtk.Box):
    def __init__(self, doc: Document, track: Track, segments: list[Segment], transport, on_message) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=6,
                         margin_start=8, margin_end=8, margin_top=6, margin_bottom=6)
        self.doc, self.track, self.segments, self.transport, self.on_message = doc, track, segments, transport, on_message
        song = doc.song
        self.first_bar = min(song.segment_bars(s)[0] for s in segments)
        self.key = ("events", track.id, tuple(s.id for s in segments))
        self.edit_tracks = [track]
        self.rows: list[dict] = []
        # 2026-11-20: tratti del brano coperti dai segmenti mostrati; servono a ritrovare i
        # segmenti dopo uno Split o un Undo, che li sostituiscono con oggetti nuovi (prima
        # la lista restava vuota)
        self._spans = [song.segment_span(s) for s in segments]

        # filtro, aggiungi, elimina
        self.filter = Gtk.DropDown.new_from_strings([label for label, _k in FILTERS])
        self.filter.connect("notify::selected", lambda *_: self.refresh())
        self.add_kind = Gtk.DropDown.new_from_strings([KIND_NAMES[k] for k in ADD_KINDS])
        add = Gtk.Button(label="Add")
        add.connect("clicked", lambda *_: self.add())
        delete = Gtk.Button(label="Delete")
        delete.connect("clicked", lambda *_: self.delete())
        top = Gtk.Box(spacing=6)
        for w in (Gtk.Label(label="Show"), self.filter, Gtk.Box(hexpand=True), self.add_kind, add, delete):
            top.append(w)

        # lista
        self.strings = Gtk.StringList.new([])
        self.selection = Gtk.MultiSelection(model=self.strings)
        self.selection.connect("selection-changed", lambda *_: self._load_fields())
        self.view = Gtk.ColumnView(model=self.selection, show_row_separators=True, vexpand=True)
        # colonne a larghezza fissa (nessuna si allarga); i numeri allineati a destra
        for title, field_name, width, xalign in (("Position", "pos", 110, 0), ("Type", "kind", 130, 0),
                                                 ("Number", "number", 210, 0.5), ("Value", "value", 80, 1),
                                                 ("Duration", "duration", 80, 1)):
            factory = Gtk.SignalListItemFactory()
            factory.connect("setup", lambda _f, li, x=xalign: li.set_child(Gtk.Label(xalign=x, margin_start=6,
                                                                                    margin_end=6)))
            factory.connect("bind", lambda _f, li, name=field_name: li.get_child().set_label(
                self._cell(int(li.get_item().get_string()), name)))
            column = Gtk.ColumnViewColumn(title=title, factory=factory, expand=False, fixed_width=width)
            self.view.append_column(column)
        self.view.set_hexpand(False)
        self.view.set_halign(Gtk.Align.START)
        scroll = Gtk.ScrolledWindow(child=self.view, vexpand=True, propagate_natural_width=True,
                                    hscrollbar_policy=Gtk.PolicyType.NEVER)

        # campi della riga scelta
        self.f_pos = Gtk.Entry(width_chars=12, placeholder_text="bar.beat.tick")
        self.f_number = Gtk.SpinButton.new_with_range(0, 127, 1)
        self.f_value = Gtk.SpinButton.new_with_range(-8192, 8191, 1)
        self.f_duration = Gtk.Entry(width_chars=8, placeholder_text="tick")
        apply = Gtk.Button(label="Apply")
        apply.add_css_class("suggested-action")
        apply.connect("clicked", lambda *_: self.apply())
        bottom = Gtk.Box(spacing=6)
        for label, widget in (("Position", self.f_pos), ("Number", self.f_number), ("Value", self.f_value),
                              ("Duration (ticks)", self.f_duration)):
            bottom.append(Gtk.Label(label=label))
            bottom.append(widget)
        bottom.append(apply)

        self.append(top)
        self.append(Gtk.Frame(child=scroll))
        self.append(bottom)
        doc.subscribe(self._changed)
        self.refresh()

    # --- infrastruttura delle schede --------------------------------------------

    def shutdown(self) -> None:
        self.doc.unsubscribe(self._changed)

    def scroll_to_playhead(self) -> None:
        pass

    def set_mode_cursor(self, _mode: str) -> None:
        pass  # gli strumenti (Select, Create…) non valgono nella lista

    def _changed(self, _doc) -> None:
        if all(t is not self.track for t in self.doc.song.tracks):
            return  # traccia cancellata: la finestra principale chiude l'Event Editor
        self.refresh()

    # --- posizioni ---------------------------------------------------------------

    def _format(self, pos: Fraction) -> str:
        song = self.doc.song
        bar, off = song.split_position(pos)
        beat = song.meters.at(bar).beat
        n = int(off // beat)
        return f"{bar + 1}.{n + 1}.{round((off - n * beat) * TICKS)}"

    def _parse(self, text: str) -> Fraction | None:
        try:
            parts = [int(p) for p in text.strip().split(".")]
        except ValueError:
            return None
        while len(parts) < 3:
            parts.append(1 if len(parts) < 2 else 0)
        bar, beat, tick = parts[:3]
        if bar < 1 or beat < 1 or tick < 0:
            return None
        song = self.doc.song
        return song.meters.bar_start(bar - 1) + (beat - 1) * song.meters.at(bar - 1).beat + Fraction(tick, TICKS)

    def _segment_at(self, pos: Fraction) -> Segment | None:
        for seg in self.segments:
            a, b = self.doc.song.segment_span(seg)
            if a <= pos < b:
                return seg
        return None

    # --- righe ---------------------------------------------------------------------

    def refresh(self) -> None:
        """Ricostruisce le righe. I segmenti mostrati sono quelli di prima ancora presenti
        (anche se spostati) più quelli della traccia nei tratti di prima (pezzi di uno Split,
        segmenti tornati con Ctrl+Z); se non ne resta nessuno i tratti si tengono, così un
        Ctrl+Z dopo Delete li fa ricomparire."""
        song = self.doc.song
        shown = {id(s) for s in self.segments}
        current = []
        for s in self.track.segments:
            a, b = song.segment_span(s)
            if id(s) in shown or any(a < y and x < b for x, y in self._spans):
                current.append(s)
        self.segments = current
        if current:
            self._spans = [song.segment_span(s) for s in current]
        wanted = FILTERS[self.filter.get_selected()][1]
        rows = []
        for seg in self.segments:
            group = song.groups[seg.group_id]
            origin, _end = song.segment_span(seg)
            for n in group.notes:
                rel = n.start - seg.trim
                if 0 <= rel < seg.length:
                    rows.append({"kind": NOTE, "pos": origin + rel, "seg": seg, "group": group, "item": n})
            for e in group.events:
                rel = e.start - seg.trim
                if 0 <= rel < seg.length:
                    rows.append({"kind": e.kind, "pos": origin + rel, "seg": seg, "group": group, "item": e})
        rows.sort(key=lambda r: (r["pos"], r["kind"] != NOTE))
        self.rows = [r for r in rows if wanted is None or r["kind"] in wanted]
        self.strings.splice(0, self.strings.get_n_items(), [str(i) for i in range(len(self.rows))])

    def _cell(self, index: int, name: str) -> str:
        if not 0 <= index < len(self.rows):
            return ""
        r = self.rows[index]
        item, kind = r["item"], r["kind"]
        if name == "pos":
            return self._format(r["pos"])
        if name == "kind":
            return KIND_NAMES.get(kind, kind)
        if name == "number":
            if kind == NOTE:
                return note_name(item.midi + r["seg"].transpose)
            if kind == CC:
                return f"{item.number} {CC_NAMES.get(item.number, '')}".strip()
            if kind == POLY_PRESSURE:
                return note_name(item.number)
            return ""
        if name == "value":
            return str(item.velocity if kind == NOTE else item.value)
        if name == "duration":
            return str(round(item.duration * TICKS)) if kind == NOTE else ""
        return ""

    def _selected(self) -> list[dict]:
        bits = self.selection.get_selection()
        return [self.rows[bits.get_nth(i)] for i in range(bits.get_size()) if bits.get_nth(i) < len(self.rows)]

    def _load_fields(self) -> None:
        chosen = self._selected()
        if len(chosen) != 1:
            return
        r = chosen[0]
        item, kind = r["item"], r["kind"]
        self.f_pos.set_text(self._format(r["pos"]))
        self.f_number.set_value(item.midi if kind == NOTE else item.number)
        self.f_value.set_value(item.velocity if kind == NOTE else item.value)
        self.f_duration.set_text(str(round(item.duration * TICKS)) if kind == NOTE else "")
        self.f_number.set_sensitive(kind in (NOTE, CC, POLY_PRESSURE))
        self.f_duration.set_sensitive(kind == NOTE)

    # --- modifiche -----------------------------------------------------------------------

    def apply(self) -> None:
        """Applica i campi alla riga scelta (una)."""
        chosen = self._selected()
        if len(chosen) != 1:
            self.on_message("Select a row to edit.", True)
            return
        r = chosen[0]
        pos = self._parse(self.f_pos.get_text())
        if pos is None or self._segment_at(pos) is not r["seg"]:
            self.on_message("Invalid position: bar.beat.tick, inside the same segment.", True)
            return
        for spin in (self.f_number, self.f_value):
            spin.update()
        start = pos - self.doc.song.segment_span(r["seg"])[0] + r["seg"].trim
        number, value = self.f_number.get_value_as_int(), self.f_value.get_value_as_int()
        if r["kind"] == NOTE:
            try:
                duration = Fraction(int(self.f_duration.get_text()), TICKS)
            except ValueError:
                duration = Fraction(0)
            if duration <= 0:
                self.on_message("Invalid duration (ticks, greater than 0).", True)
                return
            item = r["item"]
            # posizione e durata: si cambiano solo se modificate nei campi, così la sola
            # velocity non sposta una nota fuori dalla griglia dei tick (2026-11-20)
            if self._format(r["pos"]) == self.f_pos.get_text().strip():
                start = item.start
            if round(item.duration * TICKS) == int(self.f_duration.get_text()):
                duration = item.duration
            changes = {"start": start, "midi": max(0, min(number, 127)) - r["seg"].transpose,
                       "velocity": max(1, min(value, 127)), "duration": duration}
            changes = {k: v for k, v in changes.items() if getattr(item, k) != v}
            if not changes:  # niente da cambiare: nessun passo di Undo vuoto
                return
            # controllo solo se cambiano posizione, durata o altezza: la sola velocity di una nota
            # tagliata da un Resize (o sovrapposta nel MIDI importato) si può sempre cambiare
            problem = note_problem(r["group"], item, r["seg"], changes.get("start", item.start),
                                   changes.get("duration", item.duration), changes.get("midi", item.midi),
                                   self._drums()) if {"start", "duration", "midi"} & changes.keys() else None
            if problem:
                self.on_message(problem, True)
                return
            self.doc.execute(EditNotes([(r["group"], item, changes)]))
            return
        value = max(-8192, min(value, 8191)) if r["kind"] == BEND else max(0, min(value, 127))
        if self._format(r["pos"]) == self.f_pos.get_text().strip():
            start = r["item"].start  # posizione non toccata: resta esatta
        new = replace(r["item"], start=start, number=max(0, min(number, 127)), value=value)
        if new == r["item"]:  # niente da cambiare: nessun passo di Undo vuoto
            return
        events = sorted((new if e is r["item"] else e for e in r["group"].events), key=lambda e: e.start)
        self.doc.execute(SetAttribute(r["group"], "events", events, "Edit event"))

    def add(self) -> None:
        """Nuova riga del tipo scelto alla posizione della riga scelta (o all'inizio)."""
        if not self.segments:  # 2026-11-20: prima con la lista vuota Add andava in errore
            self.on_message("No segment to add to.", True)
            return
        chosen = self._selected()
        pos = chosen[0]["pos"] if chosen else self.doc.song.segment_span(self.segments[0])[0]
        seg = self._segment_at(pos) or self.segments[0]
        group = self.doc.song.groups[seg.group_id]
        start = pos - self.doc.song.segment_span(seg)[0] + seg.trim
        kind = ADD_KINDS[self.add_kind.get_selected()]
        if kind == NOTE:
            note = Note(start, Fraction(1, 4), 60 - seg.transpose, self.track.velocity)
            problem = note_problem(group, None, seg, note.start, note.duration, note.midi, self._drums())
            if problem:
                self.on_message(problem, True)
                return
            self.doc.execute(AddNotes([(group, note)]))
            return
        default = {CC: (1, 64), BEND: (0, 0), PRESSURE: (0, 64), POLY_PRESSURE: (60, 64)}[kind]
        events = sorted(group.events + [ControlEvent(start, kind, *default)], key=lambda e: e.start)
        self.doc.execute(SetAttribute(group, "events", events, "Add event"))

    def _drums(self) -> bool:
        """Traccia di batteria: per le note conta solo l'attacco."""
        return self.track.instrument.is_percussion or self.track.channel == 10

    def delete(self) -> None:
        chosen = self._selected()
        if not chosen:
            self.on_message("Select the rows to delete.", True)
            return
        commands = []
        notes = [(r["group"], r["item"]) for r in chosen if r["kind"] == NOTE]
        if notes:
            commands.append(DeleteNotes(notes))
        gone = {id(r["item"]) for r in chosen if r["kind"] != NOTE}
        for group in {id(r["group"]): r["group"] for r in chosen if r["kind"] != NOTE}.values():
            commands.append(SetAttribute(group, "events", [e for e in group.events if id(e) not in gone],
                                         "Delete events"))
        self.doc.execute(Compound(commands, "Delete events"))


class EventEditorWindow(Gtk.Window):
    """L'Event Editor in una finestra di dialogo (non modale: si continua a lavorare nella
    finestra principale), larga quanto la lista. Chiudi o Esc la chiudono."""

    def __init__(self, parent: Gtk.Window, view: EventEditorView, title: str) -> None:
        super().__init__(title=title, transient_for=parent, default_width=720, default_height=640)
        self.view = view
        close = Gtk.Button(label="Close", halign=Gtk.Align.END)
        close.connect("clicked", lambda *_: self.close())
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6, margin_bottom=10, margin_end=10)
        box.append(view)
        box.append(close)
        self.set_child(box)
        esc = Gtk.ShortcutController()
        esc.add_shortcut(Gtk.Shortcut.new(Gtk.ShortcutTrigger.parse_string("Escape"), Gtk.NamedAction.new("window.close")))
        self.add_controller(esc)
        self.connect("close-request", lambda *_: view.shutdown() or False)
