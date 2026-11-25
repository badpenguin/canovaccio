"""Volume delle tracce per il mixer (View > Mixer…), senza GTK.

2026-11-24, decisioni dell'utente: il volume di una traccia sta solo negli eventi CC7 dei suoi
segmenti (niente "volume della traccia" a parte). Il mixer mostra il CC7 in vigore nel punto di
play. Gli stili caricati non si toccano (rigenerando un segmento, lì torna il CC7 dello stile).

2026-11-25, decisioni dell'utente: un mixer solo, con le modalità delle DAW per colonna (Read, Hold,
Write). Il mixer scrive i CC7 solo **all'inizio della misura** (X:1:0) dove sta il play; i CC7 altrove
(MIDI importati) restano, si cambiano nell'Event Editor. I fade vanno in CC11 (Expression), non qui.
Prima c'erano Lock + Apply (cancellava tutti i CC7 e ne scriveva uno all'inizio) e una seconda
finestra per i CC7 di una misura: due interfacce ambigue, tolte.

2026-11-25, scelta dell'utente: i volumi si devono poter scrivere anche dove la traccia non ha segmenti
(es. gli archi che entrano alla misura 9, volume dall'inizio del brano). Lì Write crea un segmento "Volume"
lungo una misura con solo il CC7 (il volume resta nei segmenti: niente eventi della traccia fuori dai segmenti);
la X che toglie l'ultimo evento di un segmento "Volume" senza note toglie anche il segmento.
"""

from __future__ import annotations

from dataclasses import replace
from fractions import Fraction

from .model import CC, ControlEvent, Group, Segment, Song, Track

VOLUME = 7  # controller del volume
VOLUME_GROUP = "Volume"  # nome dei gruppi creati dal mixer dove la traccia non aveva segmenti
DEFAULT_VOLUME = 100  # volume di default del General MIDI (traccia senza CC7)


def volume_at(song: Song, track: Track, pos: Fraction) -> int:
    """CC7 della traccia in vigore nella posizione `pos` (semibrevi): l'ultimo arrivato fin lì;
    prima del primo, o senza CC7, il default del GM."""
    value = DEFAULT_VOLUME
    for at, e in song.placed_events(track):  # in ordine di tempo
        if at > pos:
            break
        if e.kind == CC and e.number == VOLUME:
            value = e.value
    return value


# 2026-11-24, decisioni dell'utente: un segno "V" nel righello sulle misure con un CC7 esattamente sul primo
# movimento (X:1:0). Solo il primo movimento: gli altri CC7 si cambiano nell'Event Editor. Nei cloni il cambio
# vale per tutte le copie (come per le note: il CC7 sta nel gruppo condiviso).
# 2026-11-25: il click sulla "V" porta il play su quella misura e apre il mixer (non più una finestra a parte).


def volume_bars(song: Song) -> set[int]:
    """Misure (da 0) con almeno un CC7 esattamente all'inizio della misura, su una traccia qualsiasi."""
    bars = set()
    for track in song.tracks:
        for pos, e in song.placed_events(track):
            if e.kind == CC and e.number == VOLUME:
                bar = song.meters.bar_at(pos)
                if song.meters.bar_start(bar) == pos:
                    bars.add(bar)
    return bars


def bar_volume_events(song: Song, bar: int) -> list[tuple[Track, Group, ControlEvent]]:
    """Per ogni traccia, nell'ordine della scheda Tracks, il CC7 esattamente all'inizio della misura `bar`
    con il gruppo che lo contiene; le tracce senza non ci sono. Se in quel punto ce n'è più d'uno conta il
    primo del gruppo (limite scelto: per gli altri c'è l'Event Editor)."""
    at = song.meters.bar_start(bar)
    out = []
    for track in song.tracks:
        found = None
        # i segmenti in ordine di posizione; in ognuno si cerca un CC7 visibile (dentro trim/lunghezza) che cada in `at`
        for seg in sorted(track.segments, key=lambda s: song.segment_span(s)[0]):
            origin, _end = song.segment_span(seg)
            group = song.groups[seg.group_id]
            for e in group.events:
                rel = e.start - seg.trim
                if e.kind == CC and e.number == VOLUME and 0 <= rel < seg.length and origin + rel == at:
                    found = (track, group, e)
                    break
            if found is not None:
                break
        if found is not None:
            out.append(found)
    return out


def _bar_target(song: Song, track: Track, bar: int):
    """Dove scrivere il CC7 della traccia all'inizio della misura `bar`: (gruppo, posizione nel gruppo) del segmento
    che copre quel punto, o None se lì la traccia non ha segmenti (allora write_volumes crea un segmento "Volume")."""
    at = song.meters.bar_start(bar)
    for seg in track.segments:
        origin, end = song.segment_span(seg)
        if origin <= at < end:
            return song.groups[seg.group_id], at - origin + seg.trim
    return None


