"""Righello comune a Tracce, Griglia e Notazione: misure (numero + tacca lunga)
e movimenti (tacca corta)."""

from __future__ import annotations

from fractions import Fraction
from typing import Callable, Iterable

from ..core.model import Song

RULER_H = 26  # pixel
MIN_BEAT_PX = 6  # sotto questa distanza le tacche dei movimenti non si disegnano


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
) -> None:
    """Disegna il righello nel rettangolo (x0, y0, width, height).

    `x_of(posizione)` dà la x di una posizione (None = fuori vista).
    `bar_x` può fornire direttamente la x delle stanghette (notazione)."""
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
