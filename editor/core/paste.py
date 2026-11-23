"""Incolla di segmenti copiati da un altro brano (Edit > Paste nella scheda Tracks).

2026-11-23: richiesta dell'utente, copiare segmenti fra file diversi. L'app tiene aperto un solo
brano: si copia nel primo, si apre il secondo e si incolla. Le tracce di partenza non ci sono più,
quindi i segmenti vanno sulla traccia sotto il puntatore e su quelle sotto, con la stessa distanza
fra tracce e la stessa distanza nel tempo che avevano nel brano di partenza.
Sempre copie indipendenti: un clone resta legato al gruppo di note dell'altro brano.
"""

from __future__ import annotations

from fractions import Fraction

from .model import Segment, Song, Track


def paste_from_other_song(song: Song, entries: list[dict], first_track: int, at: Fraction):
    """Segmenti da aggiungere a `song` per gli appunti `entries` copiati da un altro brano.

    Ogni voce degli appunti ha "track_index" (posizione della traccia nel brano di partenza),
    "rel" (distanza dal primo segmento copiato), "content" (gruppo copiato) e i campi del segmento.
    La traccia più in alto fra quelle copiate va su `first_track`, le altre sotto alla stessa distanza;
    il primo segmento comincia in `at`. Ritorna la lista (traccia, segmento, gruppo) per AddSegments,
    oppure None se non ci sono abbastanza tracce sotto `first_track`."""
    top = min(e["track_index"] for e in entries)
    if first_track + max(e["track_index"] for e in entries) - top >= len(song.tracks):
        return None
    items: list[tuple[Track, Segment, object]] = []
    for e in entries:
        dest = song.tracks[first_track + e["track_index"] - top]
        bar, offset = song.split_position(at + e["rel"])
        group = e["content"].copy(e["name"])  # gruppo nuovo: note con id nuovi
        seg = Segment(group.id, bar, e["length"], e["transpose"], offset, e["trim"], e.get("label", ""),
                      **e["arrangement"])
        items.append((dest, seg, group))
    return items
