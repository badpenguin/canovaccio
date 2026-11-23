"""Proprietà di una nota (doppio click nella griglia): altezza, grafia, velocity,
inizio (misura, battuta), durata (in battute), legatura alla successiva.

Salva applica tutto come un solo passo di Ctrl+Z; Annulla o Esc scartano.
La nota deve restare dentro il suo segmento.
"""

from __future__ import annotations

from fractions import Fraction

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gtk  # noqa: E402

from ..core.document import Document, EditNotes  # noqa: E402
from ..core.model import Group, PlacedNote  # noqa: E402
from ..core.pitch import STEP_SEMITONES, Spelling  # noqa: E402

NOTE_NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]
AUTO = "Automatic (from key)"


def pitch_label(midi: int) -> str:
    return f"{NOTE_NAMES[midi % 12]}{midi // 12 - 1} ({midi})"


def spellings_for(midi: int) -> list[Spelling]:
    """Grafie possibili per l'altezza (alterazioni da 𝄫 a 𝄪)."""
    return [Spelling(step, alter) for step in STEP_SEMITONES for alter in (-2, -1, 0, 1, 2)
            if (STEP_SEMITONES[step] + alter) % 12 == midi % 12]


def _spin(lo: float, hi: float, step: float, digits: int, value: float) -> Gtk.SpinButton:
    spin = Gtk.SpinButton.new_with_range(lo, hi, step)
    spin.set_digits(digits)
    spin.set_value(value)
    spin.set_activates_default(True)
    return spin


class NoteDialog(Gtk.Window):
    def __init__(self, parent: Gtk.Window, doc: Document, placed: PlacedNote, group: Group) -> None:
        super().__init__(title="Note Properties", transient_for=parent, modal=True, resizable=False)
        self.doc, self.placed, self.group = doc, placed, group
        song, note, seg = doc.song, placed.note, placed.segment
        meters = song.meters
        self.bar0 = meters.bar_at(placed.start)
        self.beat_len = meters.at(self.bar0).beat
        self.beat0 = (placed.start - meters.bar_start(self.bar0)) / self.beat_len + 1
        self.len0 = note.duration / self.beat_len

        grid = Gtk.Grid(row_spacing=10, column_spacing=12)
        row = 0

        def add(label: str, widget: Gtk.Widget) -> None:
            nonlocal row
            grid.attach(Gtk.Label(label=label, xalign=1), 0, row, 1, 1)
            grid.attach(widget, 1, row, 1, 1)
            row += 1

        self.pitch = Gtk.DropDown.new_from_strings([pitch_label(m) for m in range(127, -1, -1)])
        self.pitch.set_selected(127 - placed.midi)
        self.pitch.connect("notify::selected", lambda *_: self._fill_spellings())
        add("Pitch", self.pitch)
        self.spelling = Gtk.DropDown.new_from_strings([AUTO])
        add("Spelling", self.spelling)
        self._fill_spellings(note.spelling)
        self.velocity = _spin(1, 127, 1, 0, note.velocity)
        add("Velocity", self.velocity)
        start = Gtk.Box(spacing=6)
        self.bar = _spin(1, 9999, 1, 0, self.bar0 + 1)
        self.beat = _spin(1, 32, 1, 0 if self.beat0.denominator == 1 else 3, float(self.beat0))
        for w in (Gtk.Label(label="bar"), self.bar, Gtk.Label(label="beat"), self.beat):
            start.append(w)
        add("Start", start)
        length = Gtk.Box(spacing=6)
        self.length = _spin(0.001, 9999, 0.25, 3, float(self.len0))
        length.append(self.length)
        length.append(Gtk.Label(label="beats"))
        add("Length", length)
        self.tie = Gtk.CheckButton(label="Tied to the next note", active=note.tie_next)
        add("", self.tie)

        self.error = Gtk.Label(xalign=0, wrap=True, css_classes=["status-error"])
        cancel = Gtk.Button(label="Cancel")
        cancel.connect("clicked", lambda *_: self.close())
        save = Gtk.Button(label="Save")
        save.add_css_class("suggested-action")
        save.connect("clicked", lambda *_: self.save())
        buttons = Gtk.Box(spacing=8, halign=Gtk.Align.END, margin_top=8)
        buttons.append(cancel)
        buttons.append(save)

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12,
                      margin_start=18, margin_end=18, margin_top=18, margin_bottom=18)
        for w in (grid, self.error, buttons):
            box.append(w)
        self.set_child(box)
        self.set_default_widget(save)
        esc = Gtk.ShortcutController()
        esc.add_shortcut(Gtk.Shortcut.new(Gtk.ShortcutTrigger.parse_string("Escape"), Gtk.NamedAction.new("window.close")))
        self.add_controller(esc)
        self._seg = seg

    def _midi(self) -> int:
        return 127 - self.pitch.get_selected()

    def _fill_spellings(self, current: Spelling | None = None) -> None:
        self._spellings = [None] + spellings_for(self._midi())
        model = Gtk.StringList.new([AUTO] + [str(sp) for sp in self._spellings[1:]])
        self.spelling.set_model(model)
        self.spelling.set_selected(self._spellings.index(current) if current in self._spellings else 0)

    def save(self) -> None:
        song, note, seg = self.doc.song, self.placed.note, self._seg
        for spin in (self.velocity, self.bar, self.beat, self.length):
            spin.update()
        meters = song.meters
        bar = self.bar.get_value_as_int() - 1
        beat = Fraction(self.beat.get_value()).limit_denominator(64)
        if bar == self.bar0 and abs(float(beat - self.beat0)) < 0.0005:
            beat = self.beat0  # non toccata: resta esatta (anche se frazionaria, es. terzine)
        length = Fraction(self.length.get_value()).limit_denominator(192)
        if abs(float(length - self.len0)) < 0.0005:
            length = self.len0
        beat_len = meters.at(bar).beat
        if not 1 <= beat < meters.at(bar).numerator + 1:
            self.error.set_label(f"Bar {bar + 1} ({meters.at(bar)}) has no beat {float(beat):g}.")
            return
        start = meters.bar_start(bar) + (beat - 1) * beat_len
        duration = length * self.beat_len
        seg_start, seg_end = song.segment_span(seg)
        if not (seg_start <= start and start + duration <= seg_end):
            self.error.set_label("The note must stay inside its segment.")
            return
        values = {
            "midi": self._midi() - seg.transpose,
            "spelling": self._spellings[self.spelling.get_selected()],
            "velocity": self.velocity.get_value_as_int(),
            "start": start - seg_start + seg.trim,
            "duration": duration,
            "tie_next": self.tie.get_active(),
        }
        changed = {k: v for k, v in values.items() if getattr(note, k) != v}
        if changed:
            self.doc.execute(EditNotes([(self.group, note, changed)]))
        self.close()
