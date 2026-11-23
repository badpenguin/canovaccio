"""Tasti di batteria di altri kit → tasti GM (35–81), all'import.

"yamaha": kit Yamaha XG/PSR (Bank MSB 127). Dalla tabella Drum/SFX Kit List del
PSR-SX900 (Data List, pag. 26): i tasti 35–81 sono come il GM (anche sulla tastiera vera
le note convertite suonano lo stesso strumento); quelli extra vanno sul suono GM più
vicino: 13–34 (Surdo, Hi Q, Scratch, Finger Snap, Metronome, Seq Click, Brush, Snare
Roll, Castanet, Snare Soft, Sticks, Kick Soft, Open Rim Shot) e 82–84 (Shaker, Jingle
Bells, Bell Tree). Gli altri tasti restano come sono.
"""

from __future__ import annotations

YAMAHA_TO_GM = {
    13: 41, 14: 41,  # Surdo Mute / Open → Low Floor Tom
    15: 76,  # Hi Q → Hi Wood Block
    16: 39,  # Whip Slap → Hand Clap
    17: 73, 18: 74,  # Scratch H / L → Short / Long Guiro
    19: 39,  # Finger Snap → Hand Clap
    20: 37,  # Click Noise → Side Stick
    21: 76, 22: 81,  # Metronome Click / Bell → Hi Wood Block / Open Triangle
    23: 77, 24: 76,  # Seq Click L / H → Low / Hi Wood Block
    25: 38, 26: 38, 27: 38, 28: 38,  # Brush Tap, Brush Swirl, Brush Slap, Brush Tap Swirl → Acoustic Snare
    29: 38,  # Snare Roll → Acoustic Snare
    30: 75,  # Castanet → Claves
    31: 38,  # Snare Soft → Acoustic Snare
    32: 37,  # Sticks → Side Stick
    33: 35,  # Kick Soft → Acoustic Bass Drum
    34: 40,  # Open Rim Shot → Electric Snare
    82: 70,  # Shaker → Maracas
    83: 54,  # Jingle Bells → Tambourine
    84: 81,  # Bell Tree → Open Triangle
}
MAPS = {"yamaha": YAMAHA_TO_GM}


def to_gm(drum_map: str, key: int) -> int:
    return MAPS.get(drum_map, {}).get(key, key)
