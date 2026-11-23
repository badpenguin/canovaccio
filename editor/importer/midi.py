"""Import di un file .mid nel modello interno.

- Nessuna quantizzazione: i tick diventano Fraction esatte.
- Ogni coppia (traccia MIDI, canale) con note diventa una Track, divisa in segmenti
  sulle misure vuote (un segmento per ogni blocco di misure consecutive con note).
- Tempo, metro e tonalità: tutti gli eventi del file vanno nelle tabelle globali.
- Un meta-evento "marker" (o "text") dentro una traccia con note diventa
  l'etichetta del segmento che contiene quel punto.
- Batteria: il canale 10 e ogni canale con un kit Yamaha/XG (Bank MSB 127, 126 per i
  kit SFX) o GM2 (MSB 120): suona col kit standard GM. Nei kit Yamaha (MSB 127) i
  tasti extra (13–34, 82–84) diventano il tasto GM più vicino (importer.drum_maps).
- Con `sections` (stili Yamaha, vedi importer.style) i segmenti seguono le sezioni
  invece delle misure vuote, e tutto si sposta perché la prima sezione parta da 0.
"""

from __future__ import annotations

from bisect import bisect_right

from collections import defaultdict
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path

import mido

from ..core.clef import BASS, TREBLE
from ..core.instrument import GM_PROGRAMS, GMInstrument
from ..core.key import Key
from ..core.chords import dedupe_symbols, looks_like_symbol
from ..core.detect import detect_staccato
from ..core.model import BEND, CC, POLY_PRESSURE, PRESSURE, ControlEvent, Group, Note, Segment, Song, Track
from ..core.timeline import KeyTable, MeterTable, TempoTable, TimeSignature
from .drum_maps import to_gm

PERCUSSION_CHANNEL = 9  # canale 10 in numerazione 1-based
DRUM_KIT_MSB = {120, 126, 127}  # Bank Select MSB dei kit: GM2, Yamaha/XG SFX, Yamaha/XG


@dataclass
class ImportOptions:
    bass_clef_below: int = 60  # altezza media sotto il Do centrale → chiave di basso


