"""Tasto dei comandi della piattaforma per i controlli fatti a mano sugli eventi di tastiera e mouse.

2026-11-23: per la build macOS (scelta dell'utente). Le scorciatoie dei menu usano "<Primary>", che GTK traduce
in Ctrl su Linux e Windows e in Cmd su macOS; i controlli diretti dei modificatori (Ctrl+clic, Ctrl+tasto nelle
viste) usano PRIMARY_MASK, così sul Mac rispondono a Cmd come il resto del sistema."""

from __future__ import annotations

import gi

gi.require_version("Gdk", "4.0")
from gi.repository import Gdk  # noqa: E402

from ..system import is_mac  # noqa: E402

PRIMARY_MASK = Gdk.ModifierType.META_MASK if is_mac() else Gdk.ModifierType.CONTROL_MASK
