"""Scorrimento durante il play, uguale in tutte le viste."""

from __future__ import annotations


def follow_center(adj, x: float) -> None:
    """Il punto di play (x, nelle unità dell'Adjustment orizzontale) avanza finché arriva al
    centro della vista; poi resta al centro e la vista scorre sotto, finché lo scorrimento
    non è al massimo (allora il punto prosegue verso destra). Se è fuori vista a sinistra
    (salto o loop) la vista lo riporta al centro."""
    value, page, upper = adj.get_value(), adj.get_page_size(), adj.get_upper()
    if x > value + page / 2 or x < value:
        adj.set_value(min(max(x - page / 2, 0), max(upper - page, 0)))
