"""Dialogo Segment > Label: etichetta dei segmenti selezionati (Salva / Annulla)."""

from __future__ import annotations

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gtk  # noqa: E402

from ..core.document import Compound, Document, SetAttribute  # noqa: E402


class LabelDialog(Gtk.Window):
    def __init__(self, parent: Gtk.Window, doc: Document, segments: list) -> None:
        super().__init__(title="Segment Label" if len(segments) == 1 else f"Label of {len(segments)} Segments",
                         transient_for=parent, modal=True, resizable=False)
        self.doc, self.segments = doc, segments
        labels = {s.label for s in segments}
        self.entry = Gtk.Entry(text=labels.pop() if len(labels) == 1 else "", width_chars=30, activates_default=True)
        cancel = Gtk.Button(label="Cancel")
        cancel.connect("clicked", lambda *_: self.close())
        save = Gtk.Button(label="Save")
        save.add_css_class("suggested-action")
        save.connect("clicked", lambda *_: self.save())
        buttons = Gtk.Box(spacing=8, halign=Gtk.Align.END)
        buttons.append(cancel)
        buttons.append(save)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12,
                      margin_start=18, margin_end=18, margin_top=18, margin_bottom=18)
        box.append(self.entry)
        box.append(buttons)
        self.set_child(box)
        self.set_default_widget(save)
        esc = Gtk.ShortcutController()
        esc.add_shortcut(Gtk.Shortcut.new(Gtk.ShortcutTrigger.parse_string("Escape"), Gtk.NamedAction.new("window.close")))
        self.add_controller(esc)

    def save(self) -> None:
        text = self.entry.get_text().strip()
        changes = [SetAttribute(s, "label", text, "Label") for s in self.segments if s.label != text]
        if changes:
            self.doc.execute(Compound(changes, "Label segments"))
        self.close()
