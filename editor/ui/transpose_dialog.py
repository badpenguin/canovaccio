"""Notes > Transpose…: traspone le note selezionate della griglia di una quantità in
semitoni, in gradi della scala (della tonalità in vigore dove sta ogni nota) o in ottave.
Un solo passo di Ctrl+Z; Annulla o Esc scartano."""

from __future__ import annotations

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gtk  # noqa: E402

UNITS = ["semitones", "scale steps (current key)", "octaves"]


class TransposeDialog(Gtk.Window):
    def __init__(self, parent: Gtk.Window, view) -> None:
        super().__init__(title="Transpose", transient_for=parent, modal=True, resizable=False)
        self.view = view
        grid = Gtk.Grid(row_spacing=10, column_spacing=12)
        grid.attach(Gtk.Label(label="Amount", xalign=1), 0, 0, 1, 1)
        self.amount = Gtk.SpinButton.new_with_range(-48, 48, 1)
        self.amount.set_value(1)
        self.amount.set_activates_default(True)
        grid.attach(self.amount, 1, 0, 1, 1)
        grid.attach(Gtk.Label(label="In", xalign=1), 0, 1, 1, 1)
        self.unit = Gtk.DropDown.new_from_strings(UNITS)
        grid.attach(self.unit, 1, 1, 1, 1)
        grid.attach(Gtk.Label(label="Positive = up, negative = down.", xalign=0), 0, 2, 2, 1)

        cancel = Gtk.Button(label="Cancel")
        cancel.connect("clicked", lambda *_: self.close())
        ok = Gtk.Button(label="Transpose")
        ok.add_css_class("suggested-action")
        ok.connect("clicked", lambda *_: self.apply())
        buttons = Gtk.Box(spacing=8, halign=Gtk.Align.END, margin_top=8)
        buttons.append(cancel)
        buttons.append(ok)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12,
                      margin_start=18, margin_end=18, margin_top=18, margin_bottom=18)
        box.append(grid)
        box.append(buttons)
        self.set_child(box)
        self.set_default_widget(ok)
        esc = Gtk.ShortcutController()
        esc.add_shortcut(Gtk.Shortcut.new(Gtk.ShortcutTrigger.parse_string("Escape"), Gtk.NamedAction.new("window.close")))
        self.add_controller(esc)

    def apply(self) -> None:
        self.amount.update()
        amount = self.amount.get_value_as_int()
        unit = self.unit.get_selected()
        if unit == 2:  # ottave
            amount *= 12
        if amount == 0 or self.view.transpose_selection(amount, diatonic=unit == 1):
            self.close()
