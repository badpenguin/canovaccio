"""Runtime hook di PyInstaller (2026-11-23): pyfluidsynth cerca libfluidsynth con ctypes.util.find_library,
che guarda solo le librerie del sistema. Nel pacchetto la libreria sta accanto al programma: qui
find_library viene fatta rispondere con quella, così il synth interno funziona anche dove FluidSynth
non è installato. Si esegue prima del programma, solo nella versione impacchettata."""

import ctypes.util
import glob
import os
import sys

_system_find_library = ctypes.util.find_library
if sys.platform.startswith("win"):
    _PATTERN = "*fluidsynth*.dll"
elif sys.platform == "darwin":
    _PATTERN = "libfluidsynth*.dylib"
else:
    _PATTERN = "libfluidsynth.so*"


def _find_library(name):
    """Per i nomi di FluidSynth la libreria del pacchetto; per tutto il resto la ricerca normale."""
    if "fluidsynth" in name:
        # 2026-11-23: nome della libreria per sistema (Linux .so, Windows DLL di MSYS2, macOS .dylib di Homebrew)
        found = sorted(glob.glob(os.path.join(sys._MEIPASS, _PATTERN)))
        if found:
            return found[0]
    return _system_find_library(name)


ctypes.util.find_library = _find_library
