#!/usr/bin/env bash
# Costruisce la versione Windows di Canovaccio (2026-11-23), in uno zip con la cartella del programma.
# Pensato per GitHub Actions (.github/workflows/build.yml): gira nella shell UCRT64 di MSYS2, con GTK 4,
# PyGObject, FluidSynth e PyInstaller di MSYS2, il venv .venv già pronto e il SoundFont in build/soundfonts
# (lo prepara il job "soundfont" del workflow).
#
# Risultato: dist/Canovaccio-<versione>-windows-x86_64.zip (si estrae e si lancia Canovaccio\canovaccio.exe).
set -euo pipefail
cd "$(dirname "$0")/.."

# il venv di Python di MSYS2 può avere bin/ (schema mingw) o Scripts/ (schema Windows)
PY=.venv/bin/python
[ -x "$PY" ] || PY=.venv/Scripts/python.exe
VERSION="$("$PY" -c 'import editor; print(editor.__version__)')"

test -f build/soundfonts/TimGM6mb.sf2 || { echo "build/soundfonts/TimGM6mb.sf2 mancante" >&2; exit 1; }

# icona del programma in PNG (PyInstaller la converte in .ico con Pillow)
mkdir -p build/icons
rsvg-convert -w 256 -h 256 editor/resources/icons/hicolor/scalable/apps/io.github.badpenguin.Canovaccio.svg \
    -o build/icons/canovaccio.png

"$PY" -m PyInstaller --noconfirm --clean --distpath build/pyinstaller --workpath build/work packaging/canovaccio.spec

# zip con la cartella "Canovaccio" (nome leggibile per chi estrae)
rm -rf build/zip
mkdir -p build/zip dist
cp -a build/pyinstaller/canovaccio build/zip/Canovaccio
(cd build/zip && "../../$PY" -m zipfile -c "../../dist/Canovaccio-$VERSION-windows-x86_64.zip" Canovaccio)
echo "Creato dist/Canovaccio-$VERSION-windows-x86_64.zip"
