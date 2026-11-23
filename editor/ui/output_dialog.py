"""File > Output Devices…: elenco delle uscite (a sinistra) e impostazioni di quella
scelta (a destra). "Salva" salva e chiude; "Chiudi" o Esc chiudono senza salvare.

- Interno: synth FluidSynth con un SoundFont (automatico = il primo GM trovato) e un
  driver audio (automatico = il primo che si apre).
- Porta MIDI: una porta del sistema; "Invia Program Change" (alcuni device non lo vogliono).
"""

from __future__ import annotations

from pathlib import Path
from typing import Callable

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gio, Gtk  # noqa: E402

from ..output.devices import INTERNAL, KINDS, MIDI, OutputDevice  # noqa: E402
from ..output.fluidsynth_backend import find_soundfont  # noqa: E402
from ..output.midi_port_backend import list_ports  # noqa: E402
from ..output.patch_lists import list_names  # noqa: E402
from ..system import audio_drivers  # noqa: E402

AUTO = "automatic"


class OutputDevicesDialog(Gtk.Window):
    def __init__(self, parent: Gtk.Window, devices: list[OutputDevice],
                 on_save: Callable[[list[OutputDevice]], None]) -> None:
        super().__init__(title="Output Devices", transient_for=parent, modal=True,
                         default_width=640, default_height=340)
        self.devices = devices
        self.on_save = on_save
        self._loading = False  # mentre si riempie il modulo: niente scritture sull'uscita
        self.ports: list[str] = []  # porte presenti ora
        self._shown_ports: list[str] = []  # nella tendina (anche quella salvata ma scollegata)

        # sinistra: elenco + "+" "−"
        self.list = Gtk.ListBox(selection_mode=Gtk.SelectionMode.SINGLE)
        self.list.connect("row-selected", lambda *_: self._load())
        scroll = Gtk.ScrolledWindow(vexpand=True, hscrollbar_policy=Gtk.PolicyType.NEVER, child=self.list)
        scroll.set_size_request(190, -1)
        add = Gtk.Button(icon_name="list-add-symbolic", tooltip_text="New output")
        add.connect("clicked", lambda *_: self._add())
        self.remove = Gtk.Button(icon_name="list-remove-symbolic", tooltip_text="Remove output")
        self.remove.connect("clicked", lambda *_: self._remove())
        buttons = Gtk.Box(spacing=4)
        buttons.append(add)
        buttons.append(self.remove)
        left = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        frame = Gtk.Frame(child=scroll)
        left.append(frame)
        left.append(buttons)

        # destra: modulo
        self.form = Gtk.Grid(row_spacing=10, column_spacing=12, hexpand=True)
        self.name = Gtk.Entry(hexpand=True)
        self.name.connect("changed", lambda *_: self._set_name())
        self.kind = Gtk.DropDown.new_from_strings([label for _k, label in KINDS])
        self.kind.connect("notify::selected", lambda *_: self._set_kind())
        # interno
        self.sf_label = Gtk.Label(xalign=0, hexpand=True, ellipsize=1)  # Pango.EllipsizeMode.START
        sf_choose = Gtk.Button(label="Choose…")
        sf_choose.connect("clicked", lambda *_: self._choose_soundfont())
        sf_auto = Gtk.Button(label="Automatic")
        sf_auto.connect("clicked", lambda *_: self._set_soundfont(""))
        sf_box = Gtk.Box(spacing=6)
        for w in (self.sf_label, sf_choose, sf_auto):
            sf_box.append(w)
        self.drivers = [""] + audio_drivers()
        self.driver = Gtk.DropDown.new_from_strings([AUTO] + audio_drivers())
        self.driver.connect("notify::selected", lambda *_: self._set_driver())
        # MIDI
        self.port = Gtk.DropDown.new_from_strings([])
        self.port.connect("notify::selected", lambda *_: self._set_port())
        refresh = Gtk.Button(icon_name="view-refresh-symbolic", tooltip_text="Rescan MIDI ports")
        refresh.connect("clicked", lambda *_: self._load())
        port_box = Gtk.Box(spacing=6)
        port_box.append(self.port)
        port_box.append(refresh)
        self.port.set_hexpand(True)
        self.program_change = Gtk.CheckButton(label="Send Program Change (GM instrument of the track)")
        self.program_change.connect("toggled", lambda *_: self._set_program_change())
        self.patch_lists = [""] + list_names()
        self.patch_list = Gtk.DropDown.new_from_strings(["(none)"] + self.patch_lists[1:])
        self.patch_list.set_tooltip_text("Device sounds: chosen by name in the track properties")
        self.patch_list.connect("notify::selected", lambda *_: self._set_patch_list())
        self.no_ports = Gtk.Label(xalign=0, wrap=True,
                                  label="No MIDI port found: connect the device and press ↻.")

        self.rows: dict[str, list[Gtk.Widget]] = {INTERNAL: [], MIDI: []}
        r = 0
        for label, widget, kind in (("Name", self.name, None), ("Type", self.kind, None),
                                    ("SoundFont", sf_box, INTERNAL), ("Audio driver", self.driver, INTERNAL),
                                    ("Port", port_box, MIDI), ("", self.program_change, MIDI),
                                    ("Sound list", self.patch_list, MIDI), ("", self.no_ports, MIDI)):
            lbl = Gtk.Label(label=label, xalign=1)
            self.form.attach(lbl, 0, r, 1, 1)
            self.form.attach(widget, 1, r, 1, 1)
            if kind:
                self.rows[kind] += [lbl, widget]
            r += 1

        close = Gtk.Button(label="Close")
        close.connect("clicked", lambda *_: self.close())
        save = Gtk.Button(label="Save")
        save.add_css_class("suggested-action")
        save.connect("clicked", lambda *_: self._save())
        buttons_end = Gtk.Box(spacing=8, halign=Gtk.Align.END)
        buttons_end.append(close)
        buttons_end.append(save)
        right = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12, hexpand=True)
        right.append(self.form)
        right.append(Gtk.Box(vexpand=True))
        right.append(buttons_end)

        box = Gtk.Box(spacing=18, margin_start=18, margin_end=18, margin_top=18, margin_bottom=18)
        box.append(left)
        box.append(right)
        self.set_child(box)
        self.set_default_widget(save)
        esc = Gtk.ShortcutController()
        esc.add_shortcut(Gtk.Shortcut.new(Gtk.ShortcutTrigger.parse_string("Escape"), Gtk.NamedAction.new("window.close")))
        self.add_controller(esc)

        self._fill_list(0)

    def _save(self) -> None:
        self.on_save(self.devices)
        self.close()

    # --- elenco ------------------------------------------------------------

    def _fill_list(self, select: int) -> None:
        while (row := self.list.get_row_at_index(0)) is not None:
            self.list.remove(row)
        for d in self.devices:
            self.list.append(Gtk.Label(label=d.name, xalign=0, margin_start=8, margin_end=8,
                                       margin_top=6, margin_bottom=6))
        self.list.select_row(self.list.get_row_at_index(max(0, min(select, len(self.devices) - 1))))
        self.remove.set_sensitive(len(self.devices) > 1)

    def _index(self) -> int:
        row = self.list.get_selected_row()
        return row.get_index() if row is not None else -1

    def _device(self) -> OutputDevice | None:
        i = self._index()
        return self.devices[i] if 0 <= i < len(self.devices) else None

    def _add(self) -> None:
        names = {d.name for d in self.devices}
        n = 1
        while f"Output {n}" in names:
            n += 1
        self.devices.append(OutputDevice(f"Output {n}", kind=MIDI))
        self._fill_list(len(self.devices) - 1)
        self.name.grab_focus()

    def _remove(self) -> None:
        i = self._index()
        if len(self.devices) > 1 and i >= 0:
            del self.devices[i]
            self._fill_list(i)

    # --- modulo ------------------------------------------------------------

    def _load(self) -> None:
        d = self._device()
        if d is None:
            return
        self._loading = True
        self.name.set_text(d.name)
        self.kind.set_selected(0 if d.kind == INTERNAL else 1)
        self._show_soundfont(d)
        self.driver.set_selected(self.drivers.index(d.driver) if d.driver in self.drivers else 0)
        self.ports = list_ports()
        shown = list(self.ports)
        if d.port and d.port not in shown:
            shown.append(d.port)
        labels = [p if p in self.ports else f"{p} (not connected)" for p in shown]
        self.port.set_model(Gtk.StringList.new(labels))
        self._shown_ports = shown
        if d.port in shown:
            self.port.set_selected(shown.index(d.port))
        elif shown:
            self.port.set_selected(0)
            d.port = shown[0]
        self.program_change.set_active(d.program_change)
        self.patch_list.set_selected(self.patch_lists.index(d.patch_list) if d.patch_list in self.patch_lists else 0)
        self._loading = False
        self._show_kind(d.kind)

    def _show_kind(self, kind: str) -> None:
        for k, widgets in self.rows.items():
            for w in widgets:
                w.set_visible(k == kind)
        if kind == MIDI:
            self.no_ports.set_visible(not self.ports)
            self.form.get_child_at(0, 7).set_visible(not self.ports)

    def _show_soundfont(self, d: OutputDevice) -> None:
        if d.soundfont:
            self.sf_label.set_label(Path(d.soundfont).name)
            self.sf_label.set_tooltip_text(d.soundfont)
        else:
            found = find_soundfont()
            self.sf_label.set_label(f"{AUTO} ({Path(found).name})" if found else f"{AUTO} (none found)")
            self.sf_label.set_tooltip_text(found)

    def _set_name(self) -> None:
        d = self._device()
        if d is None or self._loading:
            return
        d.name = self.name.get_text().strip() or d.name
        row = self.list.get_selected_row()
        if row is not None:
            row.get_child().set_label(d.name)

    def _set_kind(self) -> None:
        d = self._device()
        if d is None or self._loading:
            return
        d.kind = KINDS[self.kind.get_selected()][0]
        self._show_kind(d.kind)

    def _set_driver(self) -> None:
        d = self._device()
        if d is not None and not self._loading:
            d.driver = self.drivers[self.driver.get_selected()]

    def _set_port(self) -> None:
        d = self._device()
        i = self.port.get_selected()
        if d is not None and not self._loading and 0 <= i < len(self._shown_ports):
            d.port = self._shown_ports[i]

    def _set_patch_list(self) -> None:
        d = self._device()
        if d is not None and not self._loading:
            d.patch_list = self.patch_lists[self.patch_list.get_selected()]

    def _set_program_change(self) -> None:
        d = self._device()
        if d is not None and not self._loading:
            d.program_change = self.program_change.get_active()

    def _set_soundfont(self, path: str) -> None:
        d = self._device()
        if d is not None:
            d.soundfont = path
            self._show_soundfont(d)

    def _choose_soundfont(self) -> None:
        dialog = Gtk.FileDialog(title="SoundFont")
        sf = Gtk.FileFilter(name="SoundFont (*.sf2, *.sf3)")
        sf.add_pattern("*.sf2")
        sf.add_pattern("*.sf3")
        sf.add_pattern("*.SF2")
        filters = Gio.ListStore.new(Gtk.FileFilter)
        filters.append(sf)
        dialog.set_filters(filters)
        d = self._device()
        if d is not None and d.soundfont:
            dialog.set_initial_file(Gio.File.new_for_path(d.soundfont))

        def done(dlg, result) -> None:
            try:
                f = dlg.open_finish(result)
            except Exception:  # noqa: BLE001 — annullato
                return
            if f is not None and f.get_path():
                self._set_soundfont(f.get_path())

        dialog.open(self, None, done)
