"""Finestra di dialogo dei parametri di una traccia (nome, chiave, strumento, canale MIDI, velocità).

In fondo, il suono della traccia sull'uscita in uso (tendina accanto al player), al
posto dello strumento GM: per l'interno un suono di un SoundFont (elenco letto dal
file), per una porta MIDI Bank Select MSB/LSB + Program Change (mandati solo se
l'uscita ha "Invia Program Change"). Si salva nel brano, per nome dell'uscita.

Salva applica le modifiche (un solo passo di Ctrl+Z); Annulla o Esc le scarta.
"""

from __future__ import annotations

import gi

gi.require_version("Gtk", "4.0")
from pathlib import Path  # noqa: E402

from gi.repository import Gio, GLib, Gtk  # noqa: E402

from ..core.clef import MVP_CLEFS  # noqa: E402
from ..core.document import Command, Compound, Document, SetAttribute  # noqa: E402
from ..core.instrument import GM_FAMILIES, GM_PROGRAMS, GMInstrument  # noqa: E402
from ..core.model import Sound, Track  # noqa: E402
from ..output.devices import INTERNAL, NAME_ALIASES, OutputDevice  # noqa: E402
from .patch_picker import PatchPicker  # noqa: E402


# 2026-11-23: menu degli strumenti GM costruito una volta e riusato dai dialoghi successivi (idea
# dell'utente): GTK costruisce subito i widget di tutte le 129 voci e dei 16 sottomenu, e le misure
# davano 250–375 ms a ogni apertura. Le voci non cambiano mai (elenco GM fisso); la voce scelta la
# segna l'azione "dlg.program" del dialogo in cui il menu è attaccato.
_GM_POPOVER: Gtk.PopoverMenu | None = None


def _gm_popover() -> Gtk.PopoverMenu:
    """Menu Instrument: "Drum" in cima, poi le famiglie GM, ognuna con i suoi strumenti a lato.
    Costruito alla prima chiamata, poi lo stesso oggetto (staccato dal dialogo che lo usava)."""
    global _GM_POPOVER
    if _GM_POPOVER is None:
        menu = Gio.Menu()
        drum_item = Gio.MenuItem.new(_program_label(DRUM), None)
        drum_item.set_action_and_target_value("dlg.program", GLib.Variant("i", DRUM))
        drum_section = Gio.Menu()
        drum_section.append_item(drum_item)
        menu.append_section(None, drum_section)
        families = Gio.Menu()
        menu.append_section(None, families)
        for name, first, last in GM_FAMILIES:
            family = Gio.Menu()
            for p in range(first, last):
                item = Gio.MenuItem.new(_program_label(p), None)
                item.set_action_and_target_value("dlg.program", GLib.Variant("i", p))
                family.append_item(item)
            families.append_submenu(name, family)
        _GM_POPOVER = Gtk.PopoverMenu.new_from_model_full(menu, Gtk.PopoverMenuFlags.NESTED)
    owner = _GM_POPOVER.get_parent()
    if isinstance(owner, Gtk.MenuButton):  # ancora attaccato a un altro dialogo: si stacca
        owner.set_popover(None)
    return _GM_POPOVER


