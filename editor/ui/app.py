"""Applicazione GTK 4 (senza libadwaita: usa il tema GTK di sistema)."""

from __future__ import annotations

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
from pathlib import Path  # noqa: E402

from gi.repository import Gdk, Gio, Gtk  # noqa: E402

from ..output.devices import load_devices  # noqa: E402
from ..session import Session  # noqa: E402
from . import preferences  # noqa: E402
from .fonts import register_music_font  # noqa: E402
from .main_window import MainWindow, build_menubar  # noqa: E402

APP_ID = "io.github.todomusician.Editor"
ICON_DIR = Path(__file__).resolve().parent.parent / "resources" / "icons"

# Sopra il tema di sistema: solo niente angoli arrotondati (menu, elenchi a
# discesa, pulsanti, schede, angoli delle finestre). Colori e resto: quelli del tema.
SQUARE_CSS = """
popover > contents, popover.menu > contents { border-radius: 0; }
popover modelbutton, menubar > item { border-radius: 0; }
button, stackswitcher > button, dropdown > button { border-radius: 0; }
notebook > header tab { border-radius: 0; }
window, window.csd, window.csd.background, window.dialog.csd, window > decoration,
window.csd > decoration, window.csd > .titlebar, window.csd > headerbar,
headerbar, .titlebar { border-radius: 0; }
"""

# Toolbar: pulsanti tutti uguali, separatore visibile, posizione in un riquadro.
TOOLBAR_CSS = """
.toolbar button { min-width: 32px; min-height: 32px; padding: 0; }
.toolbar separator { margin: 4px 8px; min-width: 1px; background: alpha(currentColor, 0.3); }
.status-error { color: #c01c28; font-weight: bold; }
.listen-toggle { min-width: 22px; min-height: 22px; padding: 0; }
.note-key { font-size: 8px; margin: 1px 0 0 2px; }  /* numero del tasto sui valori di nota */
/* 2026-11-20: niente testo attenuato (dev/DESIGN.md, "Testi"): tolti opacity qui e "dim-label" nelle etichette. */
.listen-toggle:checked { background: alpha(currentColor, 0.35); }  /* acceso ben visibile anche sulla riga selezionata */
row:selected .listen-toggle { color: black; }  /* M e S neri anche sulla traccia selezionata */
.toolbar .position {
  padding: 3px 10px;
  border: 1px solid alpha(currentColor, 0.3);
  font-feature-settings: "tnum";
}
"""


class EditorApp(Gtk.Application):
    def __init__(self, backend: str | None = None) -> None:
        # `backend` serve solo ai test (es. "null"); di solito l'uscita è quella scelta
        # nella tendina accanto al player (preferenze, File > Output Devices…).
        super().__init__(application_id=APP_ID, flags=Gio.ApplicationFlags.HANDLES_OPEN)
        self.backend_name = backend
        self.session: Session | None = None
        self.window: MainWindow | None = None

    def do_startup(self) -> None:
        Gtk.Application.do_startup(self)
        display = Gdk.Display.get_default()
        Gtk.IconTheme.get_for_display(display).add_search_path(str(ICON_DIR))
        css = Gtk.CssProvider()
        css.load_from_string(SQUARE_CSS + TOOLBAR_CSS)
        # sopra anche a ~/.config/gtk-4.0/gtk.css (priorità USER), che alcuni temi installano:
        # altrimenti le regole qui sotto possono non avere effetto
        Gtk.StyleContext.add_provider_for_display(display, css, Gtk.STYLE_PROVIDER_PRIORITY_USER + 100)
        Gtk.Window.set_default_icon_name(APP_ID)  # icona dell'app (resources/icons/hicolor/scalable/apps)
        self.set_menubar(build_menubar())

    def _ensure_window(self) -> MainWindow:
        if self.window is None:
            register_music_font()
            device = None if self.backend_name else load_devices(preferences.load())[1]
            self.session = Session(self.backend_name, device)
            self.window = MainWindow(self, self.session)
        return self.window

    def do_activate(self) -> None:
        self._ensure_window().present()

    def do_open(self, files, _n, _hint) -> None:
        win = self._ensure_window()
        win.present()
        if files and files[0].get_path():
            path = files[0].get_path()
            # 2026-11-20: un file aperto dall'esterno (secondo avvio, file manager) arriva alla
            # finestra già aperta: prima si chiede di salvare le modifiche, come con File > Open.
            win.after_saving_changes(lambda: win.open_path(path))
