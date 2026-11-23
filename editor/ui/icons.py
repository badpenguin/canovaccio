"""Icone della UI: sempre simboliche (prendono il colore del tema)."""

from __future__ import annotations


def themed(name: str) -> str:
    """Nome dell'icona simbolica."""
    return f"{name.removesuffix('-symbolic')}-symbolic"


def tool_icon(tool: str) -> str:
    """Icona di uno strumento (icone dell'app, simboliche)."""
    if tool == "create":
        return "list-add-symbolic"  # icona del tema (nessuna grafica disegnata dall'app)
    return f"tm-tool-{tool}-symbolic"