class TrackDialog(Gtk.Window):
    def __init__(self, parent: Gtk.Window, doc: Document, track: Track, device: OutputDevice | None = None) -> None:
        super().__init__(title=track.name, transient_for=parent, modal=True, resizable=False)
        self.doc, self.track, self.device = doc, track, device

        grid = Gtk.Grid(row_spacing=10, column_spacing=12)
        grid.attach(Gtk.Label(label="Name", xalign=1), 0, 0, 1, 1)
        self.name = Gtk.Entry(text=track.name, activates_default=True, hexpand=True)
        grid.attach(self.name, 1, 0, 1, 1)
        grid.attach(Gtk.Label(label="Clef", xalign=1), 0, 1, 1, 1)
        clefs = [c for c, _l in MVP_CLEFS]
        self.clef = Gtk.DropDown.new_from_strings([label for _c, label in MVP_CLEFS])
        if track.clef in clefs:
            self.clef.set_selected(clefs.index(track.clef))
        grid.attach(self.clef, 1, 1, 1, 1)

        # strumento: una sola select che apre le famiglie; ogni famiglia apre a lato
        # i suoi strumenti; in cima "Drum" (batteria su un canale qualsiasi, es. kit Yamaha sul 9
        # o sull'1). Canale 10 = sempre batteria: la scelta è disattivata e mostra "Drum".
        grid.attach(Gtk.Label(label="Instrument", xalign=1), 0, 2, 1, 1)
        program = DRUM if track.instrument.is_percussion else getattr(track.instrument, "program", 0)
        self.program = Gio.SimpleAction.new_stateful("program", GLib.VariantType.new("i"), GLib.Variant("i", program))
        self.program.connect("change-state", self._program_chosen)
        actions = Gio.SimpleActionGroup()
        actions.add_action(self.program)
        self.insert_action_group("dlg", actions)
        self.instrument = Gtk.MenuButton(label=_program_label(program), always_show_arrow=True)
        self.instrument.set_popover(_gm_popover())
        # il menu condiviso si stacca alla chiusura, altrimenti verrebbe distrutto con il dialogo
        self.connect("close-request", lambda *_: self.instrument.set_popover(None) or False)
        drum = Gtk.DropDown.new_from_strings(["Drum"])
        drum.set_sensitive(False)
        self.instrument_stack = Gtk.Stack()
        self.instrument_stack.add_named(self.instrument, "gm")
        self.instrument_stack.add_named(drum, "drum")
        grid.attach(self.instrument_stack, 1, 2, 1, 1)

        grid.attach(Gtk.Label(label="MIDI channel", xalign=1), 0, 3, 1, 1)
        self.channel = Gtk.SpinButton.new_with_range(1, 16, 1)
        self.channel.set_value(track.channel)
        self.channel.set_activates_default(True)
        self.channel.connect("value-changed", lambda *_: self._channel_changed())
        grid.attach(self.channel, 1, 3, 1, 1)
        grid.attach(Gtk.Label(label="Default velocity", xalign=1), 0, 4, 1, 1)
        self.velocity = Gtk.SpinButton.new_with_range(1, 127, 1)
        self.velocity.set_value(track.velocity)
        self.velocity.set_activates_default(True)
        grid.attach(self.velocity, 1, 4, 1, 1)
        self.sound_box = self._build_sound(device) if device is not None else None
        self._channel_changed()

        cancel = Gtk.Button(label="Cancel")
        cancel.connect("clicked", lambda *_: self.close())
        save = Gtk.Button(label="Save")
        save.add_css_class("suggested-action")
        save.connect("clicked", lambda *_: self.save())
        buttons = Gtk.Box(spacing=8, halign=Gtk.Align.END, margin_top=8)
        buttons.append(cancel)
        buttons.append(save)

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12,
                      margin_start=18, margin_end=18, margin_top=18, margin_bottom=18)
        box.append(grid)
        if self.sound_box is not None:
            box.append(Gtk.Separator())
            box.append(self.sound_box)
        box.append(buttons)
        self.set_child(box)
        self.set_default_widget(save)

        esc = Gtk.ShortcutController()
        esc.add_shortcut(Gtk.Shortcut.new(Gtk.ShortcutTrigger.parse_string("Escape"), Gtk.NamedAction.new("window.close")))
        self.add_controller(esc)

    def save(self) -> None:
        commands: list[Command] = []
        name = self.name.get_text().strip()
        if name and name != self.track.name:
            commands.append(SetAttribute(self.track, "name", name, "Rename track"))
        for attr, spin in (("channel", self.channel), ("velocity", self.velocity)):
            spin.update()  # valore digitato e non ancora confermato
            value = spin.get_value_as_int()
            if value != getattr(self.track, attr):
                commands.append(SetAttribute(self.track, attr, value))
        clef = MVP_CLEFS[self.clef.get_selected()][0]
        if clef != self.track.clef and not self._drum():
            commands.append(SetAttribute(self.track, "clef", clef, "Change clef"))
        if self._drum():
            instrument = GMInstrument(0, percussion=True)
        else:
            instrument = GMInstrument(self.program.get_state().get_int32())
        if instrument != self.track.instrument:
            commands.append(SetAttribute(self.track, "instrument", instrument, "Change instrument"))
        sounds = self._sounds()
        if sounds != self.track.sounds:
            commands.append(SetAttribute(self.track, "sounds", sounds, "Change sound"))
        if commands:
            self.doc.execute(Compound(commands, f"Properties of {self.track.name}"))
        self.close()

    def _drum(self) -> bool:
        return self.channel.get_value_as_int() == 10 or self.program.get_state().get_int32() == DRUM

    def _channel_changed(self) -> None:
        """Canale 10: sempre batteria (lo strumento non si sceglie). Batteria (canale 10 o
        "Drum" scelto): la chiave non si sceglie."""
        self.instrument_stack.set_visible_child_name("drum" if self.channel.get_value_as_int() == 10 else "gm")
        self.clef.set_sensitive(not self._drum())

    # --- suono sull'uscita in uso -------------------------------------------

    def _build_sound(self, device: OutputDevice) -> Gtk.Widget:
        # 2026-11-20: l'uscita interna si chiamava "Interno", ora "Internal": i brani salvati
        # prima hanno il suono sotto il vecchio nome, che si cerca come alias.
        current = self.track.sounds.get(device.name)
        if current is None and device.name in NAME_ALIASES:
            current = self.track.sounds.get(NAME_ALIASES[device.name])
        self.sound_on = Gtk.CheckButton(label="Sound different from the GM instrument", active=current is not None)
        self.sound_on.connect("toggled", lambda *_: self._sound_toggled())
        grid = Gtk.Grid(row_spacing=10, column_spacing=12)
        grid.attach(Gtk.Label(label=f"On “{device.name}”", xalign=1, css_classes=["heading"]), 0, 0, 1, 1)
        grid.attach(self.sound_on, 1, 0, 1, 1)
        self.sound_widgets: list[Gtk.Widget] = []
        if device.kind == INTERNAL:
            from ..output.fluidsynth_backend import find_soundfont

            self.default_sf = device.soundfont or find_soundfont() or ""
            self.sf_path = (current.soundfont if current and current.soundfont else self.default_sf)
            self.sf_label = Gtk.Label(xalign=0, hexpand=True, ellipsize=1)
            choose = Gtk.Button(label="Choose…")
            choose.connect("clicked", lambda *_: self._choose_soundfont())
            sf_box = Gtk.Box(spacing=6)
            sf_box.append(self.sf_label)
            sf_box.append(choose)
            self.preset = PatchPicker()  # ricerca per inizio di parola
            self.preset.set_size_request(320, -1)
            rows = (("SoundFont", sf_box), ("Sound", self.preset))
            self._load_presets(current)
        else:
            self.msb = Gtk.SpinButton.new_with_range(0, 127, 1)
            self.lsb = Gtk.SpinButton.new_with_range(0, 127, 1)
            self.pc = Gtk.SpinButton.new_with_range(1, 128, 1)  # come sui manuali: 1–128
            gm = 0 if self.track.instrument.is_percussion else getattr(self.track.instrument, "program", 0)
            self.msb.set_value(current.bank if current else 0)
            self.lsb.set_value(current.lsb if current else 0)
            self.pc.set_value((current.program if current else gm) + 1)
            bank = Gtk.Box(spacing=6)
            for w in (Gtk.Label(label="MSB"), self.msb, Gtk.Label(label="LSB", margin_start=8), self.lsb):
                bank.append(w)
            rows = (("Bank Select", bank), ("Program", self.pc))
            self.patch = None
            self.patches = self._device_patches(device)
            if self.patches:  # lista dei suoni dell'uscita: si sceglie per nome, i numeri si riempiono
                self.patch = PatchPicker([p.label() for p in self.patches], self._patch_chosen)
                self.patch.set_size_request(360, -1)
                self.patch.set_tooltip_text(f"List “{device.patch_list}”")
                for spin in (self.msb, self.lsb, self.pc):
                    spin.connect("value-changed", lambda *_: self._numbers_changed())
                rows = (("Sound", self.patch),) + rows
                self._numbers_changed()
        for r, (label, widget) in enumerate(rows, start=1):
            lbl = Gtk.Label(label=label, xalign=1)
            grid.attach(lbl, 0, r, 1, 1)
            grid.attach(widget, 1, r, 1, 1)
            widget.set_halign(Gtk.Align.FILL if device.kind == INTERNAL or widget is getattr(self, "patch", None)
                              else Gtk.Align.START)
            self.sound_widgets += [lbl, widget]
        if device.kind != INTERNAL and not device.program_change:
            note = Gtk.Label(label="On this output “Send Program Change” is off: the sound is not sent.",
                             xalign=0, wrap=True, max_width_chars=50)
            grid.attach(note, 0, len(rows) + 1, 2, 1)
        self._sound_toggled()
        return grid

    def _device_patches(self, device: OutputDevice) -> list:
        """Suoni della lista dell'uscita; per la batteria (canale 10) solo i kit."""
        from ..output.patch_lists import load

        patches = list(load(device.patch_list)) if device.patch_list else []
        if self._drum() or self.track.instrument.is_percussion:
            # 2026-11-20: su una porta MIDI la batteria mostra solo i kit (prima: kit in cima,
            # poi tutti gli altri suoni). Se la lista non ha kit resta intera, per poter scegliere.
            kits = [p for p in patches if p.drum]
            patches = kits or patches
        return patches

    def _patch_chosen(self) -> None:
        i = self.patch.get_selected()
        if getattr(self, "_syncing", False) or not 0 <= i < len(self.patches):
            return
        p = self.patches[i]
        self._syncing = True
        self.msb.set_value(p.msb)
        self.lsb.set_value(p.lsb)
        self.pc.set_value(p.program + 1)
        self._syncing = False

    def _numbers_changed(self) -> None:
        """Numeri scritti a mano: nella tendina il suono corrispondente (o nessuno)."""
        if getattr(self, "_syncing", False) or self.patch is None:
            return
        key = (self.msb.get_value_as_int(), self.lsb.get_value_as_int(), self.pc.get_value_as_int() - 1)
        index = next((i for i, p in enumerate(self.patches) if p.key == key), Gtk.INVALID_LIST_POSITION)
        self._syncing = True
        self.patch.set_selected(index)
        self._syncing = False

    def _sound_toggled(self) -> None:
        """Con un suono per l'uscita in uso lo strumento GM non conta: spento."""
        on = self.sound_on.get_active()
        for w in self.sound_widgets:
            w.set_sensitive(on)
        self.instrument_stack.set_sensitive(not on)

    def _load_presets(self, current: Sound | None = None) -> None:
        from ..output.fluidsynth_backend import list_presets

        self.sf_label.set_label(Path(self.sf_path).name if self.sf_path else "no SoundFont")
        self.sf_label.set_tooltip_text(self.sf_path)
        self.presets = list_presets(self.sf_path) if self.sf_path else []
        self.preset.set_items([f"{b:03d}:{p + 1:03d} {n}" for b, p, n in self.presets])
        if current is not None:
            want = (current.bank, current.program)
        elif self._drum() or self.track.instrument.is_percussion:
            want = (128, 0)
        else:
            want = (0, getattr(self.track.instrument, "program", 0))
        keys = [(b, p) for b, p, _n in self.presets]
        if want in keys:
            self.preset.set_selected(keys.index(want))

    def _choose_soundfont(self) -> None:
        dialog = Gtk.FileDialog(title="SoundFont")
        sf = Gtk.FileFilter(name="SoundFont (*.sf2, *.sf3)")
        for pattern in ("*.sf2", "*.sf3", "*.SF2"):
            sf.add_pattern(pattern)
        filters = Gio.ListStore.new(Gtk.FileFilter)
        filters.append(sf)
        dialog.set_filters(filters)
        if self.sf_path:
            dialog.set_initial_file(Gio.File.new_for_path(self.sf_path))

        def done(dlg, result) -> None:
            try:
                f = dlg.open_finish(result)
            except Exception:  # noqa: BLE001 — annullato
                return
            if f is not None and f.get_path():
                self.sf_path = f.get_path()
                self._load_presets()

        dialog.open(self, None, done)

    def _sounds(self) -> dict[str, Sound]:
        """Suoni per uscita dopo il dialogo (solo quello dell'uscita in uso può cambiare)."""
        sounds = dict(self.track.sounds)
        if self.device is None:
            return sounds
        sounds.pop(self.device.name, None)
        sounds.pop(NAME_ALIASES.get(self.device.name, ""), None)  # suono sotto il vecchio nome: si riscrive col nuovo
        if not self.sound_on.get_active():
            return sounds
        if self.device.kind == INTERNAL:
            i = self.preset.get_selected()
            if not 0 <= i < len(self.presets):
                # 2026-11-20: nessun suono valido scelto (SoundFont mancante su questo PC,
                # banco/programma non presenti): il suono salvato resta com'era invece di
                # sparire salvando il dialogo per un'altra modifica (es. il nome).
                return dict(self.track.sounds)
            bank, program, _name = self.presets[i]
            sf = "" if self.sf_path == self.default_sf else self.sf_path  # "" = segue l'uscita
            sounds[self.device.name] = Sound(program, bank, 0, sf)
        else:
            for spin in (self.msb, self.lsb, self.pc):
                spin.update()
            sounds[self.device.name] = Sound(self.pc.get_value_as_int() - 1, self.msb.get_value_as_int(),
                                             self.lsb.get_value_as_int())
        return sounds

    def _program_chosen(self, action: Gio.SimpleAction, value: GLib.Variant) -> None:
        action.set_state(value)
        self.instrument.set_label(_program_label(value.get_int32()))
        self._channel_changed()


DRUM = -1  # valore della scelta "Drum" nel menu Strumento


def _program_label(program: int) -> str:
    return "Drum (percussion)" if program == DRUM else f"{program + 1:03d} {GM_PROGRAMS[program]}"
