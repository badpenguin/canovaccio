"""Menu a comparsa (tasto destro) da un modello Gio.Menu."""

from __future__ import annotations

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
from gi.repository import Gdk, GLib, Gtk  # noqa: E402


def popup_menu(model, parent: Gtk.Widget, x: float, y: float) -> None:
    """Apre il menu `model` nel punto (x, y) di `parent`, con i sottomenu a lato.
    `parent` va scelto grande (la scheda intera, non un'area piccola): GTK dimensiona il
    menu anche in base al genitore, e con un genitore piccolo compare la barra di scorrimento."""
    pop = Gtk.PopoverMenu.new_from_model_full(model, Gtk.PopoverMenuFlags.NESTED)
    pop.set_parent(parent)
    pop.set_has_arrow(False)
    rect = Gdk.Rectangle()
    rect.x, rect.y, rect.width, rect.height = int(x), int(y), 1, 1
    pop.set_pointing_to(rect)
    pop.connect("closed", lambda p: GLib.idle_add(lambda: p.unparent() or False))
    pop.popup()
