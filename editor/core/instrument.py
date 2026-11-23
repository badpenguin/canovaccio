"""Strumenti. Astratti: l'MVP usa solo General MIDI, ma una traccia potrà
puntare a un banco/patch di un synth esterno, un preset SoundFont, ecc."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass


class Instrument(ABC):
    @property
    @abstractmethod
    def display_name(self) -> str: ...

    @property
    def is_percussion(self) -> bool:
        return False


@dataclass(frozen=True)
class GMInstrument(Instrument):
    program: int = 0  # 0–127
    percussion: bool = False  # True = canale batteria (10)

    def __post_init__(self) -> None:
        if not 0 <= self.program <= 127:
            raise ValueError(f"GM program out of range: {self.program}")

    @property
    def display_name(self) -> str:
        if self.percussion:
            return "Drums"
        return f"{self.program + 1:03d} {GM_PROGRAMS[self.program]}"

    @property
    def is_percussion(self) -> bool:
        return self.percussion


GM_PROGRAMS = [
    # Pianoforti
    "Acoustic Grand Piano", "Bright Acoustic Piano", "Electric Grand Piano", "Honky-tonk Piano",
    "Electric Piano 1", "Electric Piano 2", "Harpsichord", "Clavinet",
    # Percussioni cromatiche
    "Celesta", "Glockenspiel", "Music Box", "Vibraphone",
    "Marimba", "Xylophone", "Tubular Bells", "Dulcimer",
    # Organi
    "Drawbar Organ", "Percussive Organ", "Rock Organ", "Church Organ",
    "Reed Organ", "Accordion", "Harmonica", "Tango Accordion",
    # Chitarre
    "Acoustic Guitar (nylon)", "Acoustic Guitar (steel)", "Electric Guitar (jazz)", "Electric Guitar (clean)",
    "Electric Guitar (muted)", "Overdriven Guitar", "Distortion Guitar", "Guitar Harmonics",
    # Bassi
    "Acoustic Bass", "Electric Bass (finger)", "Electric Bass (pick)", "Fretless Bass",
    "Slap Bass 1", "Slap Bass 2", "Synth Bass 1", "Synth Bass 2",
    # Archi
    "Violin", "Viola", "Cello", "Contrabass",
    "Tremolo Strings", "Pizzicato Strings", "Orchestral Harp", "Timpani",
    # Ensemble
    "String Ensemble 1", "String Ensemble 2", "Synth Strings 1", "Synth Strings 2",
    "Choir Aahs", "Voice Oohs", "Synth Voice", "Orchestra Hit",
    # Ottoni
    "Trumpet", "Trombone", "Tuba", "Muted Trumpet",
    "French Horn", "Brass Section", "Synth Brass 1", "Synth Brass 2",
    # Ance
    "Soprano Sax", "Alto Sax", "Tenor Sax", "Baritone Sax",
    "Oboe", "English Horn", "Bassoon", "Clarinet",
    # Flauti
    "Piccolo", "Flute", "Recorder", "Pan Flute",
    "Blown Bottle", "Shakuhachi", "Whistle", "Ocarina",
    # Synth lead
    "Lead 1 (square)", "Lead 2 (sawtooth)", "Lead 3 (calliope)", "Lead 4 (chiff)",
    "Lead 5 (charang)", "Lead 6 (voice)", "Lead 7 (fifths)", "Lead 8 (bass + lead)",
    # Synth pad
    "Pad 1 (new age)", "Pad 2 (warm)", "Pad 3 (polysynth)", "Pad 4 (choir)",
    "Pad 5 (bowed)", "Pad 6 (metallic)", "Pad 7 (halo)", "Pad 8 (sweep)",
    # Synth effetti
    "FX 1 (rain)", "FX 2 (soundtrack)", "FX 3 (crystal)", "FX 4 (atmosphere)",
    "FX 5 (brightness)", "FX 6 (goblins)", "FX 7 (echoes)", "FX 8 (sci-fi)",
    # Etnici
    "Sitar", "Banjo", "Shamisen", "Koto",
    "Kalimba", "Bagpipe", "Fiddle", "Shanai",
    # Percussivi
    "Tinkle Bell", "Agogo", "Steel Drums", "Woodblock",
    "Taiko Drum", "Melodic Tom", "Synth Drum", "Reverse Cymbal",
    # Effetti sonori
    "Guitar Fret Noise", "Breath Noise", "Seashore", "Bird Tweet",
    "Telephone Ring", "Helicopter", "Applause", "Gunshot",
]
assert len(GM_PROGRAMS) == 128

# Famiglie per la scelta a due livelli: le 16 famiglie GM, con le chitarre
# divise in acustiche ed elettriche. (nome, primo programma, ultimo programma escluso)
GM_FAMILIES = [
    ("Piano", 0, 8), ("Chromatic Percussion", 8, 16), ("Organ", 16, 24),
    ("Acoust. Guit.", 24, 26), ("Elec. Guit.", 26, 32), ("Bass", 32, 40),
    ("Strings", 40, 48), ("Ensemble", 48, 56), ("Brass", 56, 64), ("Reed", 64, 72),
    ("Pipe", 72, 80), ("Synth Lead", 80, 88), ("Synth Pad", 88, 96),
    ("Synth Effects", 96, 104), ("Ethnic", 104, 112), ("Percussive", 112, 120),
    ("Sound Effects", 120, 128),
]
assert [p for _n, a, b in GM_FAMILIES for p in range(a, b)] == list(range(128))


def gm_family(program: int) -> int:
    """Indice in GM_FAMILIES della famiglia del programma."""
    return next(i for i, (_n, a, b) in enumerate(GM_FAMILIES) if a <= program < b)

# Suoni della batteria General MIDI (canale 10): nota → nome
GM_DRUMS = {
    35: "Acoustic Bass Drum", 36: "Bass Drum 1", 37: "Side Stick", 38: "Acoustic Snare", 39: "Hand Clap",
    40: "Electric Snare", 41: "Low Floor Tom", 42: "Closed Hi-Hat", 43: "High Floor Tom", 44: "Pedal Hi-Hat",
    45: "Low Tom", 46: "Open Hi-Hat", 47: "Low-Mid Tom", 48: "Hi-Mid Tom", 49: "Crash Cymbal 1",
    50: "High Tom", 51: "Ride Cymbal 1", 52: "Chinese Cymbal", 53: "Ride Bell", 54: "Tambourine",
    55: "Splash Cymbal", 56: "Cowbell", 57: "Crash Cymbal 2", 58: "Vibraslap", 59: "Ride Cymbal 2",
    60: "Hi Bongo", 61: "Low Bongo", 62: "Mute Hi Conga", 63: "Open Hi Conga", 64: "Low Conga",
    65: "High Timbale", 66: "Low Timbale", 67: "High Agogo", 68: "Low Agogo", 69: "Cabasa",
    70: "Maracas", 71: "Short Whistle", 72: "Long Whistle", 73: "Short Guiro", 74: "Long Guiro",
    75: "Claves", 76: "Hi Wood Block", 77: "Low Wood Block", 78: "Mute Cuica", 79: "Open Cuica",
    80: "Mute Triangle", 81: "Open Triangle",
}
