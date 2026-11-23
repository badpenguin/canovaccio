"""Segment > Import MIDI…: sceglie un file MIDI da una cartella (anche con migliaia di file),
un canale (se il file ne ha più d'uno) e un tratto da battuta a battuta, lo ascolta e lo
importa come segmento di una traccia, all'inizio di una misura.

Decisioni dell'utente (2026-11-20): niente anteprima grafica delle battute; la preview parte
da sola selezionando un file e suona con il suono della traccia di destinazione al tempo del
brano; se il segmento si sovrappone a un altro, l'import si rifiuta con un errore.
La logica (ritaglio, segmento, brano della preview) sta in importer/midi_clip.py.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Callable

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gio, GLib, Gtk, Pango  # noqa: E402

from ..core.document import AddSegments  # noqa: E402
from ..core.model import Segment, Track  # noqa: E402
from ..importer.midi_clip import (ClipSource, clip_group, clip_segment, is_midi_file,  # noqa: E402
                                  preview_song, read_clip_source)
from ..output.performance import build_performance  # noqa: E402
from ..output.transport import State  # noqa: E402
from . import preferences  # noqa: E402

PARENT = ".."  # voce per salire di una cartella
PREF_KEY = "midi_clip"  # chiave in last_dirs delle preferenze


class MidiClipDialog(Gtk.Window):
    """Dialogo dell'import di un tratto MIDI in un segmento."""

    def __init__(self, parent: Gtk.Window, session, track: Track | None, bar: int,
                 on_imported: Callable[[Track, Segment], None]) -> None:
        super().__init__(title="Import MIDI into Segment", transient_for=parent, modal=True,
                         default_width=860, default_height=540)
        self.session = session
        self.doc = session.document
        self.transport = session.transport
        self.on_imported = on_imported
        self.source: ClipSource | None = None
        self.folder = Path.home()
        self._names: list[str] = []  # voci dell'elenco: "..", cartelle con "/" finale, file
        self._saved: tuple | None = None  # (loop, posizione) del brano prima della prima preview
        self._previewing = False
        self._loading = False  # True mentre si riempiono i controlli: niente ripartenze della preview

        # tracce di destinazione: tutte tranne la traccia accordi
        self.targets = [t for t in self.doc.song.tracks if t.kind != "chords"]

        # --- sinistra: cartella, filtro, elenco dei file --------------------
        self.folder_label = Gtk.Label(xalign=0, hexpand=True, ellipsize=Pango.EllipsizeMode.START)
        folder_btn = Gtk.Button(label="Folder…")
        folder_btn.connect("clicked", lambda *_: self._choose_folder())
        folder_row = Gtk.Box(spacing=6)
        folder_row.append(self.folder_label)
        folder_row.append(folder_btn)
        self.search = Gtk.SearchEntry(placeholder_text="Filter")
        self.search.connect("search-changed", lambda *_: self._refilter())
        self.count_label = Gtk.Label(xalign=0)
        self.count_label.add_css_class("dim-label")

        self.store = Gtk.StringList()
        self.filter = Gtk.CustomFilter.new(self._match)
        self.filtered = Gtk.FilterListModel(model=self.store, filter=self.filter)
        self.selection = Gtk.SingleSelection(model=self.filtered, autoselect=False, can_unselect=True)
        self.selection.connect("notify::selected", lambda *_: self._on_selected())
        factory = Gtk.SignalListItemFactory()
        factory.connect("setup", self._setup_row)
        factory.connect("bind", self._bind_row)
        self.list = Gtk.ListView(model=self.selection, factory=factory)
        self.list.connect("activate", self._on_activate)
        scroller = Gtk.ScrolledWindow(vexpand=True, hscrollbar_policy=Gtk.PolicyType.NEVER)
        scroller.set_child(self.list)
        scroller.add_css_class("frame")
        left = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8, hexpand=True)
        for w in (folder_row, self.search, self.count_label, scroller):
            left.append(w)

        # --- destra: file scelto, canale, battute, preview -----------------
        self.summary = Gtk.Label(xalign=0, wrap=True, label="Select a MIDI file.")
        self.summary.add_css_class("dim-label")
        self.channel = Gtk.DropDown.new_from_strings([])
        self.channel.connect("notify::selected", lambda *_: self._content_changed())
        self.bar_from = Gtk.SpinButton.new_with_range(1, 1, 1)
        self.bar_to = Gtk.SpinButton.new_with_range(1, 1, 1)
        self.bar_from.connect("value-changed", lambda *_: self._bars_changed(self.bar_from))
        self.bar_to.connect("value-changed", lambda *_: self._bars_changed(self.bar_to))
        self.bars_label = Gtk.Label(xalign=0)
        self.bars_label.add_css_class("dim-label")
        bars = Gtk.Box(spacing=6)
        for w in (Gtk.Label(label="from"), self.bar_from, Gtk.Label(label="to"), self.bar_to, self.bars_label):
            bars.append(w)
        grid = Gtk.Grid(row_spacing=10, column_spacing=12)
        grid.attach(Gtk.Label(label="Channel", xalign=1), 0, 0, 1, 1)
        grid.attach(self.channel, 1, 0, 1, 1)
        grid.attach(Gtk.Label(label="Bars", xalign=1), 0, 1, 1, 1)
        grid.attach(bars, 1, 1, 1, 1)

        play = Gtk.Button(label="Preview")
        play.connect("clicked", lambda *_: self._preview())
        stop = Gtk.Button(label="Stop")
        stop.connect("clicked", lambda *_: self._stop())
        self.loop = Gtk.CheckButton(label="Loop", active=True)
        self.loop.connect("toggled", lambda *_: self._content_changed())
        transport_row = Gtk.Box(spacing=8)
        for w in (play, stop, self.loop):
            transport_row.append(w)
        self.error = Gtk.Label(xalign=0, wrap=True)
        self.error.add_css_class("error")
        right = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14, hexpand=True)
        for w in (self.summary, grid, transport_row, self.error):
            right.append(w)

        # --- in basso: destinazione e pulsanti ------------------------------
        self.target = Gtk.DropDown.new_from_strings([t.name for t in self.targets])
        if track in self.targets:
            self.target.set_selected(self.targets.index(track))
        self.target.connect("notify::selected", lambda *_: self._content_changed())
        self.at_bar = Gtk.SpinButton.new_with_range(1, 9999, 1)
        self.at_bar.set_value(bar + 1)  # in UI le misure partono da 1
        self.at_bar.connect("value-changed", lambda *_: self._content_changed())
        cancel = Gtk.Button(label="Cancel")
        cancel.connect("clicked", lambda *_: self.close())
        self.ok = Gtk.Button(label="Import")
        self.ok.add_css_class("suggested-action")
        self.ok.connect("clicked", lambda *_: self._import())
        bottom = Gtk.Box(spacing=8)
        for w in (Gtk.Label(label="Into"), self.target, Gtk.Label(label="at bar"), self.at_bar):
            bottom.append(w)
        spacer = Gtk.Box(hexpand=True)
        bottom.append(spacer)
        bottom.append(cancel)
        bottom.append(self.ok)

        panes = Gtk.Box(spacing=18, homogeneous=True, vexpand=True)
        panes.append(left)
        panes.append(right)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12,
                      margin_start=18, margin_end=18, margin_top=18, margin_bottom=18)
        box.append(panes)
        box.append(Gtk.Separator())
        box.append(bottom)
        self.set_child(box)
        esc = Gtk.ShortcutController()
        esc.add_shortcut(Gtk.Shortcut.new(Gtk.ShortcutTrigger.parse_string("Escape"),
                                          Gtk.NamedAction.new("window.close")))
        self.add_controller(esc)
        self.connect("close-request", lambda *_: self._restore() or False)

        self._set_source(None)
        last = preferences.load().get("last_dirs", {}).get(PREF_KEY)
        self._open_folder(Path(last) if last and Path(last).is_dir() else Path.home())

    # --- elenco dei file -----------------------------------------------------

    def _setup_row(self, _factory, item) -> None:
        """Riga dell'elenco: icona (cartella o file) + nome."""
        row = Gtk.Box(spacing=8)
        row.append(Gtk.Image())
        row.append(Gtk.Label(xalign=0, ellipsize=Pango.EllipsizeMode.END))
        item.set_child(row)

    def _bind_row(self, _factory, item) -> None:
        name = item.get_item().get_string()
        icon, label = item.get_child().get_first_child(), item.get_child().get_last_child()
        is_dir = name == PARENT or name.endswith("/")
        icon.set_from_icon_name("folder-symbolic" if is_dir else "audio-x-generic-symbolic")
        label.set_label(name.rstrip("/") if name != PARENT else PARENT)

    def _open_folder(self, folder: Path) -> None:
        """Mostra la cartella: prima "..", poi le sottocartelle, poi i file MIDI, in ordine alfabetico."""
        try:
            entries = list(os.scandir(folder))
        except OSError as exc:
            self.error.set_label(f"Can't open {folder}: {exc.strerror or exc}")
            return
        self.folder = folder
        self.folder_label.set_label(str(folder))
        self.folder_label.set_tooltip_text(str(folder))
        dirs = sorted((e.name + "/" for e in entries if e.is_dir() and not e.name.startswith(".")), key=str.lower)
        files = sorted((e.name for e in entries if e.is_file() and is_midi_file(e.name)), key=str.lower)
        self._names = ([PARENT] if folder.parent != folder else []) + dirs + files
        self.store.splice(0, self.store.get_n_items(), self._names)
        self.selection.set_selected(Gtk.INVALID_LIST_POSITION)
        self._refilter()
        dirs_pref = dict(preferences.load().get("last_dirs", {}))
        dirs_pref[PREF_KEY] = str(folder)
        preferences.save(last_dirs=dirs_pref)

    def _match(self, item) -> bool:
        """Filtro: le cartelle restano sempre; un file passa se il nome contiene tutte le parole."""
        name = item.get_string()
        if name == PARENT or name.endswith("/"):
            return True
        words = self.search.get_text().lower().split()
        return all(w in name.lower() for w in words)

    def _refilter(self) -> None:
        self.filter.changed(Gtk.FilterChange.DIFFERENT)
        files = sum(1 for n in self._names if n != PARENT and not n.endswith("/"))
        shown = sum(1 for i in range(self.filtered.get_n_items())
                    if not self.filtered.get_item(i).get_string().endswith("/")
                    and self.filtered.get_item(i).get_string() != PARENT)
        self.count_label.set_label(f"{files} {'file' if files == 1 else 'files'} · {shown} shown")

    def _selected_name(self) -> str | None:
        item = self.selection.get_selected_item()
        return item.get_string() if item is not None else None

    def _on_activate(self, _view, position: int) -> None:
        """Invio o doppio click: entra nella cartella (o sale con "..")."""
        name = self.filtered.get_item(position).get_string()
        if name == PARENT:
            self._open_folder(self.folder.parent)
        elif name.endswith("/"):
            self._open_folder(self.folder / name.rstrip("/"))

    def _on_selected(self) -> None:
        """Un file selezionato si legge e parte subito la preview (richiesta dell'utente)."""
        name = self._selected_name()
        if name is None or name == PARENT or name.endswith("/"):
            return
        try:
            source = read_clip_source(self.folder / name)
        except Exception as exc:  # noqa: BLE001 — file rotto o non MIDI: lo si dice e basta
            self._set_source(None)
            self.error.set_label(f"Can't read {name}: {exc}")
            self._stop()
            return
        self._set_source(source)
        self._preview()

    def _choose_folder(self) -> None:
        dialog = Gtk.FileDialog(title="Choose a folder of MIDI files")
        dialog.set_initial_folder(Gio.File.new_for_path(str(self.folder)))

        def chosen(d, result) -> None:
            """Cartella scelta nel dialogo di sistema."""
            try:
                gfile = d.select_folder_finish(result)
            except GLib.Error:
                return
            if gfile and gfile.get_path():
                self._open_folder(Path(gfile.get_path()))

        dialog.select_folder(self, None, chosen)

    # --- canale e battute ----------------------------------------------------

    def _set_source(self, source: ClipSource | None) -> None:
        """Aggiorna canale, battute e riepilogo per il file scelto (None = nessuno)."""
        self.source = source
        self.error.set_label("")
        self._loading = True  # i cambi qui sotto non devono far ripartire la preview
        if source is None:
            self.summary.set_label("Select a MIDI file.")
            self.channel.set_model(Gtk.StringList.new([]))
            self.channel.set_sensitive(False)
            for spin in (self.bar_from, self.bar_to):
                spin.set_range(1, 1)
                spin.set_sensitive(False)
            self.bars_label.set_label("")
        else:
            self.summary.set_label(f"{source.path.name}\n{source.summary()}")
            self.channel.set_model(Gtk.StringList.new([f"{c.name} · {c.notes} notes" for c in source.channels]))
            # di default la batteria (canale 10) se c'è; un solo canale = si prende tutto
            drums = next((i for i, c in enumerate(source.channels) if c.channel == 10), 0)
            self.channel.set_selected(drums)
            self.channel.set_sensitive(len(source.channels) > 1)
            for spin in (self.bar_from, self.bar_to):
                spin.set_range(1, source.bars)
                spin.set_sensitive(True)
            self.bar_from.set_value(1)
            self.bar_to.set_value(source.bars)
        self._loading = False
        self._update_bars_label()
        self.ok.set_sensitive(source is not None)

    def _bars_changed(self, changed: Gtk.SpinButton) -> None:
        """Tiene "da" ≤ "a": spostando uno oltre l'altro, l'altro lo segue."""
        a, b = self.bar_from.get_value_as_int(), self.bar_to.get_value_as_int()
        if a > b:
            (self.bar_to if changed is self.bar_from else self.bar_from).set_value(changed.get_value_as_int())
        self._update_bars_label()
        self._content_changed()

    def _update_bars_label(self) -> None:
        if self.source is None:
            self.bars_label.set_label("")
            return
        n = self.bar_to.get_value_as_int() - self.bar_from.get_value_as_int() + 1
        self.bars_label.set_label(f"({n} {'bar' if n == 1 else 'bars'})")

    def _clip(self):
        """Gruppo e durata del tratto scelto, o None senza file."""
        if self.source is None:
            return None
        channel = self.source.channels[self.channel.get_selected()].channel
        return clip_group(self.source, channel, self.bar_from.get_value_as_int() - 1,
                          self.bar_to.get_value_as_int() - 1)

    def _target(self) -> Track | None:
        i = self.target.get_selected()
        return self.targets[i] if 0 <= i < len(self.targets) else None

    def _content_changed(self) -> None:
        """Canale, battute, destinazione o loop cambiati: se la preview suona, riparte."""
        self.error.set_label("")
        if self._previewing and not self._loading and self.transport.state is State.PLAYING:
            self._preview()

    # --- preview ---------------------------------------------------------------

    def _preview(self) -> None:
        """Suona il tratto con il suono della traccia di destinazione, al tempo del brano."""
        clip, track = self._clip(), self._target()
        if clip is None or track is None:
            return
        group, length = clip
        if self._saved is None:  # la prima volta: si ricorda il loop e la posizione del brano
            self._saved = (self.transport.loop, self.transport.position)
        song = preview_song(group, length, track, self.doc.song, self.at_bar.get_value_as_int() - 1)
        self.transport.stop()
        self.transport.set_silenced(set())
        self.transport.load(build_performance(song))
        end = song.tempos.seconds(length)
        self.transport.set_loop((0.0, end) if self.loop.get_active() else None)
        self.transport.seek(0.0)
        self.transport.play()
        self._previewing = True

    def _stop(self) -> None:
        if self._previewing:
            self.transport.stop()

    def _restore(self) -> None:
        """Rimette nel transport il brano, il suo loop, mute/solo e la posizione di prima."""
        if not self._previewing:
            return
        self._previewing = False
        song = self.doc.song
        self.transport.stop()
        loop, pos = self._saved or (None, 0.0)
        self.transport.load(build_performance(song))
        self.transport.set_silenced(song.silent_track_ids())
        self.transport.set_loop(loop)
        self.transport.seek(pos)

    # --- import ----------------------------------------------------------------

    def _import(self) -> None:
        """Crea il segmento all'inizio della misura scelta; se si sovrappone a un altro della
        traccia, errore e niente import (decisione dell'utente 2026-11-20)."""
        clip, track = self._clip(), self._target()
        if clip is None or track is None:
            self.error.set_label("Select a MIDI file and a track first.")
            return
        group, length = clip
        seg = clip_segment(group, length, self.at_bar.get_value_as_int() - 1)
        item = (track, seg, group)
        if not AddSegments.fits(self.doc.song, [item]):
            self.error.set_label(f"The segment would overlap another one on “{track.name}”. "
                                 "Choose another bar or track.")
            return
        self._restore()
        cmd = AddSegments([item])
        cmd.label = "Import MIDI segment"
        self.doc.execute(cmd)
        self.on_imported(track, seg)
        self.close()
