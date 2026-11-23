"""Notes > Velocity…: velocity delle note selezionate.

Valore fisso 1–127 (parte dalla velocity della prima nota selezionata); con
"Umanizza" (spenta di default) ogni nota varia a caso entro ±N attorno al valore,
restando fra 1 e 127. Un solo passo di Ctrl+Z; Annulla o Esc scartano.
"""

from __future__ import annotations

import random

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gtk  # noqa: E402


def velocities(value: int, count: int, spread: int = 0, rng: random.Random | None = None) -> list[int]:
    """`count` velocity: `value`, o con `spread` > 0 a caso entro ±spread; sempre fra 1 e 127."""
    rng = rng or random.Random()
    return [min(max(value + (rng.randint(-spread, spread) if spread else 0), 1), 127) for _ in range(count)]


class VelocityDialog(Gtk.Window):
    def __init__(self, parent: Gtk.Window, view) -> None:
        super().__init__(title="Velocity", transient_for=parent, modal=True, resizable=False)
        self.view = view
        first = view.selected_notes()[0]
        grid = Gtk.Grid(row_spacing=10, column_spacing=12)
        grid.attach(Gtk.Label(label="Value", xalign=1), 0, 0, 1, 1)
        self.value = Gtk.SpinButton.new_with_range(1, 127, 1)
        self.value.set_value(first.velocity)
        self.value.set_activates_default(True)
        grid.attach(self.value, 1, 0, 1, 1)
        self.humanize = Gtk.CheckButton(label="Humanize: random variation of ±", active=False)
        self.spread = Gtk.SpinButton.new_with_range(1, 40, 1)
        self.spread.set_value(8)
        self.spread.set_sensitive(False)
        self.humanize.connect("toggled", lambda b: self.spread.set_sensitive(b.get_active()))
        row = Gtk.Box(spacing=6)
        row.append(self.humanize)
        row.append(self.spread)
        grid.attach(row, 0, 1, 2, 1)

        cancel = Gtk.Button(label="Cancel")
        cancel.connect("clicked", lambda *_: self.close())
        ok = Gtk.Button(label="Apply")
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
        for spin in (self.value, self.spread):
            spin.update()
        spread = self.spread.get_value_as_int() if self.humanize.get_active() else 0
        self.view.set_velocities(self.value.get_value_as_int(), spread)
        self.close()
