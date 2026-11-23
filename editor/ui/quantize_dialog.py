"""Notes > Quantize…: quantizza le note selezionate scegliendo cosa allineare (inizio,
durata, fine) e la griglia (anche terzine e puntati; di partenza lo Snap della griglia).
Un solo passo di Ctrl+Z; Annulla o Esc scartano."""

from __future__ import annotations

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gtk  # noqa: E402

from ..core.quantize import GRIDS, MODES  # noqa: E402


class QuantizeDialog(Gtk.Window):
    def __init__(self, parent: Gtk.Window, view) -> None:
        super().__init__(title="Quantize", transient_for=parent, modal=True, resizable=False)
        self.view = view
        grid = Gtk.Grid(row_spacing=10, column_spacing=12)
        grid.attach(Gtk.Label(label="Align", xalign=1), 0, 0, 1, 1)
        self.mode = Gtk.DropDown.new_from_strings([label for _m, label in MODES])
        grid.attach(self.mode, 1, 0, 1, 1)
        grid.attach(Gtk.Label(label="Grid", xalign=1), 0, 1, 1, 1)
        self.grid = Gtk.DropDown.new_from_strings([label for label, _v in GRIDS])
        steps = [v for _l, v in GRIDS]
        self.grid.set_selected(steps.index(view.snap) if view.snap in steps else 2)  # Snap corrente (Off → 1/16)
        grid.attach(self.grid, 1, 1, 1, 1)

        cancel = Gtk.Button(label="Cancel")
        cancel.connect("clicked", lambda *_: self.close())
        ok = Gtk.Button(label="Quantize")
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
        self.view.quantize_selection(GRIDS[self.grid.get_selected()][1], MODES[self.mode.get_selected()][0])
        self.close()
