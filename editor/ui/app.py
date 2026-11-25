"""Applicazione GTK 4 (senza libadwaita: usa il tema GTK di sistema)."""

from __future__ import annotations

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
from pathlib import Path  # noqa: E402

from gi.repository import Gdk, Gio, GLib, Gtk  # noqa: E402

from .. import APP_ID  # noqa: E402
from ..output.devices import load_devices  # noqa: E402
from ..session import Session  # noqa: E402
from . import preferences  # noqa: E402
from .fonts import register_music_font  # noqa: E402
from .desktop_dialog import check_at_startup as check_desktop_integration  # noqa: E402
from .main_window import MainWindow, build_menubar  # noqa: E402

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
/* 2026-11-24: toolbar che va a capo (toolbar.Toolbar): ogni palette in una scatola "palette"; il divisore fra due
   palette della stessa riga è un'immagine di sfondo larga 1 px (non cambia le misure: niente relayout) */
.toolbar > .palette { padding: 0 8px; }
.toolbar > .palette.after-palette {
  background-image: linear-gradient(alpha(currentColor, 0.3), alpha(currentColor, 0.3));
  background-size: 1px 70%; background-position: left center; background-repeat: no-repeat;
}
.status-error { color: #c01c28; font-weight: bold; }
.listen-toggle { min-width: 22px; min-height: 22px; padding: 0; }
.note-key { font-size: 8px; margin: 1px 0 0 2px; }  /* numero del tasto sui valori di nota */
/* 2026-11-20: niente testo attenuato (dev/DESIGN.md, "Testi"): tolti opacity qui e "dim-label" nelle etichette. */
.listen-toggle:checked { background: alpha(currentColor, 0.35); }  /* acceso ben visibile anche sulla riga selezionata */
row:selected .listen-toggle { color: black; }  /* M e S neri anche sulla traccia selezionata */
/* 2026-11-23: riga intera della traccia in Solo gialla, in Mute rossa (richiesta dell'utente). Colori
   semitrasparenti: si leggono su temi chiari e scuri. Anche sulla riga selezionata (prima esclusa: il Mute
   della traccia selezionata non si vedeva); la selezione resta riconoscibile dal bordo del tema. */
row.track-solo { background-color: alpha(#f6d32d, 0.45); }
row.track-mute { background-color: alpha(#e01b24, 0.25); }
/* 2026-11-24: mixer di prova: colonne separate da una linea; M rosso e S giallo quando accesi (come i colori delle
   righe in Mute e in Solo); lucchetto evidenziato con Lock; numeri a cifre fisse */
.mixer-strip { padding: 8px 4px; border-right: 1px solid alpha(currentColor, 0.15); }
.mixer-strip:last-child { border-right: none; }
.mixer-channel, .mixer-value { font-feature-settings: "tnum"; }
.mixer-listen { min-width: 20px; min-height: 18px; padding: 0; border-radius: 0; }
.mixer-mute:checked { background: alpha(#e01b24, 0.45); color: black; }
.mixer-solo:checked { background: alpha(#f6d32d, 0.8); color: black; }
.mixer-mode:checked { background: alpha(@theme_selected_bg_color, 0.35); }
.mixer-write:checked { background: alpha(#e66100, 0.6); color: black; }
.mixer-delete { min-width: 22px; min-height: 22px; padding: 0; }
.mixer-name-box { background: alpha(currentColor, 0.06); }
.mixer-name { padding: 2px 2px; font-size: smaller; }
/* 2026-11-23: traccia selezionata con un bordo del colore di selezione del tema (richiesta dell'utente):
   resta riconoscibile anche sopra il giallo del Solo e il rosso del Mute */
row.track-row:selected { outline: 2px solid @theme_selected_bg_color; outline-offset: -2px; }
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
            # 2026-11-23: integrazione col desktop Linux (desktop_dialog.py): la domanda o l'aggiornamento del
            # link, dopo aver mostrato la finestra; non nei test (che passano un backend)
            if not self.backend_name:
                GLib.idle_add(check_desktop_integration, self.window)
        return self.window

    def do_activate(self) -> None:
        """Avvio senza file da aprire: alla prima finestra riapre il file aperto alla chiusura."""
        first = self.window is None
        win = self._ensure_window()
        win.present()
        # 2026-11-23: riapre il file che era aperto quando l'app è stata chiusa (richiesta dell'utente);
        # solo all'avvio (non a un secondo avvio con la finestra già aperta) e solo se il file esiste ancora.
        # Con un file sulla riga di comando passa da do_open e questo non succede.
        last = preferences.last_open() if first else None
        if last and Path(last).is_file():
            GLib.idle_add(lambda: win.open_path(last) or False)  # dopo aver mostrato la finestra

    def do_open(self, files, _n, _hint) -> None:
        win = self._ensure_window()
        win.present()
        if files and files[0].get_path():
            path = files[0].get_path()
            # 2026-11-20: un file aperto dall'esterno (secondo avvio, file manager) arriva alla
            # finestra già aperta: prima si chiede di salvare le modifiche, come con File > Open.
            win.after_saving_changes(lambda: win.open_path(path))
