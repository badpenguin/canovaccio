"""File > Save as MIDI: esporta il brano come si sente al play.

- Note come suonano: le catene legate diventano una nota, lo staccato suona a metà
  (lo stesso calcolo del playback), i cloni sono scritti per intero.
- Traccia 0 (conductor): titolo, copyright (se c'è), tempo, metro, tonalità. Poi una traccia per traccia del
  brano: nome, programma, canale (quello della traccia), etichette dei segmenti
  come marker (all'import tornano etichette).
- Anche le tracce in Mute vengono esportate.
"""

from __future__ import annotations

from fractions import Fraction
from pathlib import Path

import mido

from ..core.key import MAJOR, MINOR, Key
from ..core.model import Song
from ..output.performance import _sounding_notes, strum_delays

TPQ = 960  # tick per semiminima (terzine e suddivisioni fini restano esatte)


def _tick(pos: Fraction) -> int:
    return round(pos * 4 * TPQ)


def _ascii(text: str) -> str:
    """Testo per il MIDI (latin-1): ♯/♭ come #/b, il resto senza caratteri non rappresentabili."""
    # 2026-11-20: anche lineette e virgolette tipografiche diventano i loro equivalenti semplici
    # (prima diventavano "?"); vale per titolo, nomi delle tracce, etichette e sigle.
    for src, dst in (("♯", "#"), ("♭", "b"), ("—", "-"), ("–", "-"), ("“", '"'), ("”", '"'),
                     ("‘", "'"), ("’", "'"), ("…", "...")):
        text = text.replace(src, dst)
    return text.encode("latin-1", "replace").decode("latin-1")


def _key_name(key: Key) -> str:
    """Nome per mido: modi diversi da maggiore/minore → maggiore con la stessa armatura."""
    if key.mode not in (MAJOR, MINOR):
        key = Key.from_fifths(key.fifths, MAJOR)
    return str(key)


def _track(events: list[tuple[int, int, mido.Message]]) -> mido.MidiTrack:
    """events: (tick, ordine a parità di tick, messaggio) → tempi relativi."""
    out, last = mido.MidiTrack(), 0
    for tick, _order, msg in sorted(events, key=lambda e: (e[0], e[1])):
        out.append(msg.copy(time=tick - last))
        last = tick
    return out


def export_midi(song: Song, path: str | Path) -> None:
    mid = mido.MidiFile(type=1, ticks_per_beat=TPQ)
    # 2026-11-20: tutti i testi passano da _ascii (mido scrive in latin-1: un "—" nel nome
    # faceva fallire l'export con UnicodeEncodeError)
    conductor = [(0, 0, mido.MetaMessage("track_name", name=_ascii(song.title)))]
    if song.copyright.strip():  # 2026-11-23: Copyright Notice a tick 0 della prima traccia, come vuole lo standard
        conductor.append((0, 0, mido.MetaMessage("copyright", text=_ascii(song.copyright.strip()))))
    for e in song.tempos:
        conductor.append((_tick(e.at), 1, mido.MetaMessage("set_tempo", tempo=mido.bpm2tempo(e.value))))
    for e in song.meters:
        conductor.append((_tick(song.meters.bar_start(e.at)), 1, mido.MetaMessage(
            "time_signature", numerator=e.value.numerator, denominator=e.value.denominator)))
    for e in song.keys:
        conductor.append((_tick(song.meters.bar_start(e.at)), 1, mido.MetaMessage("key_signature", key=_key_name(e.value))))
    mid.tracks.append(_track(conductor))

    for track in song.tracks:
        if track.kind == "chords":  # traccia accordi: non suona, non si esporta
            continue
        ch = min(max(track.channel, 1), 16) - 1
        if track.instrument.is_percussion:
            # 2026-11-20: la batteria va sempre sul canale 10, l'unico che in General MIDI suona come
            # batteria (il file deve suonare uguale ovunque); su un altro canale, senza Program
            # Change, un lettore GM la suonava col pianoforte
            ch = 9
        events = [(0, 0, mido.MetaMessage("track_name", name=_ascii(track.name)))]
        if not track.instrument.is_percussion:
            events.append((0, 1, mido.Message("program_change", channel=ch, program=getattr(track.instrument, "program", 0))))
        for seg in track.segments:
            start = song.segment_span(seg)[0]
            if seg.label:
                events.append((_tick(start), 1, mido.MetaMessage("marker", text=_ascii(seg.label))))
            for pos, text in song.groups[seg.group_id].chords:  # sigle d'accordo → eventi Text
                rel = pos - seg.trim
                if 0 <= rel < seg.length:
                    events.append((_tick(start + rel), 1, mido.MetaMessage("text", text=_ascii(text))))
        for pos, e in song.placed_events(track):  # controller, pitch bend, aftertouch (prima delle note)
            events.append((_tick(pos), 1, _control_message(ch, e)))
        delays = strum_delays(song, track)  # pennata: attacchi sfasati (secondi → tick col tempo del punto)
        for start, end, midi, velocity in _sounding_notes(song, track):
            if not 0 <= midi <= 127:
                continue
            shift = round(delays.get((start, midi), 0.0) * song.tempos.at(start) / 60 * TPQ)
            on = min(_tick(start) + shift, _tick(end) - 1)
            events.append((on, 3, mido.Message("note_on", channel=ch, note=midi, velocity=velocity)))
            events.append((_tick(end), 2, mido.Message("note_off", channel=ch, note=midi, velocity=0)))
        mid.tracks.append(_track(events))
    # 2026-11-20: prima su un file temporaneo, poi rinominato (come save_project): un errore
    # a metà scrittura non tronca più un export precedente con lo stesso nome.
    tmp = Path(str(path) + ".tmp")
    try:
        mid.save(tmp)
        tmp.replace(path)
    finally:
        tmp.unlink(missing_ok=True)


def _control_message(ch: int, e) -> mido.Message:
    from ..core.model import BEND, CC, PRESSURE

    if e.kind == CC:
        return mido.Message("control_change", channel=ch, control=e.number, value=e.value)
    if e.kind == BEND:
        return mido.Message("pitchwheel", channel=ch, pitch=max(-8192, min(e.value, 8191)))
    if e.kind == PRESSURE:
        return mido.Message("aftertouch", channel=ch, value=e.value)
    return mido.Message("polytouch", channel=ch, note=e.number, value=e.value)
