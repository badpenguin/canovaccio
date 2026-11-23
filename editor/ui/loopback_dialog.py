"""File > Audio Loopback…: sentire nelle cuffie del PC l'audio USB di una tastiera
(ponte dall'ingresso all'uscita predefinita). Attiva e Disattiva agiscono subito;
ingresso e latenza usati con "Attiva" si ricordano per le volte dopo (anche dopo un
riavvio, quando il nome dell'ingresso può cambiare suffisso). Il ponte resta attivo anche
dopo la chiusura dell'app."""

from __future__ import annotations

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import GLib, Gtk  # noqa: E402

from ..output import loopback  # noqa: E402
from . import preferences  # noqa: E402

DEFAULT_LATENCY = 10  # ms


class LoopbackDialog(Gtk.Window):
    def __init__(self, parent: Gtk.Window) -> None:
        super().__init__(title="Audio Loopback", transient_for=parent, modal=True, resizable=False)
        self.sources: list[loopback.Source] = []
        self.saved = preferences.load().get("loopback", {})

        self.source = Gtk.DropDown.new_from_strings([])
        self.source.set_hexpand(True)
        self.source.connect("notify::selected", lambda *_: self._update_state())
        refresh = Gtk.Button(icon_name="view-refresh-symbolic", tooltip_text="Rescan inputs")
        refresh.connect("clicked", lambda *_: self._refresh())
        src_box = Gtk.Box(spacing=6)
        src_box.append(self.source)
        src_box.append(refresh)
        self.latency = Gtk.SpinButton.new_with_range(1, 500, 1)
        self.latency.set_value(self.saved.get("latency", DEFAULT_LATENCY))
        self.state = Gtk.Label(xalign=0)
        self.message = Gtk.Label(xalign=0, wrap=True, max_width_chars=50)

        grid = Gtk.Grid(row_spacing=10, column_spacing=12)
        for r, (label, widget) in enumerate((("Input", src_box), ("Latency (ms)", self.latency),
                                             ("Status", self.state))):
            grid.attach(Gtk.Label(label=label, xalign=1), 0, r, 1, 1)
            grid.attach(widget, 1, r, 1, 1)
        self.latency.set_halign(Gtk.Align.START)

        wireplumber = Gtk.Button(label="Restart WirePlumber")
        wireplumber.connect("clicked", lambda *_: self._restart_wireplumber())
        self.btn_on = Gtk.Button(label="Enable")
        self.btn_on.add_css_class("suggested-action")
        self.btn_on.connect("clicked", lambda *_: self._activate())
        self.btn_off = Gtk.Button(label="Disable")
        self.btn_off.connect("clicked", lambda *_: self._deactivate())
        close = Gtk.Button(label="Close")
        close.connect("clicked", lambda *_: self.close())
        buttons = Gtk.Box(spacing=8, margin_top=8)
        buttons.append(wireplumber)
        buttons.append(Gtk.Box(hexpand=True))
        for b in (self.btn_off, self.btn_on, close):
            buttons.append(b)

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12,
                      margin_start=18, margin_end=18, margin_top=18, margin_bottom=18)
        box.append(grid)
        box.append(self.message)
        box.append(buttons)
        self.set_child(box)
        esc = Gtk.ShortcutController()
        esc.add_shortcut(Gtk.Shortcut.new(Gtk.ShortcutTrigger.parse_string("Escape"), Gtk.NamedAction.new("window.close")))
        self.add_controller(esc)
        self._refresh()

    def _say(self, text: str) -> None:
        self.message.set_label(text)

    def _selected(self) -> loopback.Source | None:
        i = self.source.get_selected()
        return self.sources[i] if 0 <= i < len(self.sources) else None

    def _refresh(self) -> None:
        try:
            self.sources = loopback.sources()
        except RuntimeError as exc:
            self.sources = []
            self._say(f"Cannot read the inputs: {exc}")
        labels = [s.description for s in self.sources]
        self.source.set_model(Gtk.StringList.new(labels))
        stables = [s.stable for s in self.sources]
        if self.saved.get("source") in stables:
            self.source.set_selected(stables.index(self.saved["source"]))
        if not self.sources:
            self._say("No audio input found.")
        self._update_state()

    def _update_state(self) -> None:
        src = self._selected()
        if src is not None:
            self.source.set_tooltip_text(src.name)
        active = False
        try:
            active = src is not None and bool(loopback.bridges(src.name))
        except RuntimeError as exc:
            self._say(str(exc))
        self.state.set_label("bridge enabled" if active else "bridge disabled")
        self.btn_on.set_sensitive(src is not None and not active)
        self.btn_off.set_sensitive(active)

    def _activate(self) -> None:
        src = self._selected()
        if src is None:
            return
        latency = self.latency.get_value_as_int()
        try:
            loopback.load(src.name, latency)
            self._say("")
            self.saved = {"source": src.stable, "latency": latency}
            preferences.save(loopback=self.saved)
        except RuntimeError as exc:
            self._say(f"Bridge not enabled: {exc}")
        self._update_state()

    def _deactivate(self) -> None:
        src = self._selected()
        if src is None:
            return
        try:
            loopback.unload(src.name)
            self._say("")
        except RuntimeError as exc:
            self._say(f"Bridge not removed: {exc}")
        self._update_state()

    def _restart_wireplumber(self) -> None:
        try:
            loopback.restart_wireplumber()
        except RuntimeError as exc:
            self._say(f"WirePlumber not restarted: {exc}")
            return
        self._say("")
        GLib.timeout_add(2000, lambda: self._refresh() or False)  # i dispositivi ricompaiono dopo un attimo
