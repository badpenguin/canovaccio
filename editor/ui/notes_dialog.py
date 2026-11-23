"""File > Song Notes…: note del brano, testo libero salvato nel progetto. All'import di
uno stile Yamaha ci sono le regole delle parti (CASM) in forma leggibile.
"Salva" applica (un passo di Ctrl+Z); "Annulla" o Esc chiudono senza salvare."""

from __future__ import annotations

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gtk  # noqa: E402

from ..core.document import Document, SetAttribute  # noqa: E402


class NotesDialog(Gtk.Window):
    def __init__(self, parent: Gtk.Window, doc: Document) -> None:
        super().__init__(title="Song Notes", transient_for=parent, modal=True, default_width=760, default_height=520)
        self.doc = doc
        self.text = Gtk.TextView(wrap_mode=Gtk.WrapMode.WORD_CHAR, left_margin=8, right_margin=8,
                                 top_margin=8, bottom_margin=8)
        self.text.get_buffer().set_text(doc.song.notes)
        scroll = Gtk.ScrolledWindow(vexpand=True, hexpand=True, child=self.text)
        frame = Gtk.Frame(child=scroll)
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
        box.append(frame)
        box.append(buttons)
        self.set_child(box)
        esc = Gtk.ShortcutController()
        esc.add_shortcut(Gtk.Shortcut.new(Gtk.ShortcutTrigger.parse_string("Escape"), Gtk.NamedAction.new("window.close")))
        self.add_controller(esc)

    def save(self) -> None:
        buf = self.text.get_buffer()
        text = buf.get_text(buf.get_start_iter(), buf.get_end_iter(), False)
        if text != self.doc.song.notes:
            self.doc.execute(SetAttribute(self.doc.song, "notes", text, "Song notes"))
        self.close()
