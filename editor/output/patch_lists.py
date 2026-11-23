"""Liste di suoni (patch) per le uscite MIDI: nome, categoria, Bank Select MSB/LSB e Program.

A strati:
- base: le definizioni di Rosegarden (.rgd, XML compresso con gzip) copiate in
  `resources/instruments/rosegarden/` (oggi solo GM);
- correzioni: file JSON in `resources/instruments/` ricavati dai manuali ufficiali.
  Con `"base"` correggono una lista Rosegarden: con `"replace_banks"` i banchi
  (MSB/LSB) che contengono sostituiscono per intero quelli della base, poi ogni voce
  sostituisce o aggiunge il suono con gli stessi MSB/LSB/Program. Senza base sono
  liste nuove. Il nome della lista è quello del JSON; la base corretta non compare
  più da sola. `"exclude_msb"`: banchi della base da togliere (es. le Scene, MSB 85).

Program: 0–127 come in MIDI (nel JSON "pc" 1–128 come sui manuali; nei .rgd "id" 0–127).
"""

from __future__ import annotations

import gzip
import json
import logging
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

log = logging.getLogger(__name__)

DIR = Path(__file__).resolve().parent.parent / "resources" / "instruments"
RGD_DIR = DIR / "rosegarden"


@dataclass(frozen=True)
class Patch:
    name: str
    category: str
    msb: int
    lsb: int
    program: int  # 0–127
    drum: bool = False
    group: str = ""  # banco o espansione (es. "PR-A", "EXZ001")
    source: str = ""  # "Rosegarden" o il file di correzione

    @property
    def key(self) -> tuple[int, int, int]:
        return self.msb, self.lsb, self.program

    def label(self) -> str:
        """Testo per gli elenchi: "Nylon Guitar · Ac.Guitar  (87/93/79)" (PC 1–128)."""
        cat = f" · {self.category}" if self.category else ""
        return f"{self.name}{cat}  ({self.msb}/{self.lsb}/{self.program + 1})"


def read_rgd(path: Path) -> list[Patch]:
    """Suoni di una definizione di Rosegarden (primo device MIDI del file)."""
    root = ET.fromstring(gzip.decompress(path.read_bytes()))
    patches = []
    for bank in root.iter("bank"):
        if bank.get("send") is not None or bank.get("msb") is None:
            continue  # riferimenti di banco (non definizioni)
        msb, lsb = int(bank.get("msb", 0)), int(bank.get("lsb", 0))
        drum = bank.get("percussion") == "true"
        for prog in bank.iter("program"):
            pid = int(prog.get("id", -1))
            if 0 <= pid <= 127:
                patches.append(Patch(prog.get("name", "").strip(), bank.get("name", ""), msb, lsb, pid, drum,
                                     bank.get("name", ""), "Rosegarden"))
    return patches


def _overlays() -> dict[str, Path]:
    out = {}
    for p in sorted(DIR.glob("*.json")):
        try:
            out[json.loads(p.read_text(encoding="utf-8"))["name"]] = p
        except (OSError, ValueError, KeyError) as exc:
            log.warning("lista %s non leggibile: %s", p.name, exc)
    return out


def list_names() -> list[str]:
    """Liste disponibili: quelle di Rosegarden (per nome del file) e quelle corrette."""
    overlays = _overlays()
    replaced = set()
    for path in overlays.values():
        try:
            replaced.add(json.loads(path.read_text(encoding="utf-8")).get("base", ""))
        except (OSError, ValueError):
            pass
    names = [p.stem for p in sorted(RGD_DIR.glob("*.rgd")) if p.stem not in replaced]
    return names + [n for n in overlays if n not in names]


@lru_cache(maxsize=16)
def load(name: str) -> tuple[Patch, ...]:
    """Suoni della lista, ordinati per MSB/LSB/Program; vuota se non c'è."""
    overlay = _overlays().get(name)
    base_name = name
    data = None
    if overlay is not None:
        data = json.loads(overlay.read_text(encoding="utf-8"))
        base_name = data.get("base", "")
    rgd = RGD_DIR / f"{base_name}.rgd"
    patches: dict[tuple[int, int, int], Patch] = {}
    if base_name and rgd.is_file():
        try:
            patches = {p.key: p for p in read_rgd(rgd)}
        except (OSError, ET.ParseError, ValueError) as exc:
            log.warning("lista Rosegarden %s non leggibile: %s", rgd.name, exc)
    if data is not None:
        entries = [Patch(e["name"], e.get("category", ""), e["msb"], e["lsb"], e["pc"] - 1, e.get("drum", False),
                         e.get("group", ""), overlay.name) for e in data.get("entries", [])]
        excluded = set(data.get("exclude_msb", []))
        patches = {k: p for k, p in patches.items() if p.msb not in excluded}
        if data.get("replace_banks"):
            banks = {(p.msb, p.lsb) for p in entries}
            patches = {k: p for k, p in patches.items() if (p.msb, p.lsb) not in banks}
        patches.update({p.key: p for p in entries})
    return tuple(sorted(patches.values(), key=lambda p: p.key))
