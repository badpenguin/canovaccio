"""Genera editor/resources/instruments/roland-fantom-ex.json dalle Sound List ufficiali
Roland (PDF del FANTOM EX e delle espansioni EXZ/EXM/EXSN/ACB).

Lista a sé (senza base Rosegarden). Le Scene (MSB 85) no.

    .venv/bin/python tools/make_fantom_ex_list.py FILE.pdf|FILE.txt ...

I PDF si convertono con `pdftotext -layout` (poppler-utils); si possono dare anche i
.txt già convertiti. I file giapponesi (*_jpn*) si saltano.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

OUT = Path(__file__).resolve().parent.parent / "editor" / "resources" / "instruments" / "roland-fantom-ex.json"

NAME = r"\S+(?: \S+)*"  # parole separate da un solo spazio (le colonne da due o più)
MSB = r"8[6-9]|9\d|10[0-9]"  # 85 = Scene: escluse
# Sound List del FANTOM: "PR-A   0207   Nylon Guitar   87   93   79   16:Ac.Guitar"
BASE = re.compile(rf"(?P<bank>PR-[A-E]|CMN|PRST)\s+(?P<no>\d{{4}})\s+(?P<name>{NAME})\s+(?P<msb>{MSB})\s+"
                  rf"(?P<lsb>\d{{1,3}})\s+(?P<pc>\d{{1,3}})(?:\s+(?P<cat>{NAME}))?")
# espansioni: "1   Expressive Grand   Ac.Piano   101   64   1"
EXP = re.compile(rf"(?<!\S)(?P<no>\d{{1,4}})\s+(?P<name>{NAME})\s{{2,}}(?P<cat>{NAME})\s{{2,}}(?P<msb>{MSB})\s+"
                 rf"(?P<lsb>\d{{1,3}})\s+(?P<pc>\d{{1,3}})(?!\S)")
# senza categoria: "1   Artists' Choice   105   67   1" (EXSN, kit EXZ)
BARE = re.compile(rf"(?<!\S)(?P<no>\d{{1,4}})\s+(?P<name>{NAME})\s{{2,}}(?P<msb>{MSB})\s+"
                  rf"(?P<lsb>\d{{1,3}})\s+(?P<pc>\d{{1,3}})(?!\S)")
DRUM_MSB = {86, 92, 100}  # Drum Kit di serie ed EXZ (MIDI Implementation)


def text_of(path: Path) -> str:
    if path.suffix.lower() == ".pdf":
        return subprocess.run(["pdftotext", "-layout", str(path), "-"], capture_output=True, text=True,
                              check=True).stdout
    return path.read_text(encoding="utf-8", errors="replace")


def expansion_of(path: Path) -> str:
    m = re.match(r"(EX[A-Z]{1,2}\d{2,3}|ACB\d{3})", path.name)
    return m.group(1) if m else ""


def category(text: str | None) -> str:
    return re.sub(r"^\d+:", "", text or "").strip()


HEADERS = {"No.", "Tone Name", "Category", "MSB", "LSB", "PC", "Memo", "Bank", "Tone List", "Name"}


def join_vertical(lines: list[str]) -> list[str]:
    """pdftotext a volte scrive la prima riga di una colonna "in verticale": una cella per
    riga, sempre più a destra, mescolata alle intestazioni. Le celle si riuniscono in una
    riga sola (le intestazioni si saltano)."""
    out, cells, last_col = [], [], -1
    for line in lines:
        text = line.strip()
        single = text and "  " not in text
        col = len(line) - len(line.lstrip())
        if single and text in HEADERS:
            continue
        if single and col > last_col:
            cells.append(text)
            last_col = col
            continue
        if len(cells) > 1:
            out.append("   ".join(cells))
        cells, last_col = ([text], col) if single else ([], -1)
        if not single:
            out.append(line)
    if len(cells) > 1:
        out.append("   ".join(cells))
    return out


def parse(path: Path) -> list[dict]:
    exp = expansion_of(path)
    out = []
    for line in join_vertical(text_of(path).splitlines()):
        for pattern in (BASE, EXP, BARE):
            for m in pattern.finditer(line):
                msb, lsb, pc = int(m["msb"]), int(m["lsb"]), int(m["pc"])
                if not (0 <= lsb <= 127 and 1 <= pc <= 128):
                    continue
                cat = category(m.groupdict().get("cat"))
                out.append({"name": m["name"], "category": cat, "msb": msb, "lsb": lsb, "pc": pc,
                            "drum": msb in DRUM_MSB or cat.lower().startswith("drum"),
                            "group": exp or m.groupdict().get("bank") or ""})
    return out


def main(paths: list[str]) -> None:
    entries: dict[tuple[int, int, int], dict] = {}
    for p in map(Path, paths):
        if "_jpn" in p.name:
            continue
        found = parse(p)
        for e in found:
            entries.setdefault((e["msb"], e["lsb"], e["pc"]), e)
        print(f"{p.name}: {len(found)}")
    data = {"name": "Roland FANTOM EX",
            "source": "Roland FANTOM EX Sound List e Sound List delle espansioni (PDF ufficiali)",
            "entries": [entries[k] for k in sorted(entries)]}
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(data, ensure_ascii=False, indent=0), encoding="utf-8")
    print(f"scritto {OUT}: {len(entries)} suoni")


if __name__ == "__main__":
    main(sys.argv[1:])
