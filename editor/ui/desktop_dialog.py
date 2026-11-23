"""Dialogo dell'integrazione con il desktop Linux (editor/desktop.py).

2026-11-23: richiesta dell'utente. Due usi:
- all'avvio, se non è mai stato chiesto: Yes installa, No salva la scelta e non lo chiede più;
- da Help > Desktop Integration…: rifà la domanda da capo, con Remove se l'integrazione c'è.
Finestra propria e non Gtk.AlertDialog, che non ha caselle: la casella sceglie se Canovaccio diventa anche
l'app predefinita di MIDI, MusicXML, MuseScore e stili Yamaha (scelta dell'utente: chiedere)."""

from __future__ import annotations

import logging
from html import escape
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gtk  # noqa: E402

from .. import APP_NAME, desktop  # noqa: E402
from . import preferences  # noqa: E402

log = logging.getLogger(__name__)


def _home(path: Path) -> str:
    """Percorso da mostrare, con ~ al posto della cartella dell'utente."""
    try:
        return "~/" + str(path.relative_to(Path.home()))
    except ValueError:
        return str(path)


def _large(text: str) -> str:
    """Markup di Pango per un testo grande (size large), con i caratteri speciali protetti."""
    return f'<span size="large">{escape(text)}</span>'


class DesktopDialog(Gtk.Window):
    """Domanda Yes/No all'avvio (`startup` True) o dal menu Help (Install / Remove / Cancel)."""

    def __init__(self, parent, target: Path, startup: bool) -> None:
        super().__init__(title="Desktop Integration", transient_for=parent, modal=True, resizable=False)
        self.parent_window = parent
        self.target = target
        self.startup = startup
        installed = desktop.is_installed()

        # 2026-11-23: testi più grandi (l'utente li trovava troppo piccoli): titolo x-large, testo e casella large,
        # con il markup di Pango come in about_dialog.py (le classi CSS di dimensione esistono solo in Adwaita)
        title = Gtk.Label(xalign=0)
        title.set_markup(f'<span size="x-large" weight="bold">Add {escape(APP_NAME)} to your desktop?</span>')
        text = Gtk.Label(xalign=0, wrap=True, max_width_chars=60)
        text.set_markup(_large(
            f"{APP_NAME} can add itself to the applications menu, show its icon on its files "
            "(.music, .stile, .sty, .clb), open projects and styles with a double click and install the "
            f"\"{desktop.link_path().name}\" command in {_home(desktop.bin_dir())}.\n"
            "Everything goes in your home folder: no administrator rights are needed."))
        # dove punta oggi il comando e quale programma sta girando: chi ha sorgenti e AppImage vede quale usa
        linked = desktop.linked_target()
        lines = [f"This program: {self.target}"]
        if installed and linked is not None:
            lines.append(f"Installed: {_home(desktop.link_path())} → {linked}")
        state = Gtk.Label(xalign=0, wrap=True, max_width_chars=60, selectable=True)
        state.set_markup(_large("\n".join(lines)))
        # casella con un'etichetta propria, per poterla ingrandire come il resto
        others = Gtk.Label(xalign=0, wrap=True, max_width_chars=60)
        others.set_markup(_large("Also open MIDI, MusicXML, MuseScore and Yamaha style files with Canovaccio by default"))
        self.open_others = Gtk.CheckButton(child=others, active=preferences.desktop_open_others())

        # pulsanti: all'avvio No / Yes; dal menu Remove (se installata) a sinistra, Cancel / Install a destra
        buttons = Gtk.Box(spacing=8, margin_top=12)
        if not startup and installed:
            buttons.append(self._button("Remove", self._remove))
        spacer = Gtk.Box(hexpand=True)
        buttons.append(spacer)
        buttons.append(self._button("No" if startup else "Cancel", self._no))
        install = self._button("Yes" if startup else ("Update" if installed else "Install"), self._install)
        install.add_css_class("suggested-action")
        buttons.append(install)

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10,
                      margin_start=24, margin_end=24, margin_top=20, margin_bottom=20)
        for widget in (title, text, state, self.open_others, buttons):
            box.append(widget)
        self.set_child(box)
        self.set_default_widget(install)
        # Esc chiude senza scegliere (all'avvio la domanda torna la volta dopo)
        esc = Gtk.ShortcutController()
        esc.add_shortcut(Gtk.Shortcut.new(Gtk.ShortcutTrigger.parse_string("Escape"), Gtk.NamedAction.new("window.close")))
        self.add_controller(esc)

    def _button(self, label: str, callback) -> Gtk.Button:
        """Pulsante del dialogo, con il testo grande come il resto."""
        text = Gtk.Label()
        text.set_markup(_large(label))
        button = Gtk.Button(child=text)
        button.connect("clicked", lambda *_: callback())
        return button

    def _install(self) -> None:
        """Yes / Install / Update: installa, salva la scelta e dice com'è andata."""
        open_others = self.open_others.get_active()
        try:
            warnings = desktop.install(self.target, open_others=open_others)
        except OSError as exc:
            self.close()
            self.parent_window.show_message(f"Desktop integration failed: {exc}", error=True)
            return
        preferences.set_desktop_integration("yes", open_others)
        self.close()
        detail = "\n\n".join(warnings) if warnings else "It may take a moment to show up in the menu."
        self.parent_window._alert(f"{APP_NAME} is now in the applications menu.", detail)

    def _no(self) -> None:
        """No all'avvio: non si chiede più. Cancel dal menu: non cambia niente."""
        if self.startup:
            preferences.set_desktop_integration("no")
        self.close()

    def _remove(self) -> None:
        """Remove: toglie l'integrazione e all'avvio non la ripropone (come un No)."""
        try:
            desktop.uninstall()
        except OSError as exc:
            self.close()
            self.parent_window.show_message(f"Removing the desktop integration failed: {exc}", error=True)
            return
        preferences.set_desktop_integration("no")
        self.close()
        self.parent_window.show_message(f"{APP_NAME} was removed from the applications menu.", error=False)


def check_at_startup(window) -> bool:
    """All'avvio (una volta, dopo aver mostrato la finestra): se non è mai stato chiesto apre la domanda;
    se è stato accettato e il link è da rifare (AppImage nuovo o spostato) lo rifà in silenzio, senza toccare
    le app predefinite. Restituisce False per GLib.idle_add."""
    target = desktop.launcher_target()
    answer = preferences.desktop_integration()
    if target is None or answer == "no":
        return False
    if answer == "yes":
        if desktop.should_refresh(target):
            try:
                desktop.install(target, set_defaults=False)
                log.info("integrazione col desktop aggiornata: %s", target)
            except OSError as exc:
                log.warning("integrazione col desktop non aggiornata: %s", exc)
        return False
    DesktopDialog(window, target, startup=True).present()
    return False
