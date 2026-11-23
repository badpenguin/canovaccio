"""Avvio: python -m editor [file] (o ./canovaccio.sh [file]): progetto .music/.stile, MIDI,
partitura MuseScore/MusicXML, progetto Rosegarden o stile Yamaha da aprire."""

from __future__ import annotations

import argparse
import logging
import sys


def main() -> int:
    """Legge la riga di comando (file da aprire, -v) e avvia l'applicazione GTK."""
    # 2026-11-23: aiuto con il nome del programma e tutti i formati che si aprono (prima "editor",
    # "Music editor (MVP)" e solo .music/.mid)
    parser = argparse.ArgumentParser(
        prog="canovaccio.sh",
        description="Canovaccio: MIDI sequencer with grid, notation, chord track and Yamaha styles.")
    parser.add_argument(
        "file", nargs="?",
        help="file to open: a project (.music, .stile) or a file to import: MIDI (.mid, .midi), "
             "MuseScore (.mscz, .mscx), MusicXML (.musicxml, .mxl, .xml), Rosegarden (.rg), "
             "Yamaha style (.sty, .prs, .sst, .fps, .bcs). Without it the window opens empty.")
    parser.add_argument("-v", "--verbose", action="store_true", help="show more log messages")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO if args.verbose else logging.WARNING)

    from .ui.app import EditorApp

    app = EditorApp()
    argv = [sys.argv[0]] + ([args.file] if args.file else [])
    return app.run(argv)


if __name__ == "__main__":
    sys.exit(main())
