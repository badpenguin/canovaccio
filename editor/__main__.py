"""Avvio: python -m editor [file.mid | progetto.music]"""

from __future__ import annotations

import argparse
import logging
import sys


def main() -> int:
    parser = argparse.ArgumentParser(prog="editor", description="Music editor (MVP)")
    parser.add_argument("file", nargs="?", help=".music project to open or .mid file to import")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO if args.verbose else logging.WARNING)

    from .ui.app import EditorApp

    app = EditorApp()
    argv = [sys.argv[0]] + ([args.file] if args.file else [])
    return app.run(argv)


if __name__ == "__main__":
    sys.exit(main())
