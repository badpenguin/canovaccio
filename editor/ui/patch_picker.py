"""Scelta di un suono da un elenco lungo (lista suoni di un'uscita MIDI, suoni di un
SoundFont): pulsante con il suono scelto che apre un riquadro con ricerca ed elenco.

Ricerca: ogni parola scritta deve essere l'inizio di una parola del nome, in qualunque
ordine; maiuscole indifferenti. "E Guit" trova "Electric Guitar" e "Guitar Electric";
"Guilt" non trova "AGUILT". Le parole del nome sono separate da spazi e segni ("E.Piano"
= "E" + "Piano"). Invio sceglie il primo risultato.
"""

from __future__ import annotations

import re
from typing import Callable

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gtk  # noqa: E402

WORDS = re.compile(r"[0-9a-z#]+")


def matches(query: str, label: str) -> bool:
    words = WORDS.findall(label.lower())
    return all(any(w.startswith(q) for w in words) for q in WORDS.findall(query.lower()))


class PatchPicker(Gtk.MenuButton):
    def __init__(self, labels: list[str] | None = None, on_changed: Callable[[], None] | None = None) -> None:
        super().__init__(always_show_arrow=True)
        self.on_changed = on_changed
        self._selected = Gtk.INVALID_LIST_POSITION
        self.search = Gtk.SearchEntry(placeholder_text="Search: start of words, e.g. “e guit”")
        self.search.connect("search-changed", lambda *_: self.filter.changed(Gtk.FilterChange.DIFFERENT))
        self.search.connect("activate", lambda *_: self._activate(0))
        self.strings = Gtk.StringList.new([])
        self.filter = Gtk.CustomFilter.new(lambda item: matches(self.search.get_text(), item.get_string()))
        self.filtered = Gtk.FilterListModel(model=self.strings, filter=self.filter)
        factory = Gtk.SignalListItemFactory()
        factory.connect("setup", lambda _f, li: li.set_child(Gtk.Label(xalign=0, margin_start=6, margin_end=6)))
        factory.connect("bind", lambda _f, li: li.get_child().set_label(li.get_item().get_string()))
        self.view = Gtk.ListView(model=Gtk.NoSelection(model=self.filtered), factory=factory,
                                 single_click_activate=True)
        self.view.connect("activate", lambda _v, pos: self._activate(pos))
        scroll = Gtk.ScrolledWindow(child=self.view, min_content_height=320, min_content_width=440,
                                    hscrollbar_policy=Gtk.PolicyType.NEVER)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        box.append(self.search)
        box.append(scroll)
        popover = Gtk.Popover(child=box)
        popover.connect("show", lambda *_: (self.search.set_text(""), self.search.grab_focus()))
        self.set_popover(popover)
        self.set_items(labels or [])

    def set_items(self, labels: list[str]) -> None:
        self.labels = list(labels)
        self._index = {label: i for i, label in enumerate(self.labels)}
        self.strings.splice(0, self.strings.get_n_items(), self.labels)
        self.set_selected(Gtk.INVALID_LIST_POSITION)

    def get_selected(self) -> int:
        return self._selected

    def set_selected(self, index: int) -> None:
        """Scelta da programma (non chiama on_changed)."""
        self._selected = index if 0 <= index < len(self.labels) else Gtk.INVALID_LIST_POSITION
        self.set_label(self.labels[self._selected] if self._selected != Gtk.INVALID_LIST_POSITION else "—")

    def _activate(self, position: int) -> None:
        item = self.filtered.get_item(position)
        if item is None:
            return
        self.set_selected(self._index[item.get_string()])
        self.popdown()
        if self.on_changed is not None:
            self.on_changed()
