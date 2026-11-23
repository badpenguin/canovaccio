"""Integrazione con il desktop Linux (Help > Desktop Integration…), senza GTK e senza permessi di root.

2026-11-23: richiesta dell'utente. Un AppImage da solo non compare nel menu e il sistema non riconosce i file
di Canovaccio; il programma lo chiede all'avvio (una volta: il No resta nelle preferenze) e su richiesta dal
menu Help. Installa, tutto nella cartella dell'utente:

- il comando `~/.local/bin/canovaccio`, un link all'AppImage (o a canovaccio.sh dai sorgenti): il menu punta
  al link, così una nuova versione o un AppImage spostato cambiano solo il link e non il resto;
- la voce del menu delle applicazioni (`.desktop`), con i tipi di file che apre;
- i tipi di file nuovi (.music, .stile, .sty, .clb) con l'icona di Canovaccio;
- l'icona del programma nel tema hicolor dell'utente.

Canovaccio diventa sempre l'app predefinita dei suoi progetti e stili (.music, .stile), che nessun altro
programma apre; per MIDI, MusicXML, MuseScore e stili Yamaha solo se l'utente lo sceglie (scelta dell'utente).
Il README rimanda qui al posto delle istruzioni a mano di prima ("Menu launcher", "File icons")."""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import sys
from pathlib import Path

from . import APP_COMMAND, APP_DIR, APP_ID

log = logging.getLogger(__name__)

# Tipi di file nuovi registrati per l'utente: (tipo, descrizione, tipo da cui deriva, estensione, magic).
# 2026-11-23: gli stessi che prima il README faceva copiare a mano. Il .sty si riconosce dal contenuto
# (comincia come un MIDI), così i .sty di LaTeX restano dei loro; il .clb ha solo l'icona (si importa).
NEW_TYPES = (
    ("application/x-canovaccio-project", "Canovaccio project", "application/gzip", "*.music", None),
    ("application/x-canovaccio-style", "Canovaccio style", "application/gzip", "*.stile", None),
    ("audio/x-yamaha-style", "Yamaha keyboard style", "audio/midi", "*.sty", "MThd"),
    ("application/x-yamaha-chord-looper", "Yamaha Chord Looper bank", None, "*.clb", None),
)
# tipi di cui Canovaccio è sempre l'app predefinita (solo lui li apre)
OWN_TYPES = ("application/x-canovaccio-project", "application/x-canovaccio-style")
# tipi che apre anche, già usati da altri programmi: predefinito solo se l'utente lo sceglie
OTHER_TYPES = ("audio/midi", "audio/x-midi", "application/vnd.recordare.musicxml+xml",
               "application/vnd.recordare.musicxml", "application/x-musescore", "audio/x-yamaha-style")

ICON = Path(__file__).resolve().parent / "resources" / "icons" / "hicolor" / "scalable" / "apps" / f"{APP_ID}.svg"


def data_home() -> Path:
    """Cartella dei dati dell'utente (XDG): $XDG_DATA_HOME o ~/.local/share."""
    return Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share")


def bin_dir() -> Path:
    """Cartella dei comandi dell'utente: ~/.local/bin (specifica XDG, non ha una variabile d'ambiente)."""
    return Path.home() / ".local" / "bin"


def link_path() -> Path:
    return bin_dir() / APP_COMMAND


def desktop_path() -> Path:
    return data_home() / "applications" / f"{APP_ID}.desktop"


def mime_path() -> Path:
    # stesso nome che usava il README per l'installazione a mano: la sostituisce
    return data_home() / "mime" / "packages" / f"{APP_DIR}.xml"


def icon_path() -> Path:
    return data_home() / "icons" / "hicolor" / "scalable" / "apps" / f"{APP_ID}.svg"


def launcher_target() -> Path | None:
    """Il programma da lanciare dal menu: l'AppImage ($APPIMAGE, lo imposta il runtime di AppImage), l'eseguibile
    di PyInstaller fuori da un AppImage, oppure canovaccio.sh se gira dai sorgenti. None se non c'è niente da
    collegare (non Linux, o installato in altro modo, senza canovaccio.sh)."""
    if not sys.platform.startswith("linux"):
        return None
    appimage = os.environ.get("APPIMAGE")
    if appimage:
        return Path(appimage)
    if getattr(sys, "frozen", False):
        return Path(sys.executable)
    script = Path(__file__).resolve().parent.parent / "canovaccio.sh"
    return script if script.is_file() else None