def import_midi(source: str | Path | mido.MidiFile, options: ImportOptions | None = None,
                sections: list[tuple[int, str]] | None = None,
                channel_names: dict[int, str] | None = None) -> Song:
    """`sections`: [(tick, nome)] in ordine: un segmento per sezione (stili Yamaha).
    `channel_names`: canale 1–16 → nome della parte, per il nome delle tracce."""
    opts = options or ImportOptions()
    # 2026-11-20: clip=True come per gli stili: un data byte > 127 (file scritti male) si
    # riporta a 127 invece di impedire l'apertura dell'intero file
    mid = source if isinstance(source, mido.MidiFile) else mido.MidiFile(str(source), clip=True)
    if mid.type == 2:
        raise ValueError("type 2 MIDI files are not supported")
    tpw = mid.ticks_per_beat * 4  # tick per semibreve
    offset = sections[0][0] if sections else 0  # stili: la prima sezione parte da 0

    def pos(tick: int) -> Fraction:
        return Fraction(max(tick - offset, 0), tpw)

    # --- raccolta eventi con tick assoluti ------------------------------
    tempos: list[tuple[int, int]] = []
    meters: list[tuple[int, int, int]] = []
    keys: list[tuple[int, str]] = []
    notes: dict[tuple[int, int], list[tuple[int, int, int, int]]] = defaultdict(list)
    programs: dict[tuple[int, int], int] = {}
    banks: dict[tuple[int, int], int] = {}  # Bank Select MSB (per riconoscere i kit di batteria)
    controls: dict[tuple[int, int], list] = defaultdict(list)  # (tick, tipo, numero, valore): CC, pitch bend, aftertouch
    labels: dict[int, list[tuple[int, str]]] = defaultdict(list)  # traccia MIDI → [(tick, testo)]
    symbols: dict[int, list[tuple[int, str]]] = defaultdict(list)  # sigle d'accordo (eventi Text)
    copyright = ""  # 2026-11-23: primo meta-evento Copyright Notice del file (negli stili: quasi sempre Yamaha)

    for ti, mtrack in enumerate(mid.tracks):
        tick = 0
        open_notes: dict[tuple[int, int], list[tuple[int, int]]] = defaultdict(list)
        for msg in mtrack:
            tick += msg.time
            t = msg.type
            if t == "set_tempo":
                tempos.append((tick, msg.tempo))
            elif t == "time_signature":
                meters.append((tick, msg.numerator, msg.denominator))
            elif t == "key_signature":
                keys.append((tick, msg.key))
            elif t == "copyright":
                copyright = copyright or msg.text.strip()
            elif t == "text" and looks_like_symbol(msg.text):  # Text che sembra una sigla: sigla d'accordo
                symbols[ti].append((tick, msg.text.strip()))
            elif t in ("marker", "text") and msg.text.strip():  # marker (e altri testi): etichetta
                labels[ti].append((tick, msg.text.strip()))
            elif t == "program_change":
                programs.setdefault((ti, msg.channel), msg.program)
            elif t == "control_change":
                if msg.control == 0:  # Bank Select MSB: riconosce i kit di batteria
                    banks.setdefault((ti, msg.channel), msg.value)
                if msg.control not in (0, 32):  # Bank Select (come il Program Change) è il suono della traccia
                    controls[(ti, msg.channel)].append((tick, CC, msg.control, msg.value))
            elif t == "pitchwheel":
                controls[(ti, msg.channel)].append((tick, BEND, 0, msg.pitch))
            elif t == "aftertouch":
                controls[(ti, msg.channel)].append((tick, PRESSURE, 0, msg.value))
            elif t == "polytouch":
                controls[(ti, msg.channel)].append((tick, POLY_PRESSURE, msg.note, msg.value))

            elif t == "note_on" and msg.velocity > 0:
                open_notes[(msg.channel, msg.note)].append((tick, msg.velocity))
            elif t == "note_off" or (t == "note_on" and msg.velocity == 0):
                stack = open_notes.get((msg.channel, msg.note))
                if stack:
                    start, vel = stack.pop(0)
                    # 2026-11-20: almeno 1 tick, come le note mai chiuse (durata 0 rendeva
                    # impossibile l'export: note_on a tick -1)
                    notes[(ti, msg.channel)].append((start, max(tick, start + 1), msg.note, vel))
        for (ch, key), stack in open_notes.items():  # note mai chiuse: fino a fine traccia
            for start, vel in stack:
                notes[(ti, ch)].append((start, max(tick, start + 1), key, vel))

    # --- tabelle globali -------------------------------------------------
    meter_table = MeterTable()
    for tick, num, den in sorted(meters):
        p = pos(tick)
        meter_table.set(meter_table.bar_ceil(p), TimeSignature(num, den))

    tempo_table = TempoTable()
    for tick, us in sorted(tempos):
        tempo_table.set(pos(tick), round(mido.tempo2bpm(us), 3))

    key_table = KeyTable()
    for tick, text in sorted(keys):
        try:
            key = Key.parse(text)
        except (KeyError, ValueError, IndexError):
            continue
        key_table.set(meter_table.bar_ceil(pos(tick)), key)

    song = Song(title=_title(source), tempos=tempo_table, meters=meter_table, keys=key_table, copyright=copyright)

    # 2026-11-20: controller, pitch bend e aftertouch di una traccia MIDI senza note su quel canale
    # (es. una traccia di setup con volume e pan) vanno alla prima traccia con note dello stesso
    # canale, come già il Program Change e il Bank Select; prima si perdevano
    with_notes = sorted(k for k, v in notes.items() if v)
    for (ti, ch), events in list(controls.items()):
        if (ti, ch) in notes and notes[(ti, ch)]:
            continue
        target = next((k for k in with_notes if k[1] == ch), None)
        if target is not None:
            controls[target] = events + controls.get(target, [])

    # --- tracce ----------------------------------------------------------
    for (ti, ch), raw in sorted(notes.items()):
        if not raw:
            continue
        raw.sort()

        program = programs.get((ti, ch))
        if program is None:  # a volte il program_change sta in un'altra traccia
            program = next((p for (_, c), p in programs.items() if c == ch), None)
        msb = banks.get((ti, ch), next((b for (_, c), b in banks.items() if c == ch), None))
        percussion = ch == PERCUSSION_CHANNEL or msb in DRUM_KIT_MSB
        if msb == 127:  # kit Yamaha/XG: i tasti extra vanno sul suono GM più vicino
            raw = [(s, e, to_gm("yamaha", n), v) for s, e, n, v in raw]
        # nome: canale MIDI + strumento; senza strumento "Track" + canale
        part = (channel_names or {}).get(ch + 1)
        if part:
            name = f"{ch + 1} {part}"
        elif percussion:
            name = f"{ch + 1} Drums"  # 2026-11-20: in inglese (prima "Batteria")
        elif program is not None:
            name = f"{ch + 1} {GM_PROGRAMS[program]}"
        else:
            name = f"Track {ch + 1}"
        program = program or 0
        avg = sum(n for _, _, n, _ in raw) / len(raw)
        clef = TREBLE if percussion or avg >= opts.bass_clef_below else BASS

        # un segmento (con il suo gruppo) per ogni blocco di misure consecutive con note:
        # le misure vuote separano i segmenti
        runs = _section_runs(raw, pos, meter_table, sections) if sections else _note_runs(raw, pos, meter_table)
        segments = []
        for k, (first_bar, last_bar, run, *section) in enumerate(runs, start=1):
            origin = meter_table.bar_start(first_bar)
            group = Group(
                name=name if len(runs) == 1 else f"{name} · {k}",
                length_bars=last_bar - first_bar,
                notes=[
                    Note(start=pos(s) - origin, duration=pos(e) - pos(s), midi=n, velocity=v)
                    for s, e, n, v in run
                ],
            )
            song.groups[group.id] = group
            length = meter_table.bar_start(last_bar) - origin
            if not percussion:  # batteria: lo staccato non ha senso (colpi che suonano da soli)
                for note, written in detect_staccato(group.notes, end=length):  # staccato esportato come metà durata
                    note.duration, note.staccato = written, True
            label = section[0] if section else next((text for tick, text in labels.get(ti, [])
                                                     if origin <= pos(tick) < origin + length), "")
            group.chords = dedupe_symbols([(pos(tick) - origin, text) for tick, text in symbols.get(ti, [])
                                           if origin <= pos(tick) < origin + length])
            segments.append(Segment(group_id=group.id, start_bar=first_bar, length=length, label=label))
        _place_controls(song, segments, controls.get((ti, ch), []), pos)
        song.tracks.append(
            Track(
                name=name,
                instrument=GMInstrument(program=0 if percussion else program, percussion=percussion),
                clef=clef,
                segments=segments,
                channel=ch + 1,
            )
        )
    return song


