"""Righello comune a Tracce, Griglia e Notazione: misure (numero + tacca lunga)
e movimenti (tacca corta)."""

from __future__ import annotations

import math
from fractions import Fraction
from typing import Callable, Iterable

from ..core.model import Song

RULER_H = 26  # pixel
MIN_BEAT_PX = 6  # sotto questa distanza le tacche dei movimenti non si disegnano
MARK_SIZE = 11  # px del quadratino con la "V" (cambio di volume sul primo movimento della misura)


def ruler_colors(dark: bool) -> tuple[tuple[float, float, float], tuple[float, float, float]]:
    """(sfondo, testo/tacche) con contrasto pieno."""
    return ((0.2, 0.2, 0.23), (0.92, 0.92, 0.92)) if dark else ((0.85, 0.85, 0.88), (0.1, 0.1, 0.1))


def bar_and_beat_positions(song: Song, first_bar: int, last_bar: int) -> tuple[list[tuple[int, Fraction]], list[Fraction]]:
    """Inizi di misura [(misura, posizione)] e movimenti interni alla misura, per misure first..last-1."""
    bars, beats = [], []
    for bar in range(first_bar, last_bar):
        start = song.meters.bar_start(bar)
        ts = song.meters.at(bar)
        bars.append((bar, start))
        beats += [start + ts.beat * i for i in range(1, ts.numerator)]
    return bars, beats


def draw_ruler(
    cr,
    x0: float,
    y0: float,
    width: float,
    height: float,
    song: Song,
    first_bar: int,
    last_bar: int,
    x_of: Callable[[Fraction], float | None],
    dark: bool,
    font_size: float = 11.0,
    bar_x: Iterable[tuple[int, float]] | None = None,
    end_line: bool = True,
    marks: set[int] | None = None,
    mark_rgb: tuple[float, float, float] = (0.21, 0.52, 0.89),
) -> list[tuple[int, float, float, float, float]]:
    """Disegna il righello nel rettangolo (x0, y0, width, height).

    `x_of(posizione)` dà la x di una posizione (None = fuori vista).
    `bar_x` può fornire direttamente la x delle stanghette (notazione).
    `end_line`: stanghetta alta (senza numero) alla fine di `last_bar`, cioè alla fine del brano o
    dell'editor; False se `last_bar` è solo il limite della parte visibile.
    `marks`: misure col segno "V" (CC7 sul primo movimento), in basso a destra della stanghetta, col fondo `mark_rgb`
    (il colore di selezione del tema); ritorna i loro rettangoli
    [(misura, x, y, larghezza, altezza)] per il click e il tooltip."""
    bg, fg = ruler_colors(dark)
    cr.set_source_rgb(*bg)
    cr.rectangle(x0, y0, width, height)
    cr.fill()
    cr.set_source_rgb(*fg)
    bars, beats = bar_and_beat_positions(song, first_bar, last_bar)
    xs = [x_of(p) for p in beats]
    xs = [x for x in xs if x is not None and x0 <= x <= x0 + width]
    if len(xs) < 2 or min(b - a for a, b in zip(sorted(xs), sorted(xs)[1:])) >= MIN_BEAT_PX:
        for x in xs:
            cr.rectangle(x, y0 + height * 0.7, 1, height * 0.3)
    cr.fill()
    bar_points = list(bar_x) if bar_x is not None else [(b, x_of(p)) for b, p in bars]
    cr.select_font_face("sans-serif")
    cr.set_font_size(font_size)
    # numerazione a passo regolare (1, 2, 4, 8, 16… misure) quando le misure sono strette
    shown = [(b, x) for b, x in bar_points if x is not None]
    widest = cr.text_extents(str(max((b for b, _x in shown), default=0) + 1)).x_advance + 9
    spacing = min((x2 - x1 for (_a, x1), (_b, x2) in zip(shown, shown[1:]) if x2 > x1), default=widest)
    step = 1
    while spacing * step < widest:
        step *= 2
    label_end = -1e9  # in più: mai due numeri sovrapposti
    hits = []
    for bar, x in bar_points:
        if x is None or not (x0 - 1 <= x <= x0 + width):
            continue
        text = str(bar + 1)
        tw = cr.text_extents(text).x_advance
        labelled = bar % step == 0 and x + 3 >= label_end
        cr.rectangle(x, y0 + height * (0.15 if labelled else 0.45), 1, height * (0.85 if labelled else 0.55))
        cr.fill()
        if labelled:
            cr.move_to(x + 3, y0 + height * 0.15 + font_size)
            cr.show_text(text)
            label_end = x + 3 + tw + 6
        # 2026-11-24, correzione dell'utente: in basso, centrata sulla stanghetta (l'inizio della misura), col fondo
        # colorato; prima era dopo il numero, col solo bordo.
        # 2026-11-24, poi: fuori dalla stanghetta, perché la riga del punto di play, disegnata per ultima da x verso
        # destra come la stanghetta, la copriva quando il play è a inizio misura. Subito a destra (scelta dell'utente;
        # per un giro era a sinistra, ma lì sulla misura 1 lo spazio prima della battuta è troppo stretto)
        if marks and bar in marks:
            mx, my = x + 2.5, y0 + height - MARK_SIZE - 1  # dopo la stanghetta e la riga di play (larghe 1 e 1,5 px)
            _draw_mark(cr, mx, my, mark_rgb, font_size)
            cr.set_source_rgb(*fg)  # il colore del testo per le stanghette e i numeri che seguono
            hits.append((bar, mx, my, MARK_SIZE, MARK_SIZE))
    # 2026-11-24: stanghetta di chiusura dell'ultima misura (segnalata dall'utente: a fine brano mancava);
    # alta come quelle numerate, senza numero. La notazione (bar_x) ha le sue stanghette.
    if end_line and bar_x is None and last_bar > first_bar:
        x = x_of(song.meters.bar_start(last_bar))
        if x is not None and x0 - 1 <= x <= x0 + width:
            cr.rectangle(x, y0 + height * 0.15, 1, height * 0.85)
            cr.fill()
    return hits