def linked_target() -> Path | None:
    """Dove punta oggi ~/.local/bin/canovaccio (None: non c'è o non è un link)."""
    link = link_path()
    return Path(os.readlink(link)) if link.is_symlink() else None


def is_installed() -> bool:
    """L'integrazione c'è: voce del menu e tipi di file installati."""
    return desktop_path().is_file() and mime_path().is_file()


def link_is_current(target: Path) -> bool:
    """Il link punta già al programma che gira adesso."""
    return linked_target() == target


def should_refresh(target: Path) -> bool:
    """All'avvio, con l'integrazione accettata: va rifatta in silenzio?
    Sì se manca un pezzo, o se il link punta a un programma che non c'è più (AppImage spostato o cancellato)
    o a un altro AppImage (nuova versione). 2026-11-23: no se il link punta a un altro programma che esiste e
    non è un AppImage (es. i sorgenti accanto all'AppImage): si cambia solo da Help > Desktop Integration…,
    così chi ha due installazioni non se le vede scambiare a ogni avvio."""
    if not is_installed():
        return True
    old = linked_target()
    if old is None or old == target:
        return old is None and not link_path().exists()
    if not old.exists():
        return True
    return old.suffix.lower() == ".appimage" and target.suffix.lower() == ".appimage"


def mime_xml() -> str:
    """Definizioni dei tipi di file nuovi (shared-mime-info). Le righe generic-icon servono: senza, molti temi
    di icone mostrano un foglio bianco al posto dell'icona di Canovaccio (verificato 2026-11-23)."""
    lines = ['<?xml version="1.0" encoding="UTF-8"?>',
             '<mime-info xmlns="http://www.freedesktop.org/standards/shared-mime-info">']
    for mime, comment, parent, glob, magic in NEW_TYPES:
        lines.append(f'  <mime-type type="{mime}">')
        lines.append(f"    <comment>{comment}</comment>")
        if parent:
            lines.append(f'    <sub-class-of type="{parent}"/>')
        if magic:
            lines.append(f'    <magic priority="10"><match type="string" offset="0" value="{magic}"/></magic>')
        lines.append(f'    <glob pattern="{glob}"/>')
        lines.append(f'    <icon name="{APP_ID}"/>')
        lines.append(f'    <generic-icon name="{APP_ID}"/>')
        lines.append("  </mime-type>")
    lines.append("</mime-info>")
    return "\n".join(lines) + "\n"


def _exec_quote(path: Path) -> str:
    """Percorso per la riga Exec del .desktop: tra virgolette se contiene spazi o caratteri riservati
    (specifica Desktop Entry), con le barre rovesciate raddoppiate due volte (virgolette e valore stringa)."""
    text = str(path)
    if not any(c in text for c in " \t\n\"'\\><~|&;$*?#()`"):
        return text
    for c in ("\\", '"', "`", "$"):
        text = text.replace(c, "\\" + c)
    return '"' + text.replace("\\", "\\\\") + '"'


def desktop_entry(command: Path) -> str:
    """Voce del menu delle applicazioni; `command` è il link in ~/.local/bin (o il programma, se il link non si
    è potuto creare). Exec con il percorso completo: l'ambiente grafico non sempre ha ~/.local/bin nel PATH."""
    return "\n".join([
        "[Desktop Entry]",
        "Type=Application",
        "Name=Canovaccio",
        "Comment=MIDI sequencer with grid, notation, chord track and Yamaha styles",
        f"Exec={_exec_quote(command)} %f",
        f"Icon={APP_ID}",
        "Terminal=false",
        "Categories=AudioVideo;Audio;Midi;Sequencer;Music;",
        "MimeType=" + "".join(f"{t};" for t in (*OWN_TYPES, *OTHER_TYPES)),
        f"StartupWMClass={APP_ID}",
        "StartupNotify=true",
    ]) + "\n"


