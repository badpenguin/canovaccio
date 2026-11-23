# -*- mode: python -*-
# Ricetta di PyInstaller per la versione impacchettata di Canovaccio (2026-11-23).
#
# Si usa solo dalla build su GitHub Actions (packaging/build-appimage.sh): mette insieme Python, il codice,
# PyGObject con GTK 4, FluidSynth, python-rtmidi, le risorse (font, icone, liste dei suoni) e il SoundFont
# TimGM6mb preparato dallo script in build/soundfonts. Il risultato (una cartella) diventa poi l'AppImage.

import glob
import os

from PyInstaller.utils.hooks import collect_submodules

ROOT = os.path.abspath(os.path.join(SPECPATH, ".."))

# libfluidsynth: pyfluidsynth la apre con ctypes, quindi PyInstaller non la vede da solo; la si aggiunge a
# mano (le sue dipendenze le trova PyInstaller) e rthook_fluidsynth.py la fa trovare a pyfluidsynth
fluidsynth_libs = sorted(glob.glob("/usr/lib/*/libfluidsynth.so.3"))
if not fluidsynth_libs:
    raise SystemExit("libfluidsynth.so.3 non trovata: installare libfluidsynth3")
binaries = [(fluidsynth_libs[0], ".")]

datas = [
    (os.path.join(ROOT, "editor", "resources"), os.path.join("editor", "resources")),
    (os.path.join(ROOT, "LICENSE"), "."),
]
# SoundFont incluso (TimGM6mb, GPL-2, con la sua licenza): lo copia build-appimage.sh, non sta nel repository
soundfonts = os.path.join(ROOT, "build", "soundfonts")
if not os.path.isdir(soundfonts):
    raise SystemExit("build/soundfonts mancante: lanciare packaging/build-appimage.sh")
datas.append((soundfonts, os.path.join("editor", "resources", "soundfonts")))

# Librerie che devono restare quelle del sistema in cui gira l'AppImage (come nella excludelist di AppImage):
# OpenGL/Vulkan e driver grafici dipendono dalla scheda video, ALSA e fontconfig dalla configurazione del
# sistema (/usr/share/alsa, /etc/fonts), freetype va con fontconfig
SYSTEM_LIBS = ("libGL.so", "libGLX", "libGLdispatch", "libOpenGL", "libEGL", "libgbm", "libdrm", "libvulkan",
               "libasound.so", "libfontconfig.so", "libfreetype.so")

a = Analysis(
    [os.path.join(SPECPATH, "run_canovaccio.py")],
    pathex=[ROOT],
    binaries=binaries,
    datas=datas,
    # moduli caricati per nome: il backend rtmidi di mido e tutto il pacchetto editor (import dentro funzioni)
    hiddenimports=["mido.backends.rtmidi", "rtmidi"] + collect_submodules("editor"),
    hooksconfig={
        "gi": {
            "module-versions": {"Gtk": "4.0", "Gdk": "4.0", "Gsk": "4.0"},
            "icons": ["Adwaita"],  # icone simboliche del player e dei menu
            "languages": ["en_US"],  # l'interfaccia è in inglese
        },
    },
    runtime_hooks=[os.path.join(SPECPATH, "rthook_fluidsynth.py")],
    excludes=["tkinter", "pytest"],
)
# toglie le librerie di sistema elencate sopra (il nome è il primo elemento di ogni voce)
a.binaries = [b for b in a.binaries if not os.path.basename(b[0]).startswith(SYSTEM_LIBS)]

pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="canovaccio", console=False, strip=False, upx=False)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name="canovaccio")
