"""Registra Bravura in fontconfig per questo processo (senza installarla nel sistema)."""

from __future__ import annotations

import ctypes
import ctypes.util
import logging

from ..engraving.smufl import FONT_FILE

log = logging.getLogger(__name__)
_registered = False


def register_music_font() -> bool:
    global _registered
    if _registered:
        return True
    if not FONT_FILE.is_file():
        log.error("font musicale mancante: %s", FONT_FILE)
        return False
    name = ctypes.util.find_library("fontconfig") or "libfontconfig.so.1"
    fc = ctypes.CDLL(name)
    fc.FcConfigAppFontAddFile.argtypes = [ctypes.c_void_p, ctypes.c_char_p]
    fc.FcConfigAppFontAddFile.restype = ctypes.c_int
    _registered = bool(fc.FcConfigAppFontAddFile(None, str(FONT_FILE).encode()))
    if not _registered:
        log.error("fontconfig non ha accettato %s", FONT_FILE)
    return _registered
