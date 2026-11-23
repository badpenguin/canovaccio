"""Rende disponibile Bravura (font della notazione) al programma, senza installarla nel sistema dove si può.

Le note le disegna Cairo con un font "toy" (cairo.ToyFontFace("Bravura")), che ogni sistema cerca a modo suo:
- Linux: fontconfig, dove si aggiunge il file per questo processo;
- macOS: Cairo cerca con CoreText/Quartz: registrazione per questo processo (kCTFontManagerScopeProcess);
- Windows: Cairo cerca con DirectWrite nei font di sistema, che non vede i font privati del processo.

2026-11-23: macOS e Windows aggiunti per i binari (scelta dell'utente: build per Windows e Mac).
Su Windows Bravura si installa per l'utente (cartella dei font dell'utente e registro HKCU, senza diritti di
amministratore, come fa Windows 10 1809+ con "Installa"): è l'unico modo perché DirectWrite la trovi."""

from __future__ import annotations

import ctypes
import ctypes.util
import logging
import os
import shutil
from pathlib import Path

from ..engraving.smufl import FONT_FILE
from ..system import is_mac, is_windows

log = logging.getLogger(__name__)
_registered = False


def register_music_font() -> bool:
    """Registra Bravura una volta per processo; False se non ci riesce (le note escono con un altro font)."""
    global _registered
    if _registered:
        return True
    if not FONT_FILE.is_file():
        log.error("font musicale mancante: %s", FONT_FILE)
        return False
    # fontconfig c'è anche nelle build di GTK per Windows e macOS: si prova sempre, non costa niente
    ok = _register_fontconfig()
    if is_mac():
        ok = _register_mac() or ok
    elif is_windows():
        ok = _register_windows() or ok
    _registered = ok
    if not ok:
        log.error("font musicale non registrato: %s", FONT_FILE)
    return ok


def _register_fontconfig() -> bool:
    """Linux (e dove c'è fontconfig): aggiunge il file ai font di questo processo."""
    name = ctypes.util.find_library("fontconfig") or ("libfontconfig.so.1" if not (is_mac() or is_windows()) else None)
    if name is None:
        return False
    try:
        fc = ctypes.CDLL(name)
    except OSError:
        return False
    fc.FcConfigAppFontAddFile.argtypes = [ctypes.c_void_p, ctypes.c_char_p]
    fc.FcConfigAppFontAddFile.restype = ctypes.c_int
    return bool(fc.FcConfigAppFontAddFile(None, str(FONT_FILE).encode()))


def _register_mac() -> bool:
    """macOS: CTFontManagerRegisterFontsForURL con validità limitata a questo processo."""
    try:
        cf = ctypes.CDLL("/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation")
        ct = ctypes.CDLL("/System/Library/Frameworks/CoreText.framework/CoreText")
    except OSError as exc:
        log.warning("CoreText non disponibile: %s", exc)
        return False
    cf.CFURLCreateFromFileSystemRepresentation.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_long,
                                                           ctypes.c_bool]
    cf.CFURLCreateFromFileSystemRepresentation.restype = ctypes.c_void_p
    cf.CFRelease.argtypes = [ctypes.c_void_p]
    ct.CTFontManagerRegisterFontsForURL.argtypes = [ctypes.c_void_p, ctypes.c_uint32, ctypes.c_void_p]
    ct.CTFontManagerRegisterFontsForURL.restype = ctypes.c_bool
    path = str(FONT_FILE).encode()
    url = cf.CFURLCreateFromFileSystemRepresentation(None, path, len(path), False)
    if not url:
        return False
    try:
        return bool(ct.CTFontManagerRegisterFontsForURL(url, 1, None))  # 1 = kCTFontManagerScopeProcess
    finally:
        cf.CFRelease(url)


def _register_windows() -> bool:
    """Windows: installa Bravura per l'utente (se manca o è diversa) e la aggiunge anche per GDI."""
    import winreg

    local = os.environ.get("LOCALAPPDATA")
    if not local:
        return False
    fonts_dir = Path(local) / "Microsoft" / "Windows" / "Fonts"
    target = fonts_dir / FONT_FILE.name
    try:
        # copia solo se manca o è cambiata (nuova versione di Bravura nel programma)
        if not target.is_file() or target.stat().st_size != FONT_FILE.stat().st_size:
            fonts_dir.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(FONT_FILE, target)
        # voce del registro dei font dell'utente: senza, Windows (e DirectWrite) non la elencano
        key = winreg.CreateKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows NT\CurrentVersion\Fonts")
        with key:
            winreg.SetValueEx(key, "Bravura (OpenType)", 0, winreg.REG_SZ, str(target))
    except OSError as exc:
        log.warning("Bravura non installata per l'utente: %s", exc)
        return False
    # anche per GDI, in questa sessione (0: non privata, così la vedono anche gli altri componenti)
    gdi = ctypes.WinDLL("gdi32")
    gdi.AddFontResourceW.argtypes = [ctypes.c_wchar_p]
    gdi.AddFontResourceW(str(target))
    return True