def _volume_segment(song: Song, track: Track, bar: int) -> tuple[Segment, Group]:
    """Segmento "Volume" nuovo (con il suo gruppo vuoto) all'inizio della misura `bar`, dove la traccia non ha segmenti:
    lungo una misura, o meno se un segmento della traccia comincia prima della fine della misura (niente sovrapposizioni)."""
    at = song.meters.bar_start(bar)
    end = song.meters.bar_start(bar + 1)
    for seg in track.segments:  # il primo segmento che comincia dentro la misura la accorcia
        origin, _end = song.segment_span(seg)
        if at < origin < end:
            end = origin
    group = Group(VOLUME_GROUP, 1)
    # l'etichetta del segmento si vede nella lane (il nome del gruppo no): così si capisce cosa contiene
    return Segment(group.id, bar, end - at, label=VOLUME_GROUP), group


def write_volumes(song: Song, writes: dict[str, dict[int, int]]):
    """Comando (un passo di Ctrl+Z, "Mixer: write volume") che scrive i CC7 all'inizio delle misure:
    `writes` = {id traccia: {misura: valore 0–127}}. Se all'inizio della misura c'è già un CC7 cambia il suo valore,
    altrimenti ne aggiunge uno. Dove la traccia non ha un segmento ne crea uno "Volume" con solo il CC7
    (2026-11-25, scelta dell'utente; prima la misura si saltava): misure vuote di fila scritte insieme (Write durante il
    play) allungano lo stesso segmento invece di crearne uno per misura. Nei cloni vale per tutte le copie (il gruppo
    è condiviso). Ritorna None se non c'è niente da cambiare."""
    from .document import AddSegments, Compound, SetAttribute

    events: dict[str, list[ControlEvent]] = {}  # id gruppo → eventi in lavorazione (più scritture sullo stesso gruppo)
    groups: dict[str, Group] = {}
    added: list[tuple[Track, Segment, Group]] = []  # segmenti "Volume" nuovi
    for track in song.tracks:
        last: tuple[Segment, Group] | None = None  # l'ultimo segmento "Volume" creato qui, da allungare se serve
        for bar, value in sorted(writes.get(track.id, {}).items()):
            target = _bar_target(song, track, bar)
            if target is None:
                at = song.meters.bar_start(bar)
                if last is not None and song.segment_span(last[0])[1] == at:
                    # misura vuota subito dopo il segmento appena creato: lo si allunga fino alla fine di questa
                    seg, group = last
                    grown, _g = _volume_segment(song, track, bar)
                    seg.length += grown.length
                    group.length_bars += 1
                    target = group, at - song.segment_span(seg)[0]
                else:
                    seg, group = _volume_segment(song, track, bar)
                    added.append((track, seg, group))
                    last = (seg, group)
                    target = group, Fraction(0)
            group, start = target
            groups[group.id] = group
            current = events.setdefault(group.id, list(group.events))
            value = max(0, min(127, value))
            # il primo CC7 proprio in quel punto si cambia; se non c'è, se ne aggiunge uno (gli eventi restano in ordine)
            for i, e in enumerate(current):
                if e.kind == CC and e.number == VOLUME and e.start == start:
                    current[i] = replace(e, value=value)
                    break
            else:
                current.append(ControlEvent(start, CC, VOLUME, value))
                current.sort(key=lambda e: e.start)
    new = {id(g) for _t, _s, g in added}
    for _track, _seg, group in added:  # i gruppi nuovi entrano già con il loro CC7 (AddSegments, sotto)
        group.events = events.pop(group.id)
    commands = [SetAttribute(groups[gid], "events", evs) for gid, evs in events.items()
                if id(groups[gid]) not in new and evs != groups[gid].events]
    if added:
        commands.append(AddSegments(added))
    return Compound(commands, "Mixer: write volume") if commands else None


def delete_volume_event(song: Song, group: Group, event: ControlEvent):
    """Comando che toglie un CC7 dal gruppo. 2026-11-25: se il gruppo è un "Volume" creato dal mixer e resta vuoto
    (senza note né altri eventi), escono dal brano anche i suoi segmenti, nello stesso passo di Ctrl+Z."""
    from .document import Compound, DeleteSegments, SetAttribute

    rest = [e for e in group.events if e.id != event.id]
    command = SetAttribute(group, "events", rest, "Volume: delete")
    if group.name != VOLUME_GROUP or rest or group.notes:
        return command
    segments = [(t, s) for t in song.tracks for s in t.segments if s.group_id == group.id]
    return Compound([command, DeleteSegments(segments)], "Volume: delete")
