"""Regole di una parte di uno stile Yamaha (blocco CASM): come le note scritte su un
accordo di partenza si adattano a un altro accordo. Una regola per gruppo (sezione ×
parte); i cloni la condividono come le note.

- Accordo di partenza: fondamentale e tipo (quasi sempre CMaj7).
- NTR (Note Transposition Rule): Root Trans (trasla della distanza fra le fondamentali),
  Root Fixed (le note restano il più vicino possibile a dove sono), Guitar.
- NTT (Note Transposition Table): come si adattano le note (Bypass, Melody, Chord, Bass,
  scale minori…); in SFF2 il bit 7 è "Bass On" (segue il basso dell'accordo).
- High key: fondamentale più alta prima di ripiegare un'ottava sotto (Root Trans).
- Limiti di nota: le note fuori si ripiegano di un'ottava.
- RTR (Retrigger Rule): cosa fa una nota tenuta quando cambia l'accordo.
- Note mute / chord mute: con quali fondamentali e tipi d'accordo la parte suona.
- SFF1: una zona sola; SFF2: tre zone d'altezza (grave, media, acuta), ognuna con le sue
  NTR, NTT, High key, limiti e RTR.
I nomi dei valori seguono la documentazione degli appassionati del formato (Yamaha non
pubblica la specifica): vanno presi come interpretazione.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field

NOTE_NAMES = ["C", "C#", "D", "Eb", "E", "F", "F#", "G", "Ab", "A", "Bb", "B"]
# tipi d'accordo Yamaha (numero nel CASM → sigla dopo la fondamentale)
CHORD_TYPES = ["", "6", "Maj7", "Maj7(#11)", "add9", "Maj7(9)", "6(9)", "aug", "m", "m6", "m7", "m7b5",
               "m(9)", "m7(9)", "m7(11)", "mMaj7", "mMaj7(9)", "dim", "dim7", "7", "7sus4", "7b5", "7(9)",
               "7(#11)", "7(13)", "7(b9)", "7(b13)", "7(#9)", "Maj7aug", "7aug", "1+8", "1+5", "sus4",
               "1+2+5", "cancel"]
NTR_NAMES = {0: "Root Trans", 1: "Root Fixed", 2: "Guitar"}
NTT_SFF1 = {0: "Bypass", 1: "Melody", 2: "Chord", 3: "Bass", 4: "Melodic Minor", 5: "Harmonic Minor"}
NTT_SFF2 = {0: "Bypass", 1: "Melody", 2: "Chord", 3: "Melodic Minor", 4: "Melodic Minor 5th Var.",
            5: "Harmonic Minor", 6: "Harmonic Minor 5th Var.", 7: "Natural Minor", 8: "Natural Minor 5th Var.",
            9: "Dorian", 10: "Dorian 5th Var."}
NTT_GUITAR = {0: "All-Purpose", 1: "Stroke", 2: "Arpeggio"}
RTR_NAMES = {0: "Stop", 1: "Pitch Shift", 2: "Pitch Shift to Root", 3: "Retrigger", 4: "Retrigger to Root",
             5: "Note Generator"}
# in italiano, per il testo leggibile
# 2026-11-20: testi in inglese (tutta la UI è in inglese, anche le Song Notes generate);
# i nomi delle tabelle (_IT, ntt_italian) restano per non cambiare gli identificatori.
NTR_IT = {0: "transposes with the root", 1: "fixed root (notes stay close to where they are)", 2: "guitar voicing"}
NTT_IT = {"Bypass": "no adaptation", "Melody": "follows the scale", "Chord": "moves to chord notes",
          "Bass": "follows the chord bass", "Melodic Minor": "melodic minor scale",
          "Harmonic Minor": "harmonic minor scale", "Natural Minor": "natural minor scale",
          "Dorian": "Dorian scale", "All-Purpose": "guitar, all-purpose", "Stroke": "guitar, strum",
          "Arpeggio": "guitar, arpeggio"}


def ntt_italian(name: str) -> str:
    """"Melody + Bass On" → "follows the scale, and the chord bass"."""
    base, bass = name.replace(" + Bass On", ""), " + Bass On" in name
    fifth = base.endswith(" 5th Var.")
    base = base.replace(" 5th Var.", "")
    text = NTT_IT.get(base, base)
    return text + (" (5th variation)" if fifth else "") + (", and the chord bass" if bass else "")
RTR_IT = {0: "stops", 1: "changes pitch", 2: "changes pitch toward the root", 3: "is retriggered",
          4: "is retriggered on the root", 5: "generates notes"}


@dataclass(frozen=True)
class Zone:
    """Regole per le note fra `low` e `high` (altezze MIDI comprese)."""

    low: int
    high: int
    ntr: int
    ntt: int
    high_key: int  # 0–11
    note_low: int
    note_high: int
    rtr: int

    def ntt_name(self, sff2: bool) -> str:
        if self.ntr == 2:
            return NTT_GUITAR.get(self.ntt & 0x7F, f"NTT {self.ntt}")
        name = (NTT_SFF2 if sff2 else NTT_SFF1).get(self.ntt & 0x7F, f"NTT {self.ntt}")
        return name + (" + Bass On" if sff2 and self.ntt & 0x80 else "")


@dataclass(frozen=True)
class StyleRule:
    part: str  # nome della parte nello stile (es. "Bass")
    source_channel: int  # 1–16
    dest_channel: int  # 1–16
    source_root: int  # 0–11
    source_type: int  # indice in CHORD_TYPES
    note_mute: int  # 12 bit: fondamentali con cui la parte suona (bit 0 = C)
    chord_mute: int  # bit per tipo d'accordo con cui la parte suona
    zones: tuple[Zone, ...] = field(default_factory=tuple)
    sff2: bool = False
    editable: bool = True

    @property
    def source_chord(self) -> str:
        root = NOTE_NAMES[self.source_root % 12]
        kind = CHORD_TYPES[self.source_type] if 0 <= self.source_type < len(CHORD_TYPES) else f"?{self.source_type}"
        return root + kind

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "StyleRule":
        data = dict(d)
        data["zones"] = tuple(Zone(**z) for z in d.get("zones", []))
        return cls(**data)

    def describe(self) -> str:
        """Riga leggibile, es. "11 Bass — written on CMaj7: transposes with the root, follows
        the chord bass (Root Trans · Bass), up to Eb, notes 28–127; note held at chord change: …"."""
        # 2026-11-20: il canale dove la parte suona (come il nome della traccia, "11 Bass"),
        # non quello dove è scritta nello stile: nei Pro style i due possono essere diversi.
        head = f"{self.dest_channel} {self.part} — written on {self.source_chord}"
        parts = []
        for z in self.zones:
            ntt = z.ntt_name(self.sff2)
            what = f"{NTR_IT.get(z.ntr, f'NTR {z.ntr}')}, {ntt_italian(ntt)}"
            limits = f", notes {z.note_low}–{z.note_high}" if (z.note_low, z.note_high) != (0, 127) else ""
            text = (f"{what} ({NTR_NAMES.get(z.ntr, z.ntr)} · {ntt}), up to {NOTE_NAMES[z.high_key % 12]}{limits}; "
                    f"note held at chord change: {RTR_IT.get(z.rtr, z.rtr)}")
            if len(self.zones) > 1:
                text = f"notes {z.low}–{z.high}: {text}"
            parts.append(text)
        muted = self._muted()
        return head + (": " if parts else "") + " | ".join(parts) + (f". {muted}" if muted else "")

    def _muted(self) -> str:
        roots = [NOTE_NAMES[i] for i in range(12) if not self.note_mute >> i & 1]
        kinds = [CHORD_TYPES[i] or "Maj" for i in range(len(CHORD_TYPES) - 1) if not self.chord_mute >> i & 1]
        out = []
        if roots:
            out.append("silent on roots " + ", ".join(roots))
        if kinds:
            out.append("silent on chords " + ", ".join(kinds))
        return "; ".join(out)
