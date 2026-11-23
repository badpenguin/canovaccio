#!/usr/bin/env bash
# Avvia Canovaccio installato da sorgente (README.md, "Install from source").
# Uso: ./canovaccio.sh [file da aprire: progetto .music, MIDI, partitura, stile…]
# Senza argomenti apre la finestra vuota, come `.venv/bin/python -m editor`.
# 2026-11-23: sostituisce musician.sh (script personale che apriva examples/small.mid),
# così lo script si pubblica e serve a chi installa da sorgente.
set -euo pipefail
# si lavora nella cartella dello script, anche se lanciato da altrove o tramite un link;
# i file passati come argomento si rendono assoluti prima, perché restino validi
args=()
for arg in "$@"; do
    if [[ -e "$arg" ]]; then
        args+=("$(readlink -f "$arg")")
    else
        args+=("$arg")
    fi
done
cd "$(dirname "$(readlink -f "$0")")"
if [[ ! -x .venv/bin/python ]]; then
    echo "The Python environment .venv is missing: see README.md, section \"Install from source\"." >&2
    exit 1
fi
exec .venv/bin/python -m editor "${args[@]}"