def _draw_mark(cr, x: float, y: float, rgb: tuple[float, float, float], font_size: float) -> None:
    """Segno di un cambio di volume: quadratino con gli angoli arrotondati, pieno nel colore `rgb`, e una "V" bianca in
    grassetto. 2026-11-24: prima un quadratino pieno nero nelle corsie della scheda Tracce (all'utente il nero non
    piaceva), poi nel righello col solo bordo dopo il numero; l'utente lo vuole colorato, in basso sotto la stanghetta."""
    import cairo

    cr.save()
    rounded_rect(cr, x, y, MARK_SIZE, MARK_SIZE, 2)
    cr.set_source_rgb(*rgb)
    cr.fill()
    cr.set_source_rgb(1, 1, 1)
    cr.select_font_face("sans-serif", cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_BOLD)
    cr.set_font_size(font_size - 3)
    ext = cr.text_extents("V")
    cr.move_to(x + MARK_SIZE / 2 - ext.width / 2 - ext.x_bearing, y + MARK_SIZE / 2 - ext.height / 2 - ext.y_bearing)
    cr.show_text("V")
    cr.restore()


def rounded_rect(cr, x: float, y: float, w: float, h: float, r: float) -> None:
    """Percorso di un rettangolo con gli angoli arrotondati di raggio `r` (quadratino dei cambi di volume)."""
    cr.new_sub_path()
    cr.arc(x + w - r, y + r, r, -math.pi / 2, 0)
    cr.arc(x + w - r, y + h - r, r, 0, math.pi / 2)
    cr.arc(x + r, y + h - r, r, math.pi / 2, math.pi)
    cr.arc(x + r, y + r, r, math.pi, 3 * math.pi / 2)
    cr.close_path()
