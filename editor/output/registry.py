"""Scelta del backend d'uscita: da un'uscita configurata (devices.OutputDevice) o, per i
test, per nome ("null", "fluidsynth")."""

from __future__ import annotations

import logging
from typing import Callable

from .backend import NullBackend, OutputBackend
from .devices import MIDI, OutputDevice

log = logging.getLogger(__name__)


def _fluidsynth() -> OutputBackend:
    from .fluidsynth_backend import FluidSynthBackend

    return FluidSynthBackend()


BACKENDS: dict[str, Callable[[], OutputBackend]] = {
    "fluidsynth": _fluidsynth,
    "null": NullBackend,
}
DEFAULT_ORDER = ["fluidsynth", "null"]


def open_backend(name: str | None = None) -> tuple[OutputBackend, list[str]]:
    """Apre il backend richiesto o il primo disponibile. Ritorna (backend, errori incontrati)."""
    errors: list[str] = []
    for candidate in [name] if name else DEFAULT_ORDER:
        try:
            backend = BACKENDS[candidate]()
            backend.open()
            return backend, errors
        except Exception as exc:  # noqa: BLE001 — si passa al prossimo backend
            log.warning("backend %s non disponibile: %s", candidate, exc)
            errors.append(f"{candidate}: {exc}")
    backend = NullBackend()
    return backend, errors


def open_device(device: OutputDevice) -> OutputBackend:
    """Apre l'uscita configurata; solleva un'eccezione (con un messaggio leggibile) se non va."""
    if device.kind == MIDI:
        from .midi_port_backend import MidiPortBackend

        backend: OutputBackend = MidiPortBackend(device.port, device.program_change)
    else:
        from .fluidsynth_backend import FluidSynthBackend

        backend = FluidSynthBackend(device.soundfont or None, device.driver or None)
    backend.output_name = device.name
    backend.open()
    return backend
