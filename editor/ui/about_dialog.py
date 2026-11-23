"""Dialogo Help > About: logo, nome del programma, versione, autore con il suo sito, sito del progetto.

2026-11-23: finestra propria al posto di Gtk.AboutDialog, scelta dell'utente: l'About deve pubblicizzare
l'autore (con il link al suo sito), senza schede, descrizione né riga del copyright.
Gtk.AboutDialog non permette nessuna di queste cose (il nome grande è solo quello del programma, ogni
sezione dei crediti apre una scheda, accetta un solo link)."""

from __future__ import annotations

from html import escape

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gtk  # noqa: E402

from .. import APP_ID, APP_NAME, APP_WEBSITE, AUTHOR, AUTHOR_WEBSITE, __version__  # noqa: E402


class AboutDialog(Gtk.Window):
    """Finestra About, modale e non ridimensionabile; si chiude dalla barra del titolo o con Esc."""

    def __init__(self, parent: Gtk.Window) -> None:
        super().__init__(title=f"About {APP_NAME}", transient_for=parent, modal=True, resizable=False)
        # 2026-11-23: schema dato dall'utente, una riga sotto l'altra e centrate: logo, nome del programma
        # in grande, versione e sito del progetto (stessa dimensione); staccato sotto, "By autore - sito" grande
        # (pubblicizza l'autore). Senza pulsante Close: si chiude dalla barra del titolo o con Esc.
        logo = Gtk.Image(icon_name=APP_ID, pixel_size=128, margin_bottom=12)
        # dimensioni con il markup di Pango e non con classi CSS come "title-1": quelle esistono
        # solo nel tema Adwaita, e l'app segue il tema GTK di sistema
        program = Gtk.Label()
        program.set_markup(f'<span size="xx-large" weight="bold">{escape(APP_NAME)}</span>')
        version = Gtk.Label(label=f"v. {__version__}")
        # link nel testo con <a href>: Gtk.Label li apre col browser predefinito
        project = Gtk.Label()
        project.set_markup(f'Project website: <a href="{escape(APP_WEBSITE)}">{escape(APP_WEBSITE)}</a>')
        author = Gtk.Label(margin_top=28)
        author.set_markup(f'<span size="large">By <b>{escape(AUTHOR)}</b> - '
                          f'<a href="{escape(AUTHOR_WEBSITE)}">{escape(AUTHOR_WEBSITE)}</a></span>')
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6,
                      margin_start=48, margin_end=48, margin_top=32, margin_bottom=32)
        for widget in (logo, program, version, project, author):
            box.append(widget)
        self.set_child(box)
        esc = Gtk.ShortcutController()
        esc.add_shortcut(Gtk.Shortcut.new(Gtk.ShortcutTrigger.parse_string("Escape"), Gtk.NamedAction.new("window.close")))
        self.add_controller(esc)
