"""File > Input Device…: la porta MIDI d'ingresso (o nessuna). ↻ cerca di nuovo le
porte; Salva applica e chiude; Chiudi o Esc chiudono senza cambiare."""

from __future__ import annotations

from typing import Callable

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gtk  # noqa: E402

from ..output.midi_input import list_input_ports  # noqa: E402


class InputDeviceDialog(Gtk.Window):
    def __init__(self, parent: Gtk.Window, current: str, on_save: Callable[[str], None]) -> None:
        super().__init__(title="Input Device", transient_for=parent, modal=True, resizable=False)
        self.current, self.on_save = current, on_save
        self.ports: list[str] = []
        self.port = Gtk.DropDown.new_from_strings([])
        self.port.set_hexpand(True)
        refresh = Gtk.Button(icon_name="view-refresh-symbolic", tooltip_text="Rescan MIDI ports")
        refresh.connect("clicked", lambda *_: self._fill())
        row = Gtk.Box(spacing=6)
        row.append(Gtk.Label(label="Input port"))
        row.append(self.port)
        row.append(refresh)
        hint = Gtk.Label(xalign=0, wrap=True, max_width_chars=48,
                         label="What you play goes to the current output with the sound of the selected track "
                               "(the highlighted row in the Tracks tab, or the track of the open editor).")
        close = Gtk.Button(label="Close")
        close.connect("clicked", lambda *_: self.close())
        save = Gtk.Button(label="Save")
        save.add_css_class("suggested-action")
        save.connect("clicked", lambda *_: self._save())
        buttons = Gtk.Box(spacing=8, halign=Gtk.Align.END)
        buttons.append(close)
        buttons.append(save)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12,
                      margin_start=18, margin_end=18, margin_top=18, margin_bottom=18)
        for w in (row, hint, buttons):
            box.append(w)
        self.set_child(box)
        self.set_default_widget(save)
        esc = Gtk.ShortcutController()
        esc.add_shortcut(Gtk.Shortcut.new(Gtk.ShortcutTrigger.parse_string("Escape"), Gtk.NamedAction.new("window.close")))
        self.add_controller(esc)
        self._fill()

    def _fill(self) -> None:
        found = list_input_ports()
        self.ports = [""] + found + ([self.current] if self.current and self.current not in found else [])
        labels = ["none"] + [p if p in found else f"{p} (not connected)" for p in self.ports[1:]]
        self.port.set_model(Gtk.StringList.new(labels))
        self.port.set_selected(self.ports.index(self.current) if self.current in self.ports else 0)

    def _save(self) -> None:
        self.on_save(self.ports[self.port.get_selected()])
        self.close()
