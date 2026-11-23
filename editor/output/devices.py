"""Uscite configurate (File > Output Devices…): synth interno o porta MIDI.

Si salvano nelle preferenze dell'app (non nel brano); la tendina accanto al player
sceglie quella in uso.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from uuid import uuid4

INTERNAL, MIDI = "internal", "midi"
KINDS = [(INTERNAL, "Internal (FluidSynth)"), (MIDI, "MIDI port")]
# nome dell'uscita interna di default (2026-11-20: in inglese, prima "Interno"). Il nome è
# anche la chiave di Track.sounds nei brani: i due nomi valgono come la stessa uscita
# (backend.MidiChannelMap.sound), così i brani salvati con "Interno" tengono i loro suoni.
DEFAULT_INTERNAL_NAME = "Internal"
NAME_ALIASES = {"Internal": "Interno", "Interno": "Internal"}


@dataclass
class OutputDevice:
    name: str
    kind: str = INTERNAL
    soundfont: str = ""  # interno: file .sf2/.sf3; "" = automatico
    driver: str = ""  # interno: driver audio; "" = automatico
    port: str = ""  # MIDI: nome della porta
    program_change: bool = True  # MIDI: manda lo strumento GM della traccia (alcuni device non lo vogliono)
    patch_list: str = "GM"  # MIDI: lista dei suoni del dispositivo (patch_lists), "" = nessuna
    id: str = field(default_factory=lambda: uuid4().hex[:8])

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "OutputDevice":
        known = {k: d[k] for k in cls.__dataclass_fields__ if k in d}
        return cls(**known)


def default_devices() -> list[OutputDevice]:
    return [OutputDevice(DEFAULT_INTERNAL_NAME, id="internal")]


def load_devices(prefs: dict) -> tuple[list[OutputDevice], OutputDevice]:
    """Dalle preferenze: (uscite, uscita in uso). Senza uscite salvate: il synth interno."""
    devices = []
    for d in prefs.get("outputs") or []:
        try:
            devices.append(OutputDevice.from_dict(d))
        except TypeError:
            continue
    devices = devices or default_devices()
    current = next((d for d in devices if d.id == prefs.get("output")), devices[0])
    return devices, current


def dump_devices(devices: list[OutputDevice], current: OutputDevice) -> dict:
    """Chiavi da salvare nelle preferenze."""
    return {"outputs": [d.to_dict() for d in devices], "output": current.id}
