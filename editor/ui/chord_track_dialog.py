"""Proprietà della traccia accordi (doppio click sul nome "Accordi"): gli stili del brano
(.sty… o .stile personalizzati; solo i percorsi dei file: si leggono quando servono) con Aggiungi… / Rimuovi, e il
canale della Preview. Salva (un passo di Ctrl+Z) / Annulla o Esc."""

from __future__ import annotations

from pathlib import Path
from typing import Callable

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gio, GLib, Gtk  # noqa: E402

from ..core.document import Compound, Document, SetAttribute  # noqa: E402
from ..core.model import Track  # noqa: E402
from ..importer.style import EXTENSIONS  # noqa: E402


class ChordTrackDialog(Gtk.Window):
    def __init__(self, parent: Gtk.Window, doc: Document, track: Track,
                 start_dir: Callable[[Gtk.FileDialog], None], remember_dir: Callable[[str], None]) -> None:
        super().__init__(title="Chord Track", transient_for=parent, modal=True, default_width=520,
                         default_height=380)
        self.doc, self.track = doc, track
        self.styles = list(track.styles)
        self.start_dir, self.remember_dir = start_dir, remember_dir

        self.list = Gtk.ListBox(selection_mode=Gtk.SelectionMode.SINGLE)
        scroll = Gtk.ScrolledWindow(vexpand=True, hscrollbar_policy=Gtk.PolicyType.NEVER, child=self.list)
        add = Gtk.Button(label="Add…")
        add.connect("clicked", lambda *_: self._add())
        remove = Gtk.Button(label="Remove")
        remove.connect("clicked", lambda *_: self._remove())
        style_buttons = Gtk.Box(spacing=6)
        style_buttons.append(add)
        style_buttons.append(remove)
        self.channel = Gtk.SpinButton.new_with_range(1, 16, 1)
        self.channel.set_value(track.channel)
        channel_row = Gtk.Box(spacing=8)
        channel_row.append(Gtk.Label(label="Preview channel"))
        channel_row.append(self.channel)

        cancel = Gtk.Button(label="Cancel")
        cancel.connect("clicked", lambda *_: self.close())
        save = Gtk.Button(label="Save")
        save.add_css_class("suggested-action")
        save.connect("clicked", lambda *_: self.save())
        buttons = Gtk.Box(spacing=8, halign=Gtk.Align.END)
        buttons.append(cancel)
        buttons.append(save)

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10,
                      margin_start=18, margin_end=18, margin_top=18, margin_bottom=18)
        box.append(Gtk.Label(label="Song styles", xalign=0, css_classes=["heading"]))
        box.append(Gtk.Frame(child=scroll))
        box.append(style_buttons)
        box.append(channel_row)
        box.append(buttons)
        self.set_child(box)
        self.set_default_widget(save)
        esc = Gtk.ShortcutController()
        esc.add_shortcut(Gtk.Shortcut.new(Gtk.ShortcutTrigger.parse_string("Escape"), Gtk.NamedAction.new("window.close")))
        self.add_controller(esc)
        self._fill()

    def _fill(self) -> None:
        while (row := self.list.get_row_at_index(0)) is not None:
            self.list.remove(row)
        for path in self.styles:
            missing = "" if Path(path).is_file() else "  (file not found)"
            label = Gtk.Label(label=Path(path).name + missing, xalign=0, tooltip_text=path,
                              margin_start=8, margin_end=8, margin_top=5, margin_bottom=5)
            self.list.append(label)

    def _add(self) -> None:
        dialog = Gtk.FileDialog(title="Add Style")
        flt = Gtk.FileFilter(name="Styles (.stile and Yamaha)")
        for ext in (".stile",) + EXTENSIONS:
            flt.add_pattern(f"*{ext}")
            flt.add_pattern(f"*{ext.upper()}")
        filters = Gio.ListStore.new(Gtk.FileFilter)
        filters.append(flt)
        dialog.set_filters(filters)
        self.start_dir(dialog)

        def done(d, result) -> None:
            try:
                f = d.open_finish(result)
            except GLib.Error:
                return
            if f is not None and f.get_path() and f.get_path() not in self.styles:
                self.remember_dir(f.get_path())
                self.styles.append(f.get_path())
                self._fill()

        dialog.open(self, None, done)

    def _remove(self) -> None:
        row = self.list.get_selected_row()
        if row is not None:
            del self.styles[row.get_index()]
            self._fill()

    def save(self) -> None:
        self.channel.update()
        commands = []
        if self.styles != self.track.styles:
            commands.append(SetAttribute(self.track, "styles", list(self.styles), "Song styles"))
        if self.channel.get_value_as_int() != self.track.channel:
            commands.append(SetAttribute(self.track, "channel", self.channel.get_value_as_int(), "Preview channel"))
        if commands:
            self.doc.execute(Compound(commands, "Chord track"))
        self.close()
