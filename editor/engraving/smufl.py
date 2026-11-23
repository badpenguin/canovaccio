"""Font SMuFL (Bravura): codici dei glifi e metadati (riquadri, ancore, spessori).

Unità: spazi del rigo (1 spazio = distanza fra due linee; em del font = 4 spazi).
Coordinate SMuFL: y verso l'alto.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

FONT_DIR = Path(__file__).resolve().parent.parent / "resources" / "fonts"
FONT_FILE = FONT_DIR / "Bravura.otf"
METADATA_FILE = FONT_DIR / "Bravura.json"
FONT_FAMILY = "Bravura"

CODEPOINTS = {
    "gClef": 0xE050, "cClef": 0xE05C, "fClef": 0xE062, "unpitchedPercussionClef1": 0xE069,
    **{f"timeSig{i}": 0xE080 + i for i in range(10)},
    "noteheadWhole": 0xE0A2, "noteheadHalf": 0xE0A3, "noteheadBlack": 0xE0A4,
    "augmentationDot": 0xE1E7, "articStaccatoAbove": 0xE4A2, "articStaccatoBelow": 0xE4A3,
    "stringsDownBow": 0xE610, "stringsUpBow": 0xE612,
    "flag8thUp": 0xE240, "flag8thDown": 0xE241, "flag16thUp": 0xE242, "flag16thDown": 0xE243,
    "flag32ndUp": 0xE244, "flag32ndDown": 0xE245, "flag64thUp": 0xE246, "flag64thDown": 0xE247,
    "accidentalFlat": 0xE260, "accidentalNatural": 0xE261, "accidentalSharp": 0xE262,
    "accidentalDoubleSharp": 0xE263, "accidentalDoubleFlat": 0xE264,
    "restWhole": 0xE4E3, "restHalf": 0xE4E4, "restQuarter": 0xE4E5, "rest8th": 0xE4E6,
    "rest16th": 0xE4E7, "rest32nd": 0xE4E8, "rest64th": 0xE4E9,
    **{f"tuplet{i}": 0xE880 + i for i in range(10)},
    "metNoteQuarterUp": 0xECA5,
}

ACCIDENTAL_GLYPH = {
    -2: "accidentalDoubleFlat", -1: "accidentalFlat", 0: "accidentalNatural",
    1: "accidentalSharp", 2: "accidentalDoubleSharp",
}

_DEFAULTS = {
    "staffLineThickness": 0.13, "stemThickness": 0.12, "legerLineThickness": 0.16,
    "legerLineExtension": 0.4, "thinBarlineThickness": 0.16, "thickBarlineThickness": 0.5,
    "tieMidpointThickness": 0.22, "tieEndpointThickness": 0.1, "beamThickness": 0.5,
}


@dataclass(frozen=True)
class BBox:
    x0: float
    y0: float
    x1: float
    y1: float

    @property
    def width(self) -> float:
        return self.x1 - self.x0


class Metadata:
    def __init__(self, data: dict) -> None:
        self.engraving = {**_DEFAULTS, **data.get("engravingDefaults", {})}
        self._bboxes = data.get("glyphBBoxes", {})
        self._anchors = data.get("glyphsWithAnchors", {})

    def bbox(self, glyph: str) -> BBox:
        b = self._bboxes.get(glyph)
        if not b:
            return BBox(0.0, -0.5, 1.0, 0.5)
        (x0, y0), (x1, y1) = b["bBoxSW"], b["bBoxNE"]
        return BBox(x0, y0, x1, y1)

    def anchor(self, glyph: str, name: str, default: tuple[float, float] = (0.0, 0.0)) -> tuple[float, float]:
        a = self._anchors.get(glyph, {}).get(name)
        return (a[0], a[1]) if a else default


@lru_cache(maxsize=1)
def metadata() -> Metadata:
    try:
        return Metadata(json.loads(METADATA_FILE.read_text(encoding="utf-8")))
    except FileNotFoundError:
        return Metadata({})


def char(glyph: str) -> str:
    return chr(CODEPOINTS[glyph])
