"""Disegno con Cairo delle primitive prodotte da engraving.layout (senza GTK)."""

from __future__ import annotations

import cairo

from ..engraving.layout import Glyph, Line, ScoreLayout, Slur, Text, Tie
from ..engraving.smufl import FONT_FAMILY, char, metadata

INK = (0.1, 0.1, 0.12)
RED = (0.85, 0.1, 0.1)
TEXT_FONT = "DejaVu Sans"  # testi (sigle d'accordo): ha ♯ e ♭, il generico "sans-serif" di Cairo no
SELECTED = (0.21, 0.52, 0.89)  # teste delle note selezionate (stesso blu del rettangolo di selezione)


def _x_span(it) -> tuple[float, float]:
    if isinstance(it, Line):
        return min(it.x1, it.x2), max(it.x1, it.x2)
    if isinstance(it, (Tie, Slur)):
        return it.x1, it.x2
    return it.x - 1.0, it.x + 6.0  # glifi e testi: margine largo


def draw_score(cr: cairo.Context, layout: ScoreLayout, space: float, ink=INK,
               x_range: tuple[float, float] | None = None,
               staff_dy: list[float] | None = None, selected: set | None = None) -> None:
    """Disegna il layout; `space` = pixel per spazio del rigo.
    `x_range` (in spazi): disegna solo gli elementi in quell'intervallo orizzontale.
    `staff_dy` (in spazi): spostamento verticale di ogni rigo (distribuzione nella vista).
    `selected`: (id segmento, id nota) selezionate: le loro teste in blu."""
    md = metadata()
    cr.save()
    cr.scale(space, space)
    music = cairo.ToyFontFace(FONT_FAMILY)
    text = cairo.ToyFontFace(TEXT_FONT)
    for it in layout.items:
        if x_range is not None:
            a, b = _x_span(it)
            if b < x_range[0] or a > x_range[1]:
                continue
        color = RED if getattr(it, "red", False) else ink
        if selected and isinstance(it, Glyph) and it.keys and any(k in selected for k in it.keys):
            color = SELECTED
        cr.set_source_rgb(*color)
        dy = staff_dy[it.staff] if staff_dy else 0.0
        if isinstance(it, Glyph):
            cr.set_font_face(music)
            cr.set_font_size(4.0 * it.scale)
            cr.move_to(it.x, it.y + dy)
            cr.show_text(char(it.name))
        elif isinstance(it, Line):
            dy2 = staff_dy[it.staff2] if staff_dy and it.staff2 is not None else dy
            cr.set_line_width(it.thickness)
            cr.set_line_cap(cairo.LINE_CAP_BUTT)
            cr.move_to(it.x1, it.y1 + dy)
            cr.line_to(it.x2, it.y2 + dy2)
            cr.stroke()
        elif isinstance(it, Tie):
            cr.save()
            cr.translate(0, dy)
            _tie(cr, it, md.engraving)
            cr.restore()
        elif isinstance(it, Slur):
            cr.save()
            cr.translate(0, dy)
            _slur(cr, it, md.engraving)
            cr.restore()
        elif isinstance(it, Text):
            cr.set_font_face(text)
            cr.set_font_size(it.size)
            cr.move_to(it.x, it.y + dy)
            cr.show_text(it.text)
    cr.restore()


def _tie(cr: cairo.Context, t: Tie, eng: dict) -> None:
    """Legatura come lunetta piena (due curve di Bézier)."""
    span = t.x2 - t.x1
    if span <= 0:
        return
    sign = 1 if t.below else -1
    h = min(0.35 + span * 0.08, 1.2) * sign
    th = eng.get("tieMidpointThickness", 0.22) * sign
    c1, c2 = t.x1 + span * 0.25, t.x1 + span * 0.75
    cr.move_to(t.x1, t.y)
    cr.curve_to(c1, t.y + h, c2, t.y + h, t.x2, t.y)
    cr.curve_to(c2, t.y + h - th, c1, t.y + h - th, t.x1, t.y)
    cr.close_path()
    cr.fill()


def _slur(cr: cairo.Context, s: Slur, eng: dict) -> None:
    """Legatura di frase come lunetta piena: due curve di Bézier fra estremi a quote diverse."""
    if s.x2 <= s.x1:
        return
    sign = 1 if s.below else -1
    h = s.height * sign
    th = eng.get("slurMidpointThickness", 0.22) * sign
    c1x, c2x = s.x1 + (s.x2 - s.x1) * 0.25, s.x1 + (s.x2 - s.x1) * 0.75
    c1y, c2y = s.y1 + (s.y2 - s.y1) * 0.25 + h, s.y1 + (s.y2 - s.y1) * 0.75 + h
    cr.move_to(s.x1, s.y1)
    cr.curve_to(c1x, c1y, c2x, c2y, s.x2, s.y2)
    cr.curve_to(c2x, c2y - th, c1x, c1y - th, s.x1, s.y1)
    cr.close_path()
    cr.fill()
