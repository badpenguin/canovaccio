"""Temi GTK disponibili e cambio del tema dell'app (menu View > Theme).

La scelta è salvata nelle preferenze e riapplicata all'avvio.
"""

from __future__ import annotations

import os
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gtk  # noqa: E402

SYSTEM = ""  # voce "System": il tema impostato nel sistema
BUILTIN = ["Adwaita"]  # incluso in GTK 4, non ha una cartella in /usr/share/themes


def theme_dirs() -> list[Path]:
    """Cartelle dei temi: quelle utente e di sistema dati da GLib (valgono su ogni sistema)."""
    from gi.repository import GLib

    dirs = [Path.home() / ".themes", Path(GLib.get_user_data_dir()) / "themes"]
    data = os.environ.get("XDG_DATA_DIRS")
    dirs += [Path(d) / "themes" for d in (data.split(os.pathsep) if data else GLib.get_system_data_dirs()) if d]
    return dirs


def available_themes() -> list[str]:
    """Temi con supporto GTK 4 (cartella gtk-4.0), più quelli inclusi in GTK."""
    found = set(BUILTIN)
    for base in theme_dirs():
        try:
            for d in base.iterdir():
                if (d / "gtk-4.0" / "gtk.css").is_file():
                    found.add(d.name)
        except OSError:
            continue
    return sorted(found, key=str.lower)


class ThemeSwitcher:
    def __init__(self) -> None:
        self.settings = Gtk.Settings.get_default()
        self.system_theme = self.settings.get_property("gtk-theme-name")

    def apply(self, name: str) -> None:
        """Applica un tema; SYSTEM torna al tema di sistema."""
        self.settings.set_property("gtk-theme-name", name or self.system_theme)
