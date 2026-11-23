#!/usr/bin/env bash
# Costruisce l'AppImage di Canovaccio (2026-11-23). Pensato per GitHub Actions (.github/workflows/build.yml),
# su Ubuntu 24.04 con i pacchetti installati dal workflow e il venv .venv già pronto.
#
# Tutto il lavoro sta in build/ (si cancella senza problemi); il risultato è dist/Canovaccio-<versione>-x86_64.AppImage.
set -euo pipefail
cd "$(dirname "$0")/.."

VERSION="$(.venv/bin/python -c 'import editor; print(editor.__version__)')"
APPDIR=build/AppDir

# SoundFont incluso: TimGM6mb dal pacchetto Ubuntu timgm6mb-soundfont, con il testo della sua licenza (GPL-2)
rm -rf build/soundfonts
mkdir -p build/soundfonts
cp /usr/share/sounds/sf2/TimGM6mb.sf2 build/soundfonts/
cp /usr/share/doc/timgm6mb-soundfont/copyright build/soundfonts/TimGM6mb-LICENSE.txt

# programma impacchettato da PyInstaller (una cartella)
.venv/bin/pyinstaller --noconfirm --clean --distpath build/pyinstaller --workpath build/work packaging/canovaccio.spec

# AppDir: il programma in usr/lib/canovaccio, in cima lanciatore, .desktop e icona (come vuole appimagetool)
rm -rf "$APPDIR"
mkdir -p "$APPDIR/usr/lib"
cp -a build/pyinstaller/canovaccio "$APPDIR/usr/lib/"
install -m 755 packaging/appimage/AppRun "$APPDIR/AppRun"
cp packaging/appimage/io.github.badpenguin.Canovaccio.desktop "$APPDIR/"
cp editor/resources/icons/hicolor/scalable/apps/io.github.badpenguin.Canovaccio.svg "$APPDIR/"
cp "$APPDIR/io.github.badpenguin.Canovaccio.svg" "$APPDIR/.DirIcon"

# appimagetool (versione continua del progetto AppImage); senza FUSE sul runner si estrae e si esegue
TOOL=build/appimagetool
if [ ! -x "$TOOL" ]; then
    curl -fsSL -o "$TOOL" https://github.com/AppImage/appimagetool/releases/download/continuous/appimagetool-x86_64.AppImage
    chmod +x "$TOOL"
fi
mkdir -p dist
ARCH=x86_64 APPIMAGE_EXTRACT_AND_RUN=1 "$TOOL" "$APPDIR" "dist/Canovaccio-$VERSION-x86_64.AppImage"
echo "Creato dist/Canovaccio-$VERSION-x86_64.AppImage"