def _run(*command: str) -> None:
    """Lancia uno strumento del sistema (cache dei tipi di file, del menu, delle icone; app predefinite).
    Se manca o fallisce si va avanti: l'integrazione funziona lo stesso, al massimo dopo un nuovo login."""
    if shutil.which(command[0]) is None:
        log.info("%s non trovato: saltato", command[0])
        return
    # nell'AppImage (PyInstaller) LD_LIBRARY_PATH punta alle librerie del pacchetto: gli strumenti del sistema
    # devono usare le loro, quindi si rimette il valore di prima che PyInstaller salva in LD_LIBRARY_PATH_ORIG
    env = dict(os.environ)
    if getattr(sys, "frozen", False):
        original = env.pop("LD_LIBRARY_PATH_ORIG", None)
        if original:
            env["LD_LIBRARY_PATH"] = original
        else:
            env.pop("LD_LIBRARY_PATH", None)
    try:
        subprocess.run(command, check=False, timeout=30, env=env,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except (OSError, subprocess.SubprocessError) as exc:
        log.warning("%s non riuscito: %s", command[0], exc)


def _update_caches() -> None:
    """Rigenera le cache dell'utente dopo aver aggiunto o tolto i file."""
    _run("update-mime-database", str(data_home() / "mime"))
    _run("update-desktop-database", str(data_home() / "applications"))
    _run("gtk-update-icon-cache", "-f", "-t", str(data_home() / "icons" / "hicolor"))


def install(target: Path, open_others: bool = False, set_defaults: bool = True, tools: bool = True) -> list[str]:
    """Installa (o aggiorna) l'integrazione per `target` (launcher_target()). Restituisce gli avvisi da
    mostrare all'utente (in inglese, come tutta l'interfaccia); OSError se non si può scrivere.
    `open_others`: Canovaccio anche app predefinita di MIDI, MusicXML, MuseScore e stili Yamaha.
    `set_defaults` False: non tocca le app predefinite (aggiornamento in silenzio all'avvio: non deve
    rimettere Canovaccio dove l'utente ha scelto poi un altro programma). `tools` False solo nei test."""
    warnings = []
    # il link: si sostituisce solo se è un link (anche rotto); un file vero dell'utente non si tocca
    link = link_path()
    command = link
    if link.exists() and not link.is_symlink():
        warnings.append(f"{link} already exists and is not a link: it was left as it is, and the menu "
                        f"starts {target} directly.")
        command = target
    else:
        link.parent.mkdir(parents=True, exist_ok=True)
        if link.is_symlink():
            link.unlink()
        link.symlink_to(target)
    # voce del menu, tipi di file, icona
    desktop_path().parent.mkdir(parents=True, exist_ok=True)
    desktop_path().write_text(desktop_entry(command), encoding="utf-8")
    mime_path().parent.mkdir(parents=True, exist_ok=True)
    mime_path().write_text(mime_xml(), encoding="utf-8")
    icon_path().parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(ICON, icon_path())
    if tools:
        _update_caches()
        # app predefinite con xdg-mime (scrive mimeapps.list nel modo giusto per ogni desktop)
        if set_defaults:
            types = OWN_TYPES + (OTHER_TYPES if open_others else ())
            _run("xdg-mime", "default", desktop_path().name, *types)
    # il comando nel terminale funziona solo se ~/.local/bin è nel PATH
    paths = [Path(p) for p in os.environ.get("PATH", "").split(os.pathsep) if p]
    if command == link and bin_dir() not in paths:
        warnings.append(f"To start Canovaccio from a terminal with \"{APP_COMMAND}\", add {bin_dir()} to your "
                        "PATH (on many systems it is enough to log out and back in).")
    return warnings


def uninstall(tools: bool = True) -> None:
    """Toglie tutto quello che install() ha messo: link (solo se è un link), voce del menu, tipi di file, icona.
    Le righe delle app predefinite in mimeapps.list restano, ma puntano a una voce che non c'è più e il
    desktop le ignora."""
    if link_path().is_symlink():
        link_path().unlink()
    for path in (desktop_path(), mime_path(), icon_path()):
        path.unlink(missing_ok=True)
    if tools:
        _update_caches()
