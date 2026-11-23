"""Differenze fra sistemi operativi (Linux, Windows, macOS): cartella delle preferenze,
dove cercare i SoundFont, driver audio di FluidSynth."""

from __future__ import annotations

import os
import sys
from pathlib import Path

from . import APP_DIR


def is_windows() -> bool:
    return sys.platform.startswith("win")


def is_mac() -> bool:
    return sys.platform == "darwin"


def config_dir() -> Path:
    """Cartella delle preferenze dell'app: GLib.get_user_config_dir() dà quella giusta su
    ogni sistema (Windows, macOS); su Linux $XDG_CONFIG_HOME o ~/.config, letto ogni volta
    (GLib lo legge una volta sola)."""
    if is_windows() or is_mac():
        from gi.repository import GLib

        return Path(GLib.get_user_config_dir()) / APP_DIR
    base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / APP_DIR


def audio_drivers() -> list[str]:
    """Driver audio di FluidSynth per questo sistema; "automatico" prova i primi in ordine."""
    if is_windows():
        return ["wasapi", "dsound", "waveout"]
    if is_mac():
        return ["coreaudio", "portaudio"]
    # PulseAudio prima: su PipeWire passa da pipewire-pulse; il driver "pipewire" di
    # FluidSynth 2.3 fallisce se il processo non ha chiamato pw_init().
    return ["pulseaudio", "pipewire", "alsa", "jack"]


def auto_drivers() -> list[str]:
    """Driver provati con "automatico": tutti tranne jack (va scelto apposta)."""
    return [d for d in audio_drivers() if d != "jack"]


def soundfont_dirs() -> list[Path]:
    """Cartelle in cui cercare i SoundFont (.sf2/.sf3)."""
    home = Path.home()
    if is_windows():
        dirs = [home / "SoundFonts", home / "Documents" / "SoundFonts", Path("C:/soundfonts")]
    elif is_mac():
        dirs = [home / "Library" / "Audio" / "Sounds" / "Banks", Path("/opt/homebrew/share/soundfonts"),
                Path("/usr/local/share/soundfonts"), Path("/opt/homebrew/share/fluid-synth/sf2")]
    else:
        dirs = [Path("/usr/share/sounds/sf2"), Path("/usr/share/soundfonts"), Path("/usr/share/sounds/sf3"),
                home / ".local" / "share" / "soundfonts", home / ".local" / "share" / "sounds" / "sf2"]
    return dirs
