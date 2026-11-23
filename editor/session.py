"""Sessione dell'applicazione: documento aperto + uscita audio + transport.

Collega modello e playback senza dipendere da GTK.
"""

from __future__ import annotations

from pathlib import Path
from typing import Callable

from .core.document import Document
from .core.project import EXTENSION, STYLE_EXTENSION, load_project, save_project
from .exporter.midi import export_midi
from .importer.midi import import_midi
from .importer.musescore import import_musescore, is_musescore
from .importer.musicxml import import_musicxml, is_musicxml
from .importer.rosegarden import import_rosegarden
from .importer.style import import_style, is_style
from .output.performance import build_performance
from .output.backend import NullBackend, OutputBackend
from .output.devices import OutputDevice
from .output.registry import open_backend, open_device
from .output.transport import Transport


class Session:
    def __init__(self, backend: str | None = None, device: OutputDevice | None = None) -> None:
        """`device`: l'uscita configurata da usare; `backend`: per nome (test, es. "null")."""
        self.device = device
        if device is not None and backend is None:
            try:
                opened, self.backend_errors = open_device(device), []
            except Exception as exc:  # noqa: BLE001 — si resta muti, la UI lo dice
                opened, self.backend_errors = NullBackend(), [f"{device.name}: {exc}"]
        else:
            opened, self.backend_errors = open_backend(backend)
        self.transport = Transport(opened)
        self.document: Document | None = None
        self._listeners: list[Callable[[Document], None]] = []
        self._built: tuple[int, int] | None = None  # (documento, versione) dell'esecuzione caricata

    @property
    def backend(self) -> OutputBackend:
        return self.transport.backend

    def set_output(self, device: OutputDevice) -> None:
        """Passa a un'altra uscita (anche durante il play). Se non si apre solleva
        un'eccezione e resta quella di prima."""
        new = open_device(device)
        old = self.transport.backend
        self.transport.set_backend(new)
        old.close()
        self.device = device

    def on_document(self, fn: Callable[[Document], None]) -> None:
        """Notifica quando viene aperto un nuovo documento."""
        self._listeners.append(fn)

    def new_document(self) -> Document:
        """File > New: brano nuovo (tabelle di default, nessun file) con una traccia
        "Traccia 1" (canale 1) e un segmento vuoto di una misura."""
        from fractions import Fraction

        from .core.model import Group, Segment, Song, Track

        song = Song()
        group = Group("Track 1", 1, [])  # 2026-11-20: nome in inglese (prima "Traccia 1")
        song.groups[group.id] = group
        song.tracks.append(Track("Track 1", segments=[Segment(group.id, 0, Fraction(1))], channel=1))
        return self._open(song, "", None)

    def open_path(self, path: str | Path) -> Document:
        """Apre un progetto .music o importa un file MIDI, uno stile, un progetto Rosegarden,
        una partitura MuseScore o MusicXML."""
        if Path(path).suffix.lower() in (EXTENSION, STYLE_EXTENSION):
            return self._open(load_project(path), str(path), str(path))
        if Path(path).suffix.lower() == ".rg":  # progetto Rosegarden (2026-11-20)
            return self._open(import_rosegarden(path), str(path), None)
        if is_musescore(path):  # partitura MuseScore 2.x–4.x (2026-11-23)
            return self._open(import_musescore(path), str(path), None)
        if is_musicxml(path):  # partitura MusicXML (2026-11-23)
            return self._open(import_musicxml(path), str(path), None)
        if is_style(path):  # stile Yamaha
            return self._open(import_style(path), str(path), None)
        return self._open(import_midi(path), str(path), None)

    def open_midi(self, path: str | Path) -> Document:
        return self._open(import_midi(path), str(path), None)

    def _open(self, song, path: str, project_path: str | None) -> Document:
        if self.document:
            self.document.unsubscribe(self._reload)
        self.transport.stop()
        self.document = Document(song, path, project_path)
        self.document.subscribe(self._reload)
        self._built = None  # documento nuovo: l'esecuzione si costruisce sempre
        self._reload(self.document)
        for fn in list(self._listeners):
            fn(self.document)
        return self.document

    def save(self, path: str | Path) -> None:
        """Salva il documento come progetto .music."""
        save_project(self.document.song, path)
        self.document.mark_saved(str(path))

    def export_midi(self, path: str | Path) -> None:
        export_midi(self.document.song, path)

    def _reload(self, doc: Document) -> None:
        """A ogni modifica del documento: esecuzione nuova per il play e tracce silenziate.
        2026-11-20: l'esecuzione si ricostruisce solo se è cambiato il contenuto
        (doc.version); Mute/Solo (touch) e Save (mark_saved) aggiornano solo le tracce
        silenziate, così il play non si interrompe e non si ricalcola tutto."""
        built = (id(doc), doc.version)
        if self._built != built:
            self._built = built
            self.transport.load(build_performance(doc.song))
        self.transport.set_silenced(doc.song.silent_track_ids())

    def close(self) -> None:
        self.transport.close()
        self.backend.close()
