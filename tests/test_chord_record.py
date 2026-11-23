"""Rec sulla traccia accordi: le note suonate diventano accordi agganciati a 1/8."""

from fractions import Fraction as F

from editor.core.chord_track import ChordEvent, chords_from_notes, detect_chord_kind
from editor.core.key import Key
from editor.core.model import Note, Song
from editor.core.timeline import TimeSignature


def _chord(at, dur, *midis):
    """Note di un accordo attaccate tutte in `at`."""
    return [Note(F(at), F(dur), m) for m in midis]


def test_detect_like_yamaha_fingered():
    assert detect_chord_kind([60, 64, 67]) == (0, 0)  # C
    assert detect_chord_kind([64, 67, 72]) == (0, 0)  # rivolto: ancora C, niente basso con barra
    assert detect_chord_kind([57, 60, 64, 67]) == (9, 10)  # La al basso: Am7 (non C6)
    assert detect_chord_kind([60, 64, 67, 69]) == (0, 1)  # Do al basso: C6
    assert detect_chord_kind([60, 64, 70]) == (0, 19)  # C7 senza quinta
    assert detect_chord_kind([60]) == (0, 30)  # 1+8
    assert detect_chord_kind([60, 67]) == (0, 31)  # 5
    assert detect_chord_kind([60, 61, 62]) is None


def test_chords_snap_to_eighths_and_hold():
    song = Song()
    notes = (_chord(F(1, 64), F(1, 2), 60, 64, 67)  # C appena dopo l'inizio → 0
             + _chord(F(1, 2) - F(1, 40), F(1, 8), 65, 69, 72)  # F un filo in anticipo → 1/2, poi rilasciato
             + [Note(F(3, 4) + F(1, 100), F(1, 4), 67), Note(F(3, 4) + F(2, 100), F(1, 4), 71),
                Note(F(3, 4) + F(3, 100), F(1, 4), 74)])  # G arpeggiato velocemente → 3/4
    events = chords_from_notes(song, F(0), notes, F(1))
    assert [(e.pos, e.name) for e in events] == [(F(0), "C"), (F(1, 2), "F"), (F(3, 4), "G")]


def test_same_point_last_wins_and_repeats_merge():
    song = Song()
    notes = (_chord(0, F(1, 4), 60, 64, 67)
             + _chord(F(1, 32) + F(1, 100), F(1, 8), 62, 65, 69)  # Dm subito dopo: stessa linea di 1/8, vince lui
             + _chord(F(1, 4), F(1, 4), 62, 65, 69)  # di nuovo Dm: nessun cambio
             + _chord(F(1, 2), F(1, 4), 60, 61, 62))  # cluster: non è un accordo, ignorato
    events = chords_from_notes(song, F(0), notes, F(1))
    assert [(e.pos, e.name) for e in events] == [(F(0), "Dm")]


def test_added_note_changes_chord_and_legato_overlap():
    song = Song()
    notes = (_chord(0, F(1, 2) + F(1, 200), 60, 64, 67)  # C tenuto un attimo oltre il cambio (legato)
             + [Note(F(1, 4), F(1, 4), 70)]  # aggiunge Sib a C: C7
             + _chord(F(1, 2), F(1, 2), 65, 69, 72))  # F: le note di C rilasciate subito dopo non contano
    events = chords_from_notes(song, F(0), notes, F(1))
    assert [(e.pos, e.name) for e in events] == [(F(0), "C"), (F(1, 4), "C7"), (F(1, 2), "F")]


def test_spelling_from_key_meter_and_bounds():
    song = Song()
    song.keys.set(0, Key.parse("Dm"))
    song.meters.set(0, TimeSignature(3, 4))
    start = song.meters.bar_start(1)  # registrazione dalla seconda misura
    notes = _chord(F(1, 8) - F(1, 100), F(1, 4), 70, 74, 77) + _chord(F(3, 4) - F(1, 64), F(1, 4), 60, 64, 67)
    events = chords_from_notes(song, start, notes, F(3, 4))
    # Re minore: bemolli (Bb, non A#); il C in coda cadrebbe a fine segmento: tolto
    assert events == [ChordEvent(F(1, 8), "Bb", 0)]
