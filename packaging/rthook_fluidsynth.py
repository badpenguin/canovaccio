"""Runtime hook di PyInstaller (2026-11-23): pyfluidsynth cerca libfluidsynth con ctypes.util.find_library,
che guarda solo le librerie del sistema. Nel pacchetto la libreria sta accanto al programma: qui
find_library viene fatta rispondere con quella, così il synth interno funziona anche dove FluidSynth
non è installato. Si esegue prima del programma, solo nella versione impacchettata."""

import ctypes.util
import glob
import os
import sys

_system_find_library = ctypes.util.find_library


def _find_library(name):
    """Per i nomi di FluidSynth la libreria del pacchetto; per tutto il resto la ricerca normale."""
    if "fluidsynth" in name:
        found = sorted(glob.glob(os.path.join(sys._MEIPASS, "libfluidsynth.so*")))
        if found:
            return found[0]
    return _system_find_library(name)


ctypes.util.find_library = _find_library
