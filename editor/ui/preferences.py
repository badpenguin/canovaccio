"""Preferenze dell'app in settings.json, nella cartella di configurazione del sistema
(Linux: $XDG_CONFIG_HOME/canovaccio; Windows: %APPDATA%; macOS: Library/Application Support).
Oggi: tema, uscite (File > Output Devices…), uscita in uso, ponte audio, file recenti e integrazione col desktop."""

from __future__ import annotations

import json
import logging
from pathlib import Path

from ..system import config_dir

log = logging.getLogger(__name__)


def settings_file() -> Path:
    return config_dir() / "settings.json"


def load() -> dict:
    try:
        return json.loads(settings_file().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def save(**values) -> None:
    """Aggiorna le chiavi indicate e riscrive il file."""
    data = load()
    data.update(values)
    path = settings_file()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    except OSError as exc:
        log.warning("preferenze non salvate: %s", exc)


MAX_RECENT = 10


def recent_files() -> list[str]:
    """File aperti, salvati o esportati di recente (il più recente per primo)."""
    return [p for p in load().get("recent", []) if isinstance(p, str)]


def add_recent(path: str) -> list[str]:
    """Mette il file in cima ai recenti (senza doppioni, al massimo MAX_RECENT)."""
    full = str(Path(path).expanduser().resolve())
    files = [full] + [p for p in recent_files() if p != full]
    save(recent=files[:MAX_RECENT])
    return files[:MAX_RECENT]


def last_open() -> str | None:
    """File aperto quando l'app è stata chiusa (None: nessuno), da riaprire all'avvio."""
    path = load().get("last_open")
    return path if isinstance(path, str) else None


def set_last_open(path: str | None) -> None:
    """Ricorda il file aperto alla chiusura dell'app (None: nessun file)."""
    save(last_open=str(Path(path).expanduser().resolve()) if path else None)


def remove_recent(path: str) -> list[str]:
    files = [p for p in recent_files() if p != path]
    save(recent=files)
    return files


def desktop_integration() -> str | None:
    """Risposta alla domanda sull'integrazione col desktop (editor/desktop.py): "yes", "no" o None (mai chiesto).
    2026-11-23: con "no" all'avvio non si chiede più (scelta dell'utente); Help > Desktop Integration… la rifà."""
    value = load().get("desktop_integration")
    return value if value in ("yes", "no") else None


def desktop_open_others() -> bool:
    """Ultima scelta della casella "apri anche MIDI, MusicXML… con Canovaccio" (per riproporla nel dialogo)."""
    return bool(load().get("desktop_open_others", False))


def set_desktop_integration(answer: str, open_others: bool | None = None) -> None:
    """Salva la risposta ("yes" o "no") e, se indicata, la scelta sulle app predefinite."""
    values = {"desktop_integration": answer}
    if open_others is not None:
        values["desktop_open_others"] = open_others
    save(**values)
