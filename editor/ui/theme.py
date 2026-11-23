"""Colori condivisi dalle viste disegnate con Cairo."""

from __future__ import annotations

from ..core.model import Song

# Un colore per gruppo (i segmenti dello stesso gruppo hanno lo stesso colore).
GROUP_PALETTE = [
    (0.27, 0.52, 0.85), (0.90, 0.49, 0.13), (0.30, 0.69, 0.31), (0.61, 0.35, 0.71),
    (0.84, 0.25, 0.37), (0.10, 0.65, 0.65), (0.75, 0.62, 0.15), (0.45, 0.45, 0.55),
]
PLAYHEAD = (0.9, 0.15, 0.15)


def group_color(song: Song, group_id: str) -> tuple[float, float, float]:
    ids = list(song.groups)
    i = ids.index(group_id) if group_id in ids else 0
    return GROUP_PALETTE[i % len(GROUP_PALETTE)]


def is_dark(widget) -> bool:
    """Tema scuro se il colore del testo del widget è chiaro (vale per qualsiasi tema GTK)."""
    try:
        c = widget.get_color()
        return 0.2126 * c.red + 0.7152 * c.green + 0.0722 * c.blue > 0.5
    except Exception:  # noqa: BLE001
        return False
