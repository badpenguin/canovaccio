#!/usr/bin/env bash
# Costruisce la versione macOS di Canovaccio per Apple Silicon (2026-11-23; solo Apple Silicon, scelta
# dell'utente: i Mac Intel non si vendono più dal 2020). Pensato per GitHub Actions (.github/workflows/build.yml),
# su un runner macOS arm64 con GTK 4, PyGObject e FluidSynth di Homebrew, il venv .venv già pronto e il
# SoundFont in build/soundfonts (lo prepara il job "soundfont" del workflow).
#
# Risultato: dist/Canovaccio-<versione>-macos-arm64.dmg (Canovaccio.app da trascinare in Applicazioni).
set -euo pipefail
cd "$(dirname "$0")/.."

PY=.venv/bin/python
VERSION="$("$PY" -c 'import editor; print(editor.__version__)')"

test -f build/soundfonts/TimGM6mb.sf2 || { echo "build/soundfonts/TimGM6mb.sf2 mancante" >&2; exit 1; }

# icona del programma in PNG grande (PyInstaller la converte in .icns con Pillow)
mkdir -p build/icons
rsvg-convert -w 1024 -h 1024 editor/resources/icons/hicolor/scalable/apps/io.github.badpenguin.Canovaccio.svg \
    -o build/icons/canovaccio.png

"$PY" -m PyInstaller --noconfirm --clean --distpath build/pyinstaller --workpath build/work packaging/canovaccio.spec

# immagine disco: l'app e un collegamento ad Applicazioni, per installarla trascinandola
rm -rf build/dmg
mkdir -p build/dmg dist
cp -a build/pyinstaller/Canovaccio.app build/dmg/
ln -s /Applications build/dmg/Applications
hdiutil create -volname "Canovaccio" -srcfolder build/dmg -ov -format UDZO "dist/Canovaccio-$VERSION-macos-arm64.dmg"
echo "Creato dist/Canovaccio-$VERSION-macos-arm64.dmg"
