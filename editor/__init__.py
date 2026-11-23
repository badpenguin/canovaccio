"""Canovaccio (nome interno todo-musician): editor musicale nativo Linux."""

__version__ = "0.6.3"  # tenere allineato con pyproject.toml
# 2026-11-23: nome visibile del programma, scelto dall'utente per la pubblicazione (prima, per poco,
# "The Musician"): titolo della finestra, About, dialogo Open, nome della porta MIDI.
APP_NAME = "Canovaccio"
# 2026-11-23: ID dell'app (GTK/D-Bus, icona, collegamento .desktop) col nome pubblico, scelta dell'utente
# (prima "io.github.todomusician.Editor"); il file dell'icona in resources/icons/hicolor/scalable/apps
# deve chiamarsi come l'ID. Sta qui e non in ui/app.py perché lo usa anche main_window (import circolare).
APP_ID = "io.github.badpenguin.Canovaccio"

# 2026-11-23: le costanti col nome del programma stanno tutte qui (scelta dell'utente), scritte per intero
# e non ricavate da APP_NAME: un cambio di nome non deve spostare da solo la cartella delle preferenze.

# nome della cartella delle preferenze (prima "todo-musician"; le preferenze vecchie si spostano a mano,
# nessuna migrazione automatica); il percorso completo lo costruisce system.config_dir()
APP_DIR = "canovaccio"
# 2026-11-23: comando installato da Help > Desktop Integration… come link in ~/.local/bin (editor/desktop.py)
APP_COMMAND = "canovaccio"
# variabili d'ambiente del synth interno in automatico (prima TODO_MUSICIAN_SF2 e TODO_MUSICIAN_AUDIO_DRIVER)
SOUNDFONT_ENV = "CANOVACCIO_SF2"
DRIVER_ENV = "CANOVACCIO_AUDIO_DRIVER"

# 2026-11-23: sito del progetto e autore, mostrati in Help > About (richiesta dell'utente)
APP_WEBSITE = "https://github.com/badpenguin/canovaccio"
AUTHOR = "Antonio Gallo"
AUTHOR_WEBSITE = "https://www.antoniogallo.it"
