"""Ponte audio (loopback) da un ingresso, es. l'audio USB di una tastiera, all'uscita
predefinita del PC: `pactl load-module module-loopback` (PulseAudio o PipeWire con
pipewire-pulse). Solo Linux.

L'ingresso scelto si ricorda per nome senza il suffisso numerico che può cambiare
a ogni riavvio ("….analog-stereo.2" → "….analog-stereo"). Il ponte resta attivo anche dopo la chiusura
dell'app (fino a "Disattiva" o al riavvio del PC).
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from dataclasses import dataclass

from ..system import is_mac, is_windows

_SUFFIX = re.compile(r"\.\d+$")


@dataclass(frozen=True)
class Source:
    name: str  # es. alsa_input.usb-Roland_FANTOM-6_7_8-01.analog-stereo.2
    description: str  # es. FANTOM-6 7 8 Analog Stereo

    @property
    def stable(self) -> str:
        """Nome senza il suffisso che cambia ai riavvii: per ritrovare l'ingresso."""
        return _SUFFIX.sub("", self.name)


def available() -> bool:
    return not (is_windows() or is_mac()) and shutil.which("pactl") is not None


def _run(*args: str) -> str:
    """Esegue un comando (in inglese: l'output si analizza); errore → RuntimeError leggibile."""
    env = dict(os.environ, LC_ALL="C")
    try:
        done = subprocess.run(args, capture_output=True, text=True, timeout=10, env=env)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RuntimeError(f"{args[0]}: {exc}") from exc
    if done.returncode != 0:
        raise RuntimeError((done.stderr or done.stdout).strip() or f"{' '.join(args)}: error {done.returncode}")
    return done.stdout


def parse_sources(text: str) -> list[Source]:
    """Da `pactl list sources`: gli ingressi veri (non i ".monitor" delle uscite)."""
    out, name = [], None
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("Name:"):
            name = line[5:].strip()
        elif line.startswith("Description:") and name:
            if not name.endswith(".monitor"):
                out.append(Source(name, line[12:].strip()))
            name = None
    return out


def parse_loopbacks(text: str) -> list[tuple[str, str]]:
    """Da `pactl list short modules`: (id, sorgente) dei loopback."""
    out = []
    for line in text.splitlines():
        parts = line.split("\t")
        if len(parts) >= 3 and parts[1] == "module-loopback":
            m = re.search(r"source=(\S+)", parts[2])
            if m:
                out.append((parts[0], m.group(1)))
    return out


def sources() -> list[Source]:
    return parse_sources(_run("pactl", "list", "sources"))


def loopbacks() -> list[tuple[str, str]]:
    return parse_loopbacks(_run("pactl", "list", "short", "modules"))


def bridges(source: str) -> list[str]:
    """Id dei ponti attivi da quell'ingresso."""
    return [mid for mid, src in loopbacks() if src == source]


def load(source: str, latency_ms: int) -> str:
    mid = _run("pactl", "load-module", "module-loopback", f"source={source}",
               f"latency_msec={int(latency_ms)}").strip()
    return mid


def unload(source: str) -> None:
    """Toglie i ponti da quell'ingresso."""
    for mid in bridges(source):
        _run("pactl", "unload-module", mid)


def restart_wireplumber() -> None:
    _run("systemctl", "--user", "restart", "wireplumber")