def _place_controls(song: Song, segments: list[Segment], controls: list, pos) -> None:
    """Controller, pitch bend e aftertouch nei segmenti della traccia: nel segmento che li
    contiene; quelli prima di un segmento (es. volume e pan all'inizio del file) al suo
    inizio; quelli dopo l'ultimo si perdono. Uno che cade esattamente sulla fine di un
    segmento seguito da un vuoto (o dell'ultimo) va un tick prima, dentro quel segmento."""
    # 2026-11-20: inizio e fine dei segmenti calcolati una volta e segmento trovato con bisect
    # (prima per ogni evento si scorrevano tutti i segmenti: 20k pitch bend su 100 segmenti = 7 s)
    spans = [song.segment_span(seg) for seg in segments]
    ends = [end for _origin, end in spans]
    for tick, kind, number, value in sorted(controls, key=lambda c: c[0]):
        p = pos(tick)
        i = bisect_right(ends, p)  # primo segmento che finisce dopo p
        if i > 0 and p == ends[i - 1] and (i == len(segments) or spans[i][0] > p):
            # 2026-11-20: tipico il pedale su sulla stanghetta dove finisce il segmento: prima
            # andava all'inizio del segmento dopo (pedale tenuto nel vuoto) o si perdeva dopo
            # l'ultimo (ultimo accordo tenuto fino allo Stop)
            i -= 1
            p = max(pos(tick - 1), spans[i][0])
        if i < len(segments):
            origin = spans[i][0]
            song.groups[segments[i].group_id].events.append(ControlEvent(max(p - origin, 0 * p), kind, number, value))


def _section_runs(raw, pos, meters: MeterTable, sections: list[tuple[int, str]]) -> list[tuple]:
    """Divide le note per sezione (stili): [(prima misura, misura dopo l'ultima, note, nome)]
    per ogni sezione in cui la traccia ha note. Una nota sta nella sezione in cui comincia."""
    out = []
    for i, (tick, name) in enumerate(sections):
        end = sections[i + 1][0] if i + 1 < len(sections) else None
        run = [n for n in raw if n[0] >= tick and (end is None or n[0] < end)]
        if not run:
            continue
        first = meters.bar_at(pos(tick))
        last = meters.bar_at(pos(end)) if end is not None else max(meters.bar_ceil(pos(max(n[1] for n in run))),
                                                                  first + 1)
        out.append((first, max(last, first + 1), run, name))
    return out


def _note_runs(raw, pos, meters: MeterTable) -> list[tuple[int, int, list]]:
    """Divide le note in blocchi di misure consecutive occupate.

    Una misura è occupata se vi suona almeno una nota (anche tenuta dalla misura prima).
    Ritorna [(prima misura, misura dopo l'ultima, note del blocco)] in ordine."""
    spans = []  # (prima misura, misura dopo l'ultima occupata, nota)
    for note in raw:
        s, e = note[0], note[1]
        first = meters.bar_at(pos(s))
        spans.append((first, max(meters.bar_ceil(pos(e)), first + 1), note))
    spans.sort(key=lambda t: (t[0], t[2]))
    # 2026-11-20: blocchi modificabili e note aggiunte con append (prima `notes + [note]` copiava
    # la lista a ogni nota: quadratico, 80k note = 19 s)
    runs: list[list] = []
    for first, end, note in spans:
        if runs and first <= runs[-1][1]:  # attaccato al blocco precedente (nessuna misura vuota in mezzo)
            runs[-1][1] = max(runs[-1][1], end)
            runs[-1][2].append(note)
        else:
            runs.append([first, end, [note]])
    return [tuple(r) for r in runs]


def _title(source) -> str:
    if isinstance(source, mido.MidiFile):
        return Path(source.filename).stem if source.filename else "Untitled"
    return Path(source).stem
