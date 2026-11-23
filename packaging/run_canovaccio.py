"""Punto d'ingresso per PyInstaller (2026-11-23): PyInstaller vuole uno script, non `python -m editor`.
Avvia lo stesso main() di `python -m editor`."""

import sys

from editor.__main__ import main

if __name__ == "__main__":
    sys.exit(main())
