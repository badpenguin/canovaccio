"""Finestra principale: menu, toolbar a palette, schede (Tracce + editor).

GTK 4 puro, senza libadwaita: barra del titolo e aspetto seguono il tema di sistema.
"""

from __future__ import annotations

from fractions import Fraction
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
from gi.repository import Gdk, Gio, GLib, Gtk  # noqa: E402

from .. import APP_NAME, __version__  # noqa: E402
from ..core.document import AddTrack, Compound, DeleteTrack, Document  # noqa: E402
from ..core.model import Track  # noqa: E402
from ..session import Session  # noqa: E402
from .label_dialog import LabelDialog  # noqa: E402
from .notation import NotationView  # noqa: E402
from .composer_dialogs import ChordDialog, ChordSymbolDialog, GuitarChordDialog  # noqa: E402
from .note_editing import NoteEditing  # noqa: E402
from .piano_roll import PianoRollView  # noqa: E402
from . import preferences  # noqa: E402
from .icons import themed  # noqa: E402
from .themes import SYSTEM, ThemeSwitcher, available_themes  # noqa: E402
from .toolbar import NOTE_VALUES, NoteValuesPalette, TOOLS, PlayerPalette, Toolbar, ToolsPalette, tool_action  # noqa: E402
from ..core.project import EXTENSION, STYLE_EXTENSION  # noqa: E402
from .song_dialog import SongDialog  # noqa: E402
from .tracks_view import key_label  # noqa: E402
from .chord_editor import ChordEditorDialog  # noqa: E402
from .event_editor import EventEditorView, EventEditorWindow  # noqa: E402
from .chord_track_dialog import ChordTrackDialog  # noqa: E402
from .input_dialog import InputDeviceDialog  # noqa: E402
from .recorder import Recorder  # noqa: E402
from ..output.midi_input import MidiInput, Thru  # noqa: E402
from .notes_dialog import NotesDialog  # noqa: E402
from .output_dialog import OutputDevicesDialog  # noqa: E402
from .loopback_dialog import LoopbackDialog  # noqa: E402
from ..output import loopback  # noqa: E402
from ..output.devices import OutputDevice, dump_devices, load_devices  # noqa: E402
from .quantize_dialog import QuantizeDialog  # noqa: E402
from .midi_clip_dialog import MidiClipDialog  # noqa: E402
from .velocity_dialog import VelocityDialog  # noqa: E402
from .transpose_dialog import TransposeDialog  # noqa: E402
from .track_dialog import TrackDialog  # noqa: E402
from .tracks_view import TracksView  # noqa: E402

EDITOR_TITLES = {"grid": "Grid", "notation": "Score"}
HINT = "Select (F2): click, Shift+click (add/remove), drag to select  ·  Enter/E/double-click: score  ·  G: grid  ·  Space: play/pause"


def build_segment_menu() -> Gio.Menu:
    """Menu Segment: strumenti (come la toolbar) e comandi sui segmenti selezionati.
    È lo stesso menu che si apre col tasto destro su un segmento."""
    menu = Gio.Menu()
    menu.append_section(None, _tools_section("seg-tool"))
    commands = Gio.Menu()
    commands.append("_Duplicate", "win.seg-duplicate")
    commands.append("_Merge", "win.seg-merge")
    commands.append("Make _Independent", "win.seg-unlink")
    label = Gio.MenuItem.new("_Label…", "win.seg-label")
    label.set_attribute_value("accel", GLib.Variant("s", "l"))  # mostra "L" (il tasto è gestito in on_key)
    commands.append_item(label)
    menu.append_section(None, commands)
    # 2026-11-20: un tratto di un file MIDI in un segmento (richiesta dell'utente)
    importing = Gio.Menu()
    importing.append("_Import MIDI…", "win.seg-import-midi")
    menu.append_section(None, importing)
    editors = Gio.Menu()
    for text, action, key in (("Edit in _Grid", "win.edit-grid", "g"), ("Edit in _Notation", "win.edit-notation", "e")):
        item = Gio.MenuItem.new(text, action)
        item.set_attribute_value("accel", GLib.Variant("s", key))  # tasti G / E (gestiti in on_key)
        editors.append_item(item)
    editors.append("_Event Editor", "win.edit-events")
    menu.append_section(None, editors)
    return menu


def build_track_menu() -> Gio.Menu:
    """Menu Track: è anche quello che si apre col tasto destro sul nome di una traccia.
    I comandi agiscono sulla traccia evidenziata (col tasto destro: quella cliccata)."""
    menu = Gio.Menu()
    menu.append("_Add Track", "win.track-add")
    menu.append("Du_plicate Track (no segments)", "win.track-duplicate-empty")
    menu.append("_Delete Track", "win.track-delete")
    menu.append("_Reorder", "win.track-reorder")
    selecting = Gio.Menu()  # 2026-11-20: tutti i segmenti della traccia (anche Ctrl+A nella scheda Tracce)
    selecting.append("_Select All Segments", "win.track-select-segments")
    menu.append_section(None, selecting)
    style = Gio.Menu()  # traccia accordi: genera l'accompagnamento di tutti i segmenti
    style.append("_Generate All (style)", "win.style-generate-all")
    menu.append_section(None, style)
    return menu


def _tools_section(action: str) -> Gio.Menu:
    """Strumenti (gli stessi della toolbar) su un'azione propria del menu, così il menu
    Segment e il menu Grid si possono disattivare separatamente."""
    tools = Gio.Menu()
    for name, label, accel, _enabled in TOOLS:
        item = Gio.MenuItem.new(label, None)
        item.set_action_and_target_value(f"win.{action}", GLib.Variant("s", name))
        item.set_attribute_value("accel", GLib.Variant("s", accel))
        tools.append_item(item)
    return tools


def build_grid_menu() -> Gio.Menu:
    """Menu Grid: gli strumenti (gli stessi della toolbar) per lavorare sulle note.
    Il tasto destro nella griglia lo mostra sempre."""
    menu = Gio.Menu()
    menu.append_section(None, _tools_section("grid-tool"))
    return menu


# menu Notes: comandi sulle note selezionate, divisi per funzione.
# (etichetta, azione, tasto mostrato; i tasti veri li gestisce la griglia, tranne Ctrl+D)
NOTES_SECTIONS = [
    [("_Duplicate", "notes-duplicate", "<Control>d"), ("_Merge", "notes-merge", None),
     ("De_lete", "notes-delete", "Delete")],
    [("_Transpose…", "notes-transpose", None), ("Semitone _Up", "notes-up", "Up"),
     ("Semitone Dow_n", "notes-down", "Down"), ("_Octave Up", "notes-octave-up", "<Control>Up"),
     ("Octave Do_wn", "notes-octave-down", "<Control>Down")],
    [("Move Le_ft", "notes-left", "Left"), ("Move _Right", "notes-right", "Right")],
    [("Dou_ble Length", "notes-double", "<Control>KP_Add"), ("_Halve Length", "notes-halve", "<Control>KP_Subtract"),
     ("_Add/Remove Dot", "notes-dot", "<Control>period"), ("T_ie / Untie", "notes-tie", None)],
    [("_Velocity…", "notes-velocity", None)],
    [("_Quantize to Snap", "notes-quantize-snap", None), ("Quanti_ze…", "notes-quantize", None)],
    [("_Staccato", "notes-staccato", None), ("S_lur", "notes-slur", None)],
    [("Detect Sta_ccato", "notes-detect-staccato", None)],
]


NOTES_SECTION_NAMES = ["_Edit", "Pi_tch", "T_ime", "_Length", "_Velocity", "_Quantize", "_Articulations", "De_tect"]  # sottomenu del tasto destro


def build_notes_submenus() -> Gio.Menu:
    """Comandi del menu Notes su due livelli (un sottomenu per funzione): per il tasto destro."""
    menu = Gio.Menu()
    for name, section in zip(NOTES_SECTION_NAMES, NOTES_SECTIONS):
        sub = Gio.Menu()
        for label, action, accel in section:
            item = Gio.MenuItem.new(label, f"win.{action}")
            if accel:
                item.set_attribute_value("accel", GLib.Variant("s", accel))
            sub.append_item(item)
        menu.append_submenu(name, sub)
    return menu


def build_notes_menu() -> Gio.Menu:
    menu = Gio.Menu()
    for section in NOTES_SECTIONS:
        part = Gio.Menu()
        for label, action, accel in section:
            item = Gio.MenuItem.new(label, f"win.{action}")
            if accel:
                item.set_attribute_value("accel", GLib.Variant("s", accel))
            part.append_item(item)
        menu.append_section(None, part)
    return menu


RECENT_MENU = Gio.Menu()  # File > Open Recent: riempito dalla finestra (preferenze)


def build_menubar() -> Gio.Menu:
    """Menu principale (voci in inglese). "_" = lettera per l'accesso da tastiera
    (Alt+lettera sulla barra, lettera dentro il menu aperto; F10 apre la barra)."""
    menu = Gio.Menu()
    file_menu = Gio.Menu()
    file_menu.append("_New", "win.new")
    file_menu.append("_Open…", "win.open")
    file_menu.append_submenu("Open _Recent", RECENT_MENU)
    import_section = Gio.Menu()
    import_section.append("Import _Chord Looper…", "win.import-chord-looper")
    file_menu.append_section(None, import_section)
    save_section = Gio.Menu()
    save_section.append("_Save", "win.save")
    save_section.append("Save _As…", "win.save-as")
    save_section.append("Save as _MIDI…", "win.save-midi")
    file_menu.append_section(None, save_section)
    song_section = Gio.Menu()
    song_section.append("Song _Properties…", "win.song-properties")
    song_section.append("Song _Notes…", "win.song-notes")
    file_menu.append_section(None, song_section)
    devices_section = Gio.Menu()
    devices_section.append("Output _Devices…", "win.output-devices")
    devices_section.append("_Input Device…", "win.input-device")
    devices_section.append("Audio _Loopback…", "win.audio-loopback")
    file_menu.append_section(None, devices_section)
    quit_section = Gio.Menu()
    quit_section.append("E_xit", "win.quit")
    file_menu.append_section(None, quit_section)
    menu.append_submenu("_File", file_menu)
    edit_menu = Gio.Menu()
    history = Gio.Menu()
    history.append("_Undo", "win.undo")
    history.append("_Redo", "win.redo")
    edit_menu.append_section(None, history)
    clipboard = Gio.Menu()  # sui segmenti selezionati; Paste al punto di play
    clipboard.append("Cu_t", "win.cut")
    clipboard.append("_Copy", "win.copy")
    clipboard.append("_Paste Duplicate", "win.paste")  # cloni (copie collegate)
    clipboard.append("Paste _Independent", "win.paste-independent")
    edit_menu.append_section(None, clipboard)
    selecting = Gio.Menu()
    selecting.append("Select _All", "win.select-all")  # tutte le note dell'editor attivo; in Tracce i segmenti della traccia
    edit_menu.append_section(None, selecting)
    menu.append_submenu("_Edit", edit_menu)
    view_menu = Gio.Menu()
    themes = available_themes()
    if len(themes) > 1:  # solo Adwaita (di solito Windows e Mac): niente scelta, niente menu
        theme_menu = Gio.Menu()
        system = Gio.Menu()
        system.append("System", f"win.theme::{SYSTEM}")
        theme_menu.append_section(None, system)
        installed = Gio.Menu()
        for name in themes:
            installed.append(name, f"win.theme::{name}")
        theme_menu.append_section(None, installed)
        view_menu.append_submenu("_Theme", theme_menu)
    track_menu = build_segment_menu()
    help_menu = Gio.Menu()
    help_menu.append("_About", "win.about")
    # ordine nella barra: File, Edit, Track, Segment, Grid, Score, Notes, Composer a sinistra; View, Help a destra
    menu.append_submenu("_Track", build_track_menu())
    menu.append_submenu("_Segment", track_menu)  # strumenti sui segmenti (stessi della toolbar)
    menu.append_submenu("_Grid", build_grid_menu())  # attivo solo con la griglia in primo piano
    menu.append_submenu("S_core", Gio.Menu())  # vuoto per ora; attivo solo con la notazione in primo piano
    menu.append_submenu("_Notes", build_notes_menu())  # comandi sulle note selezionate
    composer = Gio.Menu()  # accordi sulla linea rossa dell'editor attivo
    composer.append("_Chord…", "win.composer-chord")
    composer.append("_Guitar Chord…", "win.composer-guitar")
    composer.append("Chord _Symbol…", "win.composer-symbol")
    detect = Gio.Menu()  # sulle note selezionate (azione del menu Notes)
    detect.append("Detect C_hords", "win.notes-detect-chords")
    composer.append_section(None, detect)
    menu.append_submenu("C_omposer", composer)
    menu.append_submenu("_View", view_menu)
    menu.append_submenu("_Help", help_menu)
    return menu


LEFT_MENUS = 8  # File, Edit, Track, Segment, Grid, Score, Notes, Composer; gli altri vanno a destra
TRACK_MENU, SEGMENT_MENU, GRID_MENU, NOTATION_MENU, NOTES_MENU, COMPOSER_MENU = 2, 3, 4, 5, 6, 7  # posizioni nella barra


def _find(widget: Gtk.Widget, cls: type) -> Gtk.Widget | None:
    """Primo discendente di tipo `cls`."""
    child = widget.get_first_child()
    while child is not None:
        if isinstance(child, cls):
            return child
        found = _find(child, cls)
        if found is not None:
            return found
        child = child.get_next_sibling()
    return None


class MainWindow(Gtk.ApplicationWindow):
    def __init__(self, app: Gtk.Application, session: Session) -> None:
        super().__init__(application=app, default_width=1100, default_height=650, title=APP_NAME)
        self.set_show_menubar(True)  # il menu è quello dell'applicazione (build_menubar)
        self.maximize()  # all'avvio la finestra è massimizzata
        self.session = session
        self._message_timer = 0
        self._event_windows: list = []  # Event Editor aperti (finestre di dialogo)

        self.toolbar = Toolbar()
        self.player = PlayerPalette(session)
        self.player.loop_range = self.loop_range
        self.toolbar.add_palette(self.player)
        # uscita in uso: tendina accanto al player (le uscite si configurano in File > Output Devices…)
        self.devices, _current = load_devices(preferences.load())
        self._output_guard = False
        self.output_select = Gtk.DropDown(tooltip_text="Output: internal synth or MIDI port (File > Output Devices…)",
                                          margin_start=6, focus_on_click=False)
        self.output_select.connect("notify::selected", self._on_output_chosen)
        self.player.append(Gtk.Label(label="Output", margin_start=12))
        self.player.append(self.output_select)
        self._fill_outputs()
        self._select_output(session.device or _current)
        # ingresso MIDI (File > Input Device…): thru sulla traccia selezionata
        self.midi_in = MidiInput(lambda message, when: GLib.idle_add(self._thru_message, message, when))
        self.thru = Thru(session.transport.live, self._thru_track)
        # registrazione (Rec + conteggio, dopo i pulsanti del player)
        self.recorder = Recorder(session, self._record_track, lambda text, error=True: self.show_message(text, error=error))
        GLib.idle_add(lambda: self._open_input(self._saved_input(), quiet=True) or False)
        self.tools = ToolsPalette()
        self.toolbar.add_palette(self.tools)
        self.toolbar.add_palette(NoteValuesPalette())  # valori di nota (attivi con un editor)
        record_bar = Gtk.Box(spacing=2)  # registrazione: toolbar a parte, in fondo (dopo i valori di nota)
        record_bar.append(self.recorder.button)
        record_bar.append(self.recorder.count)
        self.toolbar.add_palette(record_bar)

        # schede: "Tracce" fissa + una per ogni editor aperto
        self.tracks = TracksView(session.transport, self.open_properties, self.open_notation, self.current_tool,
                                 lambda text, error=True: self.show_message(text, error=error),
                                 build_segment_menu(), build_track_menu())
        self.tracks.on_song_properties = self.open_song_properties
        self.notebook = Gtk.Notebook(scrollable=True, vexpand=True)
        self.notebook.append_page(self.tracks, Gtk.Label(label="Tracks"))
        self.notebook.connect("switch-page", lambda *_: GLib.idle_add(self._focus_current))
        self.notebook.connect("switch-page", lambda *_: GLib.idle_add(self._update_menus))
        self.notebook.connect("switch-page", lambda *_: GLib.idle_add(lambda: self.player.update_loop() or False))

        self.stack = Gtk.Stack(vexpand=True)
        empty = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12, valign=Gtk.Align.CENTER, halign=Gtk.Align.CENTER)
        empty.append(Gtk.Label(label="No song. Import a .mid file: it will be converted to the internal format."))
        empty_btn = Gtk.Button(label="Open a MIDI File…", halign=Gtk.Align.CENTER)
        empty_btn.add_css_class("suggested-action")
        empty_btn.connect("clicked", lambda *_: self.choose_file())
        empty.append(empty_btn)
        self.stack.add_named(empty, "empty")
        self.stack.add_named(self.notebook, "song")

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        box.append(self.toolbar)
        box.append(Gtk.Separator())
        box.append(self.stack)
        box.append(Gtk.Separator())
        box.append(self._build_status())
        self.set_child(box)

        self._install_actions(app)
        self._update_menus()
        keys = Gtk.EventControllerKey(propagation_phase=Gtk.PropagationPhase.CAPTURE)
        keys.connect("key-pressed", self.on_key)
        self.add_controller(keys)

        session.on_document(self.on_document)
        self.connect("map", lambda *_: GLib.idle_add(self._menus_right))
        self.connect("close-request", self.on_close)

        if session.backend.name == "null":
            msg = "Audio not available: playback is muted."
            if session.backend_errors:
                msg += " " + "; ".join(session.backend_errors)
            self.show_message(msg)

    # --- costruzione -----------------------------------------------------

    def _build_status(self) -> Gtk.Widget:
        bar = Gtk.Box(spacing=6, margin_start=12, margin_end=12, margin_top=4, margin_bottom=4)
        self.status = Gtk.Label(label=HINT, xalign=0, hexpand=True, ellipsize=3)
        self.output_info = Gtk.Label()
        self._update_output_info()
        for lbl in (self.status, self.output_info):
            bar.append(lbl)
        return bar

    def _install_actions(self, app: Gtk.Application) -> None:
        for name, cb, accels in (
            ("new", lambda *_: self.after_saving_changes(self.new_song), ["<Control>n"]),
            ("open", lambda *_: self.after_saving_changes(self.choose_file), ["<Control>o"]),
            ("save", lambda *_: self.save(), ["<Control>s"]),
            ("save-as", lambda *_: self.save_as(), ["<Control><Shift>s"]),
            ("save-midi", lambda *_: self.save_midi(), []),
            ("import-chord-looper", lambda *_: self.import_chord_looper(), []),
            ("style-generate-all", lambda *_: self.generate_style(None), []),
            ("select-all", lambda *_: self._select_all(), ["<Control>a"]),
            ("track-select-segments", lambda *_: self.session.document and self.tracks.select_track_segments(), []),
            ("undo", lambda *_: self.session.document and self.session.document.undo(), ["<Control>z"]),
            ("redo", lambda *_: self.session.document and self.session.document.redo(), ["<Control><Shift>z"]),
            ("cut", lambda *_: self._clipboard("cut_selection", self.tracks.cut_selection), ["<Control>x"]),
            ("copy", lambda *_: self._clipboard("copy_selection", self.tracks.copy_selection), ["<Control>c"]),
            ("paste", lambda *_: self._clipboard("paste", lambda: self.tracks.paste_at_play(linked=True)), ["<Control>v"]),
            ("paste-independent", lambda *_: self.session.document and self.tracks.paste_at_play(linked=False), ["<Control><Shift>v"]),
            ("seg-unlink", lambda *_: self.session.document and self.tracks.unlink_selection(), []),
            ("seg-duplicate", lambda *_: self.session.document and self.tracks.duplicate_selection(), ["<Control>d"]),
            ("seg-merge", lambda *_: self.session.document and self.tracks.merge_selection(), []),
            ("seg-label", lambda *_: self.label_selection(), []),
            ("seg-import-midi", lambda *_: self.import_midi_clip(), []),
            ("notes-duplicate", lambda *_: self._grid_do("duplicate_selection"), []),
            ("notes-merge", lambda *_: self._grid_do("merge_selection"), []),
            ("notes-delete", lambda *_: self._grid_do("delete_selection"), []),
            ("notes-up", lambda *_: self._grid_do("semitone", 1), []),
            ("notes-down", lambda *_: self._grid_do("semitone", -1), []),
            ("notes-octave-up", lambda *_: self._grid_do("octave", 1), []),
            ("notes-octave-down", lambda *_: self._grid_do("octave", -1), []),
            ("notes-left", lambda *_: self._grid_do("move_step", -1), []),
            ("notes-right", lambda *_: self._grid_do("move_step", 1), []),
            ("notes-double", lambda *_: self._grid_do("double_lengths"), []),
            ("notes-halve", lambda *_: self._grid_do("halve_lengths"), []),
            ("notes-dot", lambda *_: self._grid_do("toggle_dot"), []),
            ("notes-staccato", lambda *_: self._grid_do("toggle_staccato"), []),
            ("notes-tie", lambda *_: self._grid_do("toggle_tie"), []),
            ("notes-slur", lambda *_: self._grid_do("toggle_slur"), []),
            ("notes-velocity", lambda *_: self._velocity(), []),
            ("composer-chord", lambda *_: self._composer(ChordDialog), []),
            ("composer-guitar", lambda *_: self._composer(GuitarChordDialog), []),
            ("composer-symbol", lambda *_: self._composer(ChordSymbolDialog), []),
            ("notes-detect-staccato", lambda *_: self._grid_do("detect_staccato"), []),
            ("notes-detect-chords", lambda *_: self._grid_do("detect_chords"), []),
            ("notes-transpose", lambda *_: self._transpose(), []),
            ("notes-quantize-snap", lambda *_: self._grid_do("quantize_selection"), []),
            ("notes-quantize", lambda *_: self._quantize(), []),
            ("edit-grid", lambda *_: self.open_grid(), []),
            ("edit-notation", lambda *_: self.open_notation(), []),
            ("edit-events", lambda *_: self.open_events(), []),
            ("song-properties", lambda *_: self.open_song_properties(), []),
            ("song-notes", lambda *_: self.session.document and NotesDialog(self, self.session.document).present(), []),
            ("output-devices", lambda *_: self.open_output_devices(), []),
            ("input-device", lambda *_: InputDeviceDialog(self, self.midi_in.port or self._saved_input(),
                                                          self._set_input).present(), []),
            ("audio-loopback", lambda *_: LoopbackDialog(self).present(), []),
            ("track-add", lambda *_: self.session.document and self.tracks.add_track(), ["<Control>t"]),
            ("track-duplicate-empty", lambda *_: self.session.document and self.tracks.duplicate_track_empty(), []),
            ("track-delete", lambda *_: self.session.document and self.tracks.delete_track(), []),
            ("track-reorder", lambda *_: self.session.document and self.tracks.begin_reorder(), []),
            ("quit", lambda *_: self.close(), ["<Control>q"]),
            ("about", lambda *_: self.show_about(), []),
            ("close-tab", lambda *_: self._close_current_tab(), ["<Control>w"]),
            ("zoom-in", lambda *_: self._zoom(1), ["<Control>plus", "<Control>equal", "<Control>KP_Add"]),
            ("zoom-out", lambda *_: self._zoom(-1), ["<Control>minus", "<Control>KP_Subtract"]),
            ("zoom-reset", lambda *_: self._zoom(0), ["<Control>0", "<Control>KP_0"]),
        ):
            action = Gio.SimpleAction.new(name, None)
            action.connect("activate", cb)
            self.add_action(action)
            app.set_accels_for_action(f"win.{name}", accels)
        self.lookup_action("audio-loopback").set_enabled(loopback.available())  # pactl: solo Linux
        recent = Gio.SimpleAction.new("open-recent", GLib.VariantType.new("s"))
        recent.connect("activate", lambda _a, value: self.open_recent(value.get_string()))
        self.add_action(recent)
        self._fill_recent(preferences.recent_files())
        # tema GTK (menu View > Theme): salvato nelle preferenze e riapplicato all'avvio
        self._themes = ThemeSwitcher()
        saved = preferences.load().get("theme", SYSTEM)
        if saved and saved not in available_themes():
            saved = SYSTEM
        theme = Gio.SimpleAction.new_stateful("theme", GLib.VariantType.new("s"), GLib.Variant("s", saved))
        self._themes.apply(saved)

        def on_theme(action: Gio.SimpleAction, value: GLib.Variant) -> None:
            action.set_state(value)
            self._themes.apply(value.get_string())
            preferences.save(theme=value.get_string())

        theme.connect("change-state", on_theme)
        self.add_action(theme)
        # strumenti (menu Segment e toolbar): Select è attivo; gli altri per ora disattivati
        self._tool = Gio.SimpleAction.new_stateful("tool", GLib.VariantType.new("s"), GLib.Variant("s", "select"))
        def on_tool(action: Gio.SimpleAction, value: GLib.Variant) -> None:
            action.set_state(value)
            for proxy in self._tool_proxies:
                proxy.set_state(value)
            self.tracks.set_mode_cursor(value.get_string())
            for view in self.editor_views():  # griglia e notazione
                view.set_mode_cursor(value.get_string())

        self._tool.connect("change-state", on_tool)
        self.add_action(self._tool)
        # valori di nota (palette e tasti 1–8) e interruttore Terzina
        self._note_value = Gio.SimpleAction.new_stateful("note-value", GLib.VariantType.new("s"), GLib.Variant("s", "4"))
        self._note_value.connect("change-state", self._on_note_value)
        self.add_action(self._note_value)
        self._triplet = Gio.SimpleAction.new_stateful("triplet", None, GLib.Variant("b", False))
        self._triplet.connect("activate", lambda a, _p: a.set_state(GLib.Variant("b", not a.get_state().get_boolean())))
        self.add_action(self._triplet)
        # copie di "tool" per i menu Segment e Grid (si disattivano a seconda della scheda)
        self._tool_proxies = []
        for proxy_name in ("seg-tool", "grid-tool"):
            proxy = Gio.SimpleAction.new_stateful(proxy_name, GLib.VariantType.new("s"), GLib.Variant("s", "select"))
            proxy.connect("change-state", lambda _a, value: self._tool.change_state(value))
            self.add_action(proxy)
            self._tool_proxies.append(proxy)
        for name, _label, accel, enabled in TOOLS:
            if not enabled:  # Select e Move sono voci di "win.tool"
                dummy = Gio.SimpleAction.new(f"tool-{name}", None)
                dummy.set_enabled(False)
                self.add_action(dummy)
            app.set_accels_for_action(tool_action(name), [accel])

    # --- documento -------------------------------------------------------

    def choose_file(self) -> None:
        """File > Open…: un progetto .music, o da importare un file MIDI, uno stile, un progetto Rosegarden,
        una partitura MuseScore o MusicXML."""
        dialog = Gtk.FileDialog(title="Open Project or MIDI File")
        filters = Gio.ListStore.new(Gtk.FileFilter)
        styles = [f"*{e}" for e in (".sty", ".prs", ".sst", ".fps", ".bcs")]
        styles += [p.upper() for p in styles]
        # 2026-11-20: anche i progetti Rosegarden (.rg), importati come i MIDI
        # 2026-11-23: anche le partiture MuseScore (.mscz, .mscx) e MusicXML (.musicxml, .mxl, .xml)
        scores = ["*.mscz", "*.mscx", "*.musicxml", "*.mxl", "*.xml"]
        scores += [p.upper() for p in scores]
        for name, patterns in (("Projects, MIDI files, styles and scores",
                                ["*.music", "*.stile", "*.mid", "*.midi", "*.MID", "*.rg", "*.RG"] + styles + scores),
                               (f"{APP_NAME} projects", ["*.music"]), ("MIDI files", ["*.mid", "*.midi", "*.MID"]),
                               ("Styles (.stile and Yamaha)", ["*.stile"] + styles),
                               ("Rosegarden projects", ["*.rg", "*.RG"]),
                               ("MuseScore and MusicXML scores", scores)):
            flt = Gtk.FileFilter(name=name)
            for pattern in patterns:
                flt.add_pattern(pattern)
            filters.append(flt)
        dialog.set_filters(filters)
        self._start_in_last_dir(dialog, "open")
        dialog.open(self, None, self._on_file_chosen)

    def _start_in_last_dir(self, dialog: Gtk.FileDialog, kind: str) -> None:
        """Il dialogo riparte dall'ultima cartella usata per quel tipo di file (preferenze)."""
        folder = preferences.load().get("last_dirs", {}).get(kind)
        if folder and Path(folder).is_dir():
            dialog.set_initial_folder(Gio.File.new_for_path(folder))

    def _remember_dir(self, kind: str, path: str) -> None:
        dirs = dict(preferences.load().get("last_dirs", {}))
        dirs[kind] = str(Path(path).parent)
        preferences.save(last_dirs=dirs)

    def _on_file_chosen(self, dialog: Gtk.FileDialog, result) -> None:
        try:
            gfile = dialog.open_finish(result)
        except GLib.Error:
            return
        if gfile and gfile.get_path():
            self._remember_dir("open", gfile.get_path())
            self.open_path(gfile.get_path())

    def open_path(self, path: str) -> None:
        try:
            self.session.open_path(path)
        except Exception as exc:  # noqa: BLE001
            # senza testo (es. file troncato) almeno il tipo d'errore, non "Opening failed: "
            self.show_message(f"Opening failed: {str(exc) or type(exc).__name__}", error=True)
            return
        self._add_recent(path)
        warnings = self.session.document.song.import_warnings
        if warnings:  # es. kit percussioni Yamaha non GM
            self.show_message("Warning: " + "; ".join(warnings) + " (File › Song Notes)", seconds=15, error=True)

    # --- file recenti (File > Open Recent) ----------------------------------

    def open_recent(self, path: str) -> None:
        if not Path(path).is_file():
            self.show_message(f"{Path(path).name} no longer exists: removed from recent files.", error=True)
            self._fill_recent(preferences.remove_recent(path))
            return
        self.after_saving_changes(lambda: self.open_path(path))

    def _add_recent(self, path: str) -> None:
        """Aperti, salvati (.music) ed esportati (Save as MIDI) vanno in cima ai recenti."""
        self._fill_recent(preferences.add_recent(path))

    def _fill_recent(self, files: list[str]) -> None:
        RECENT_MENU.remove_all()
        home = str(Path.home())
        for path in files:
            p = Path(path)
            folder = str(p.parent)
            folder = "~" + folder[len(home):] if folder.startswith(home) else folder
            label = f"{p.name}   ({folder})".replace("_", "__")  # "_" nei nomi non è un acceleratore
            item = Gio.MenuItem.new(label, None)
            item.set_action_and_target_value("win.open-recent", GLib.Variant("s", path))
            RECENT_MENU.append_item(item)
        if not files:
            RECENT_MENU.append("(no recent files)", "win.no-recent")  # azione inesistente: voce spenta

    # --- salvataggio -------------------------------------------------------

    @staticmethod
    def _project_extension(doc) -> str:
        """.stile per uno stile (aperto da un .sty/.prs… o da un .stile), altrimenti .music."""
        from ..importer.style import is_style

        path = doc.project_path or doc.path or ""
        return STYLE_EXTENSION if is_style(path) or path.lower().endswith(STYLE_EXTENSION) else EXTENSION

    def save(self, then=None) -> None:
        """File > Save: sul progetto aperto; per un brano importato da MIDI, accanto al MIDI
        con lo stesso nome e l'estensione .music; per uno stile Yamaha (.sty…) un .stile
        accanto allo stile (chiede solo se quel file esiste già)."""
        doc = self.session.document
        if doc is None:
            return
        if doc.project_path:
            self._write(doc.project_path, then)
            return
        if not doc.path:  # brano nuovo (File > New): si sceglie dove salvarlo
            self.save_as(then)
            return
        source = Path(doc.path)
        target = source.with_suffix(self._project_extension(doc))
        if target.exists():
            self._ask(f"{target.name} already exists", "Overwrite it with this song?", ["Cancel", "Overwrite"],
                      lambda choice: choice == 1 and self._write(str(target), then))
        else:
            self._write(str(target), then)

    def save_as(self, then=None) -> None:
        """File > Save As…: sceglie dove salvare il progetto .music (o .stile per uno stile)."""
        doc = self.session.document
        if doc is None:
            return
        dialog = Gtk.FileDialog(title="Save Project")
        current = Path(doc.project_path or doc.path or doc.song.title)
        ext = self._project_extension(doc)
        dialog.set_initial_name(current.with_suffix(ext).name)
        if doc.project_path or doc.path:
            if current.parent.exists():
                dialog.set_initial_folder(Gio.File.new_for_path(str(current.parent)))
        else:  # brano nuovo: dall'ultima cartella usata per aprire
            self._start_in_last_dir(dialog, "open")

        def done(d, result):
            try:
                gfile = d.save_finish(result)
            except GLib.Error:
                return
            path = Path(gfile.get_path())
            self._write(str(path if path.suffix in (EXTENSION, STYLE_EXTENSION) else path.with_name(path.name + ext)),
                        then)

        dialog.save(self, None, done)

    def save_midi(self) -> None:
        """File > Save as MIDI…: esporta come si sente (staccato a metà, legature unite)."""
        doc = self.session.document
        if doc is None:
            return
        dialog = Gtk.FileDialog(title="Export MIDI File")
        current = Path(doc.project_path or doc.path or doc.song.title)
        dialog.set_initial_name(f"{current.stem}-export.mid")  # mai il nome del MIDI originale
        if current.parent.exists():
            dialog.set_initial_folder(Gio.File.new_for_path(str(current.parent)))

        def done(d, result):
            try:
                gfile = d.save_finish(result)
            except GLib.Error:
                return
            try:
                self.session.export_midi(gfile.get_path())
                self.show_message(f"Exported {Path(gfile.get_path()).name}.", error=False)
                self._add_recent(gfile.get_path())
            except Exception as exc:  # noqa: BLE001
                self.show_message(f"Export failed: {exc}", error=True)

        dialog.save(self, None, done)

    def _write(self, path: str, then=None) -> bool:
        try:
            self.session.save(path)
        except Exception as exc:  # noqa: BLE001
            self.show_message(f"Saving failed: {exc}", error=True)
            return False
        self.show_message(f"Saved {Path(path).name}.", error=False)
        self._add_recent(path)
        if then is not None:
            then()
        return True

    def _ask(self, message: str, detail: str, buttons: list[str], callback) -> None:
        """Domanda con pulsanti; `callback(indice)` (il primo pulsante è Annulla / Esc)."""
        dialog = Gtk.AlertDialog(message=message, detail=detail, buttons=buttons, cancel_button=0,
                                 default_button=len(buttons) - 1, modal=True)

        def done(d, result):
            try:
                callback(d.choose_finish(result))
            except GLib.Error:
                callback(0)

        dialog.choose(self, None, done)

    def after_saving_changes(self, then) -> None:
        """Prima di aprire un altro file o chiudere: se ci sono modifiche non salvate,
        Salva / Non salvare / Annulla."""
        doc = self.session.document
        if doc is None or not doc.modified:
            then()
            return

        def chosen(choice: int) -> None:
            if choice == 1:
                then()
            elif choice == 2:
                self.save(then)

        self._ask("There are unsaved changes", "Save them before continuing?",
                  ["Cancel", "Don't Save", "Save"], chosen)

    def _update_title(self, doc: Document | None = None) -> None:
        doc = doc or self.session.document
        if doc is None:
            return
        name = Path(doc.project_path or doc.path).name if (doc.project_path or doc.path) else doc.song.title
        # 2026-11-23: il copyright del brano nel titolo, così aprendo un file (es. uno stile
        # derivato da quelli Yamaha) si vede subito a chi appartiene
        copyright = f" — {doc.song.copyright}" if doc.song.copyright else ""
        self.set_title(f"{'*' if doc.modified else ''}{name}{copyright} — {APP_NAME}")

    def on_document(self, doc: Document) -> None:
        for view in self.editor_views():
            self.close_editor(view)
        for window in list(self._event_windows):  # Event Editor del brano di prima
            window.close()
        doc.subscribe(self._update_title)  # * nel titolo con modifiche non salvate
        doc.subscribe(self._close_deleted_editors)
        self._update_title(doc)
        self.tracks.set_document(doc)
        self.stack.set_visible_child_name("song")
        self.notebook.set_current_page(0)
        self.player.set_enabled(True)
        self.player.fit_position(doc.song)
        self._update_menus()  # Track e Segment si accendono col primo brano aperto

    # --- uscite ------------------------------------------------------------

    def _update_output_info(self) -> None:
        backend = self.session.backend
        self.output_info.set_label("Output: " + ("none (muted)" if backend.name == "null" else backend.describe()))

    def _fill_outputs(self) -> None:
        self._output_guard = True
        self.output_select.set_model(Gtk.StringList.new([d.name for d in self.devices]))
        self._output_guard = False

    def _select_output(self, device: OutputDevice | None) -> None:
        ids = [d.id for d in self.devices]
        if device is not None and device.id in ids:
            self._output_guard = True
            self.output_select.set_selected(ids.index(device.id))
            self._output_guard = False

    def _on_output_chosen(self, *_a) -> None:
        i = self.output_select.get_selected()
        if not self._output_guard and 0 <= i < len(self.devices):
            self._switch_output(self.devices[i])

    def _switch_output(self, device: OutputDevice) -> None:
        """Passa all'uscita scelta; se non si apre resta quella di prima (e lo si dice)."""
        try:
            self.session.set_output(device)
        except Exception as exc:  # noqa: BLE001 — messaggio all'utente
            self.show_message(f"Output “{device.name}” not available: {exc}", error=True)
            self._select_output(self.session.device)
        else:
            preferences.save(**dump_devices(self.devices, device))
        self._update_output_info()

    def open_output_devices(self) -> None:
        """File > Output Devices…: si lavora su una copia; con "Salva" la si salva e, se
        l'uscita in uso è cambiata (o è stata tolta), la si riapre. "Chiudi" scarta."""
        before = {d.id: d.to_dict() for d in self.devices}
        work = [OutputDevice.from_dict(d.to_dict()) for d in self.devices]
        OutputDevicesDialog(self, work, lambda devices: self._devices_changed(devices, before)).present()

    def _devices_changed(self, devices: list[OutputDevice], before: dict) -> None:
        self.devices = devices
        used = self.session.device
        current = next((d for d in devices if used is not None and d.id == used.id), None)
        changed = current is None or current.to_dict() != before.get(current.id)
        current = current or devices[0]
        self._fill_outputs()
        self._select_output(current)
        if changed:
            self._switch_output(current)
        else:
            preferences.save(**dump_devices(devices, current))

    # --- parametri traccia ----------------------------------------------

    def new_song(self) -> None:
        """File > New: brano vuoto, e subito Song Properties (tempo, scala, metro)."""
        doc = self.session.new_document()
        GLib.idle_add(lambda: SongDialog(self, doc, new_song=True).present() or False)

    def open_song_properties(self) -> None:
        """File > Song Properties… (anche doppio click sui dati del brano in basso)."""
        if self.session.document:
            SongDialog(self, self.session.document).present()

    def open_properties(self, track: Track) -> None:
        if track.kind == "chords":  # stili del brano e canale della Preview
            if self.session.document:
                ChordTrackDialog(self, self.session.document, track,
                                 lambda dialog: self._start_in_last_dir(dialog, "styles"),
                                 lambda path: self._remember_dir("styles", path)).present()
            return
        if self.session.document:
            TrackDialog(self, self.session.document, track, self.session.device).present()

    # --- editor nelle schede ---------------------------------------------

    def editor_views(self) -> list[Gtk.Widget]:
        pages = (self.notebook.get_nth_page(i) for i in range(self.notebook.get_n_pages()))
        return [p for p in pages if isinstance(p, (PianoRollView, NotationView))]

    def _chord_editor(self, selection: list) -> bool:
        """Segmenti della traccia accordi: si aprono nel Chord Editor (uno alla volta).
        True se la selezione era della traccia accordi (gestita qui)."""
        chords = [(t, seg) for t, seg in selection if t.kind == "chords"]
        if not chords:
            return False
        if len(selection) > 1:
            self.show_message("The Chord Editor opens one segment of the chord track at a time.", error=True)
        else:
            ChordEditorDialog(self, self.session.document, chords[0][0], chords[0][1],
                              on_generate=self.generate_style).present()
        return True

    def generate_style(self, segments: list | None = None) -> None:
        """Genera l'accompagnamento dello stile per i segmenti della traccia accordi indicati
        (None = tutti) nelle tracce "Stile · …": un passo di Ctrl+Z."""
        doc = self.session.document
        chord_track = doc.song.chord_track if doc else None
        if chord_track is None:
            self.show_message("The song has no chord track (File › Import Chord Looper…).", error=True)
            return
        from ..core.arrange import generation_command
        from ..importer.style import load_style_data

        segments = list(chord_track.segments) if segments is None else segments
        try:
            command, warnings = generation_command(doc.song, segments, load_style_data)
        except Exception as exc:  # noqa: BLE001
            self.show_message(f"Generation failed: {exc}", error=True)
            return
        if command is not None:
            doc.execute(command)
        done = len(segments) - len(warnings)
        text = f"Generated {done} segments." if command is not None else "Nothing to generate."
        if warnings:
            text += " " + "; ".join(warnings)
        self.show_message(text, error=bool(warnings) or command is None)

    # --- ingresso MIDI ---------------------------------------------------------

    @staticmethod
    def _saved_input() -> str:
        return preferences.load().get("midi_input", "")

    def _set_input(self, port: str) -> None:
        """Input Device › Salva: la porta scelta (o nessuna) si apre e resta nelle preferenze."""
        preferences.save(midi_input=port)
        self._open_input(port)

    def _open_input(self, port: str, quiet: bool = False) -> None:
        self.midi_in.close()
        if not port:
            return
        try:
            self.midi_in.open(port)
        except Exception as exc:  # noqa: BLE001 — porta spenta o scollegata
            self.show_message(f"MIDI input not available: {exc}", error=True)
            return
        if not quiet:
            self.show_message(f"MIDI input: {port}.", error=False)

    def _thru_message(self, message: list[int], when: float) -> bool:
        self.recorder.capture(message, when)
        self.thru.handle(message)
        return False

    def _record_track(self):
        """Traccia su cui registrare: quella del thru; nella scheda Tracce con la riga della
        traccia accordi evidenziata, la traccia accordi (2026-11-20: le note diventano accordi)."""
        doc = self.session.document
        if doc is None:
            return None
        selected = self.tracks.selected_track()
        if self._current_editor() is None and selected is not None and selected.kind == "chords":
            return selected
        track_id = self._thru_track()
        return next((t for t in doc.song.tracks if t.id == track_id), None) if track_id else None

    def _thru_track(self) -> str | None:
        """Traccia che suona quello che arriva dall'ingresso: quella dell'editor aperto, o la
        riga evidenziata nella scheda Tracce, o la prima traccia (mai la traccia accordi, tranne
        mentre si registrano accordi su di lei)."""
        doc = self.session.document
        if doc is None:
            return None
        recording = getattr(self, "recorder", None)
        if recording is not None and recording.state != "idle" and recording.track is not None \
                and recording.track.kind == "chords" and recording.track in doc.song.tracks:
            # 2026-11-20: registrando accordi si sente la traccia accordi (suono della Preview)
            return recording.track.id
        editor = self._current_editor()
        candidates = list(getattr(editor, "edit_tracks", [])) if editor is not None else []
        selected = self.tracks.selected_track()
        candidates += [selected] if selected is not None else []
        candidates += doc.song.tracks
        track = next((t for t in candidates if t.kind != "chords"), None)
        return track.id if track is not None else None

    def import_chord_looper(self) -> None:
        """File > Import Chord Looper…: un banco .clb diventa la traccia accordi del brano
        aperto (in cima); se c'è già, la sostituisce dopo conferma."""
        doc = self.session.document
        if doc is None:
            self.show_message("Open a song first: Chord Looper banks are imported into the open song.", error=True)
            return
        dialog = Gtk.FileDialog(title="Import Chord Looper")
        flt = Gtk.FileFilter(name="Chord Looper (*.clb)")
        flt.add_pattern("*.clb")
        flt.add_pattern("*.CLB")
        filters = Gio.ListStore.new(Gtk.FileFilter)
        filters.append(flt)
        dialog.set_filters(filters)
        self._start_in_last_dir(dialog, "chord_looper")

        def chosen(d, result) -> None:
            try:
                gfile = d.open_finish(result)
            except GLib.Error:
                return
            from ..core.document import SetInitialKey
            from ..importer.chord_looper import chord_track_from_clb, guess_key

            self._remember_dir("chord_looper", gfile.get_path())
            try:
                track, groups = chord_track_from_clb(doc.song, gfile.get_path())
            except Exception as exc:  # noqa: BLE001
                self.show_message(f"Import failed: {exc}", error=True)
                return
            old = doc.song.chord_track
            commands = ([DeleteTrack(old)] if old is not None else []) + [AddTrack(track, 0, groups)]
            # brano senza note: la tonalità iniziale si ricava dagli accordi
            empty = not any(g.notes for g in doc.song.groups.values())
            key = guess_key(doc.song, track, groups) if empty else None
            if key is not None and key != doc.song.keys.initial:
                commands.append(SetInitialKey(key))
            text = f"Imported {len(track.segments)} chord progressions into the Chords track."
            if key is not None:
                text += f" Song key: {key_label(key)} (detected from the chords; change it in Song Properties)."

            def go() -> None:
                doc.execute(Compound(commands, "Import Chord Looper"))
                self.show_message(text, error=False)

            if old is None:
                go()
                return
            self._ask("Replace the chord track?", "The song already has a chord track: it will be replaced.",
                      ["Cancel", "Replace"], lambda choice: choice == 1 and go())

        dialog.open(self, None, chosen)

    def _selection_or_message(self) -> list | None:
        """Gli editor lavorano solo sui segmenti selezionati (mai su una traccia intera:
        altrimenti si potrebbero creare note fuori da ogni segmento)."""
        selection = self.tracks.selection
        if not selection:
            self.show_message("Select one or more segments in the Tracks tab.", error=True)
            return None
        return selection

    def open_grid(self) -> None:
        """Griglia dei segmenti selezionati: una sola traccia, dalla prima all'ultima misura selezionata."""
        doc = self.session.document
        selection = self._selection_or_message() if doc else None
        if not selection or self._chord_editor(selection):
            return
        selection = [self._original(t, seg) for t, seg in selection]  # un clone si apre sempre sull'originale
        tracks = {id(t): t for t, _s in selection}
        if len(tracks) > 1:
            self.show_message("The grid shows a single track: select segments of one track only.", error=True)
            return
        track = next(iter(tracks.values()))
        bars = [doc.song.segment_bars(seg) for _t, seg in selection]
        first = min(a for a, _b in bars)
        last = max(b for _a, b in bars)
        view = PianoRollView(doc, track, self.session.transport, first, last, get_mode=self.current_tool,
                             on_message=lambda text, error=True: self.show_message(text, error=error),
                             menu=build_grid_menu(), notes_menu=build_notes_submenus(),
                             on_selection=self._update_notes_actions)
        self._add_tab(view, f"{track.name} · Grid ({first + 1}–{last})")

    def open_events(self) -> None:
        """Segment > Event Editor: note ed eventi (controller, pitch bend, aftertouch) dei
        segmenti selezionati, una traccia sola, come lista."""
        doc = self.session.document
        selection = self._selection_or_message() if doc else None
        if not selection or self._chord_editor(selection):
            return
        selection = [self._original(t, seg) for t, seg in selection]
        tracks = {id(t): t for t, _s in selection}
        if len(tracks) > 1:
            self.show_message("The Event Editor shows a single track: select segments of one track only.",
                              error=True)
            return
        track = next(iter(tracks.values()))
        segments = sorted({id(s): s for _t, s in selection}.values(), key=lambda s: doc.song.segment_span(s)[0])
        view = EventEditorView(doc, track, segments, self.session.transport,
                               lambda text, error=True: self.show_message(text, error=error))
        first, last = view.first_bar, max(doc.song.segment_bars(s)[1] for s in segments)
        window = EventEditorWindow(self, view, f"Events — {track.name} (bars {first + 1}–{last})")
        self._event_windows.append(window)
        window.connect("close-request", lambda w: self._event_windows.remove(w) or False)
        window.present()

    def _original(self, track, seg):
        """(traccia, segmento) dell'originale se `seg` è un clone, altrimenti com'è."""
        song = self.session.document.song
        original = song.segment_by_id(seg.clone_of) if seg.clone_of else None
        if original is None:
            return track, seg
        owner = next((t for t in song.tracks if original in t.segments), track)
        return owner, original

    def open_notation(self) -> None:
        """Notazione dei segmenti selezionati (più tracce se coprono le stesse misure)."""
        doc = self.session.document
        selection = self._selection_or_message() if doc else None
        if not selection or self._chord_editor(selection):
            return
        chosen = {id(t) for t, _seg in selection}
        if len(chosen) == 1:
            # 2026-11-20: una traccia sola (es. dopo Ctrl+A / Select All Segments): dalla prima
            # all'ultima misura selezionata, come la griglia
            bars = [doc.song.segment_bars(seg) for _t, seg in selection]
            first_bar, last_bar = min(a for a, _b in bars), max(b for _a, b in bars)
        else:
            spans = {doc.song.segment_span(seg) for _t, seg in selection}
            if len(spans) > 1:
                self._alert(
                    "Select the Same Bars",
                    "The selected segments cover different bars. To see them together in the score, "
                    "select segments that start and end at the same bars.",
                )
                return
            first_bar, last_bar = doc.song.segment_bars(selection[0][1])
        tracks = [t for t in doc.song.tracks if id(t) in chosen]  # righi nell'ordine delle tracce
        if any(t.instrument.is_percussion or t.channel == 10 for t in tracks):
            self.open_grid()  # la batteria non ha la notazione: si apre la griglia
            return
        view = NotationView(doc, tracks, self.session.transport, first_bar, last_bar, get_mode=self.current_tool,
                            get_length=self.note_length,
                            on_message=lambda text, error=True: self.show_message(text, error=error),
                            menu=build_grid_menu(), notes_menu=build_notes_submenus(),
                            on_selection=self._update_notes_actions)
        names = " + ".join(t.name for t in tracks) if len(tracks) <= 2 else f"{len(tracks)} tracks"
        self._add_tab(view, f"{names} · Score ({first_bar + 1}–{last_bar})")

    def _add_tab(self, view: Gtk.Widget, title: str) -> None:
        """Aggiunge la scheda, o porta in primo piano quella uguale già aperta. In entrambi i
        casi il punto di play va all'inizio delle misure dell'editor."""
        song = self.session.document.song
        self.session.transport.seek(song.tempos.seconds(song.meters.bar_start(view.first_bar)))
        for existing in self.editor_views():
            if existing.key == view.key:
                view.shutdown()
                self.notebook.set_current_page(self.notebook.page_num(existing))
                existing.scroll_to_playhead()
                return
        tab = Gtk.Box(spacing=4)
        tab.append(Gtk.Label(label=title))
        close = Gtk.Button(icon_name=themed("window-close"), tooltip_text="Close")
        close.add_css_class("flat")
        close.add_css_class("circular")
        close.set_focus_on_click(False)
        close.connect("clicked", lambda *_: self.close_editor(view))
        tab.append(close)
        index = self.notebook.append_page(view, tab)
        self.notebook.set_tab_reorderable(view, True)
        self.notebook.set_current_page(index)

    def _alert(self, message: str, detail: str) -> None:
        dialog = Gtk.AlertDialog(message=message, detail=detail, modal=True)
        dialog.set_buttons(["OK"])
        dialog.show(self)

    def _close_deleted_editors(self, doc: Document) -> None:
        """Chiude griglie, notazioni ed Event Editor di una traccia appena cancellata.
        2026-11-20: prima restavano aperti sui gruppi tolti dal brano e andavano in errore
        a ogni modifica e ridisegno (un Undo di Delete Track non li riapre)."""
        alive = {id(t) for t in doc.song.tracks}
        for view in self.editor_views():
            if any(id(t) not in alive for t in view.edit_tracks):
                self.close_editor(view)
        for window in list(self._event_windows):
            if id(window.view.track) not in alive:
                window.close()

    def close_editor(self, view: Gtk.Widget) -> None:
        view.shutdown()
        self.notebook.remove_page(self.notebook.page_num(view))

    def _menubar_items(self) -> list[Gtk.Widget]:
        """Titoli della barra dei menu (File, Edit, Track, Segment, Grid, View, Help)."""
        bar = _find(self, Gtk.PopoverMenuBar)
        items, child = [], bar.get_first_child() if bar else None
        while child is not None:
            items.append(child)
            child = child.get_next_sibling()
        return items

    def _menus_right(self) -> bool:
        """Sposta View e Help sul lato destro della barra dei menu: la voce View si
        allarga e si allinea a destra (resta un'unica barra, quindi F10 e le frecce
        passano da un menu all'altro come prima)."""
        items = self._menubar_items()
        self._update_menus()  # titoli Track / Segment / Grid attivi o grigi
        if len(items) > LEFT_MENUS:
            items[LEFT_MENUS].set_hexpand(True)
            items[LEFT_MENUS].set_halign(Gtk.Align.END)
        return False

    def label_selection(self) -> None:
        if not self.session.document:
            return
        if not self.tracks.selection:
            self.show_message("No segment selected.", error=True)
            return
        LabelDialog(self, self.session.document, [s for _t, s in self.tracks.selection]).present()

    def import_midi_clip(self) -> None:
        """Segment > Import MIDI…: un tratto di un file MIDI diventa un segmento. Destinazione
        proposta: la traccia evidenziata e la misura del punto di play."""
        doc = self.session.document
        if doc is None:
            return
        song = doc.song
        if not any(t.kind != "chords" for t in song.tracks):
            self.show_message("Add a track first: MIDI is imported into an existing track.", error=True)
            return
        pos = song.tempos.position(self.session.transport.position)
        bar = song.meters.bar_at(pos) if pos > 0 else 0

        def imported(track, seg) -> None:
            """Segmento importato: selezionato e mostrato."""
            self.tracks.selection = [(track, seg)]
            self.tracks.redraw_all()
            self.show_message(f"Imported “{seg.label}” into “{track.name}” at bar {seg.start_bar + 1}.")

        MidiClipDialog(self, self.session, self.tracks.selected_track(), bar, imported).present()

    def show_about(self) -> None:
        """Help > About: informazioni sul programma."""
        dialog = Gtk.AboutDialog(
            transient_for=self,
            modal=True,
            program_name=APP_NAME,
            version=__version__,
            comments="Native Linux music editor: tracks and segments, grid (piano roll) and "
            "score, kept in sync on a single model. MIDI files are imported and exported.",
            logo_icon_name="io.github.todomusician.Editor",
        )
        dialog.add_credit_section(
            "Components",
            [
                "GTK 4 and PyGObject — user interface",
                "Cairo — grid and score drawing",
                "Bravura (Steinberg, SIL Open Font License 1.1) — SMuFL music symbols",
                "mido — MIDI file reading",
                "FluidSynth / pyfluidsynth — playback",
            ],
        )
        dialog.present()

    def loop_range(self) -> tuple[float, float] | None:
        """Tratto del Loop (secondi): le misure mostrate in griglia o notazione; nella scheda
        Tracce i segmenti selezionati (dal primo inizio all'ultima fine), senza selezione
        tutto il brano."""
        doc = self.session.document
        if doc is None:
            return None
        song = doc.song
        view = self._current_editor()
        if view is not None:
            first = view.first_bar
            last = song.bar_count if view.last_bar is None else view.last_bar
            a, b = song.meters.bar_start(first), song.meters.bar_start(last)
        elif self.tracks.selection:
            spans = [song.segment_span(seg) for _t, seg in self.tracks.selection]
            a, b = min(x for x, _y in spans), max(y for _x, y in spans)
        else:
            a, b = Fraction(0), song.end_position
        return song.tempos.seconds(a), song.tempos.seconds(b)

    def note_length(self) -> Fraction:
        """Durata del valore scelto nella palette (con Terzina: 2/3)."""
        key = self._note_value.get_state().get_string()
        value = next(v for k, _n, v, _g in NOTE_VALUES if k == key)
        return value * Fraction(2, 3) if self._triplet.get_state().get_boolean() else value

    def _on_note_value(self, action: Gio.SimpleAction, value: GLib.Variant) -> None:
        """Valore scelto (click o tasto 1–8): diventa quello delle note nuove e, se ci sono
        note selezionate nell'editor in primo piano, la loro durata; senza note selezionate
        attiva lo strumento Create."""
        action.set_state(value)
        view = self._current_editor()
        if isinstance(view, NotationView) and view.selection:
            view.set_lengths(self.note_length())
        elif isinstance(view, NotationView):  # nessuna nota selezionata: si passa a Create
            self._tool.change_state(GLib.Variant("s", "create"))

    def current_tool(self) -> str:
        return self._tool.get_state().get_string()

    def _current_editor(self) -> Gtk.Widget | None:
        page = self.notebook.get_nth_page(self.notebook.get_current_page())
        return page if isinstance(page, (PianoRollView, NotationView)) else None

    def _close_current_tab(self) -> None:
        view = self._current_editor()
        if view is not None:
            self.close_editor(view)

    def _zoom(self, direction: int) -> None:
        view = self._current_editor()
        if view is not None:
            view.zoom_step(direction)
        elif self.session.document is not None:
            self.tracks.zoom_step(direction)

    def _clipboard(self, grid_method: str, tracks_action) -> None:
        """Cut / Copy / Paste: sulle note con un editor in primo piano, sui segmenti altrimenti."""
        if not self.session.document:
            return
        view = self._current_editor()
        if isinstance(view, NoteEditing):
            getattr(view, grid_method)()
        elif view is None:
            tracks_action()

    def _composer(self, dialog) -> None:
        """Composer: dialogo dell'accordo per l'editor in primo piano."""
        view = self._current_editor()
        if isinstance(view, NoteEditing):
            dialog(self, view).present()

    def _velocity(self) -> None:
        view = self._current_editor()
        if isinstance(view, NoteEditing) and view.selection:
            VelocityDialog(self, view).present()

    def _quantize(self) -> None:
        view = self._current_editor()
        if isinstance(view, NoteEditing) and view.selection:
            QuantizeDialog(self, view).present()

    def _transpose(self) -> None:
        view = self._current_editor()
        if not isinstance(view, NoteEditing):
            return
        if not view.selection:
            self.show_message("No note selected.", error=True)
            return
        TransposeDialog(self, view).present()

    def _select_all(self) -> None:
        """Edit > Select All (Ctrl+A): in un editor tutte le note; nella scheda Tracce
        (2026-11-20) tutti i segmenti della traccia evidenziata."""
        if self._current_editor() is not None:
            self._grid_do("select_all")
        elif self.session.document:
            self.tracks.select_track_segments()

    def _grid_do(self, method: str, *args) -> None:
        """Comando sulle note dell'editor in primo piano (griglia o notazione)."""
        view = self._current_editor()
        if isinstance(view, NoteEditing):
            getattr(view, method)(*args)

    SEGMENT_ACTIONS = ("track-add", "track-duplicate-empty", "track-delete", "track-reorder", "track-select-segments", "seg-tool", "seg-duplicate", "seg-merge",
                       "seg-unlink", "seg-label", "seg-import-midi", "edit-grid", "edit-notation", "edit-events", "paste-independent")
    EDITOR_ACTIONS = ("grid-tool",)
    # 2026-11-20: Composer spento se l'editor ha una traccia di batteria (come Detect Chords)
    COMPOSER_ACTIONS = ("composer-chord", "composer-guitar", "composer-symbol")
    SCORE_ACTIONS = ("note-value", "triplet")  # palette dei valori: solo con la notazione
    # menu Notes: solo sulle note selezionate di un editor (mai su segmenti o tracce)
    NOTES_ACTIONS = tuple(action for section in NOTES_SECTIONS for _label, action, _accel in section)

    def _update_menus(self) -> bool:
        """Menu attivi secondo la scheda in primo piano: Tracce → Track e Segment; griglia →
        Grid e Notes; notazione → Score e Notes. Ctrl+D duplica le note nella griglia,
        i segmenti nella scheda Tracce. Si tocca solo ciò che cambia: rifare le scorciatoie
        mentre un menu è aperto (es. tasto destro) lo chiuderebbe."""
        editor = self._current_editor()
        grid = isinstance(editor, PianoRollView)
        # 2026-11-20: senza un brano aperto Track e Segment restano grigi (prima erano
        # attivi ma le voci non facevano nulla)
        tracks_tab = editor is None and self.session.document is not None
        for name in self.SEGMENT_ACTIONS:
            self._enable(name, tracks_tab)
        for name in self.EDITOR_ACTIONS:
            self._enable(name, editor is not None)
        composer = editor is not None and not editor.is_drums
        for name in self.COMPOSER_ACTIONS:
            self._enable(name, composer)
        for name in self.SCORE_ACTIONS:
            self._enable(name, isinstance(editor, NotationView))
        self._update_notes_actions()
        items = self._menubar_items()  # anche i titoli: grigi e non apribili (mouse e Alt+lettera)
        if len(items) > COMPOSER_MENU:
            for i, on in ((TRACK_MENU, tracks_tab), (SEGMENT_MENU, tracks_tab), (GRID_MENU, grid),
                          (NOTATION_MENU, isinstance(editor, NotationView)), (NOTES_MENU, editor is not None),
                          (COMPOSER_MENU, composer)):
                if items[i].get_sensitive() != on:
                    items[i].set_sensitive(on)
        on_notes = editor is not None
        if getattr(self, "_accels_notes", None) != on_notes:
            self._accels_notes = on_notes
            app = self.get_application()
            app.set_accels_for_action("win.seg-duplicate", [] if on_notes else ["<Control>d"])
            app.set_accels_for_action("win.notes-duplicate", ["<Control>d"] if on_notes else [])
            # Ctrl + / Ctrl − del tastierino: negli editor cambiano la durata delle note, non lo zoom
            app.set_accels_for_action("win.zoom-in", ["<Control>plus", "<Control>equal"] + ([] if on_notes else ["<Control>KP_Add"]))
            app.set_accels_for_action("win.zoom-out", ["<Control>minus"] + ([] if on_notes else ["<Control>KP_Subtract"]))
        return False

    STACCATO_ACTIONS = ("notes-staccato", "notes-detect-staccato", "notes-slur", "notes-detect-chords")  # non sulla batteria
    # 2026-11-20: Composer > Detect Chords è un'azione sulle note selezionate ma non sta nel
    # menu Notes (quindi non in NOTES_ACTIONS): senza questa aggiunta non veniva mai
    # disattivata, né sulla batteria né senza note selezionate.
    NOTES_EXTRA_ACTIONS = ("notes-detect-chords",)

    def _update_notes_actions(self) -> bool:
        """Menu Notes (e Composer > Detect Chords): attivo solo con note selezionate
        nell'editor in primo piano (griglia o notazione); staccato, slur e Detect Chords
        non valgono per le tracce di batteria."""
        editor = self._current_editor()
        on = isinstance(editor, NoteEditing) and bool(editor.selection)
        drums = on and editor.is_drums
        for name in self.NOTES_ACTIONS + self.NOTES_EXTRA_ACTIONS:
            self._enable(name, on and not (drums and name in self.STACCATO_ACTIONS))
        return False

    def _enable(self, name: str, on: bool) -> None:
        action = self.lookup_action(name)
        if action.get_enabled() != on:
            action.set_enabled(on)

    def _focus_current(self) -> bool:
        page = self.notebook.get_nth_page(self.notebook.get_current_page())
        if isinstance(page, NoteEditing):
            page.area.grab_focus()
        return False

    # --- tastiera --------------------------------------------------------

    def on_key(self, _c, keyval: int, _code: int, state: Gdk.ModifierType) -> bool:
        if state & (Gdk.ModifierType.CONTROL_MASK | Gdk.ModifierType.ALT_MASK):
            return False
        focus = self.get_focus()
        if isinstance(focus, Gtk.Editable):
            return False
        if focus is not None and focus.get_ancestor(Gtk.Popover) is not None:
            return False  # dentro un menu aperto i tasti (Invio, lettere, Spazio) restano al menu
        name = Gdk.keyval_name(keyval)
        if name == "Escape" and self.tracks.reordering is not None:
            self.tracks.cancel_reorder()  # Track > Reorder: Esc annulla
            return True
        if name in ("g", "G"):
            self.open_grid()
            return True
        if name in ("e", "E", "Return", "KP_Enter"):
            self.open_notation()
            return True
        if name in ("l", "L") and self.session.document and self._current_editor() is None:
            self.label_selection()  # Segment > Label
            return True
        if name == "Delete" and self.session.document and self._current_editor() is None:
            self.tracks.delete_selection()  # tasto Canc: cancella la selezione, in ogni modalità
            return True
        if name == "Delete" and isinstance(self._current_editor(), NoteEditing):
            self._current_editor().delete_selection()  # griglia: cancella le note selezionate
            return True
        if name in ("1", "2", "3", "4", "5", "6", "7", "8") and isinstance(self._current_editor(), NotationView):
            self._note_value.change_state(GLib.Variant("s", name))  # valore di nota (come la palette)
            return True
        if name == "space" and self.session.document:
            self.player.play_pause()
            return True
        return False

    def on_close(self, *_a) -> bool:
        doc = self.session.document
        if doc is not None and doc.modified and not getattr(self, "_closing", False):
            def go() -> None:
                self._closing = True
                self.close()

            self.after_saving_changes(go)
            return True  # la finestra resta aperta finché non si risponde
        for view in self.editor_views():
            view.shutdown()
        for window in list(self._event_windows):
            window.close()
        self.tracks.shutdown()
        self.player.shutdown()
        self.midi_in.close()
        self.session.close()
        return False

    def show_message(self, text: str, seconds: int = 8, error: bool = False) -> None:
        """Messaggio temporaneo nella riga di stato; gli errori in rosso e in grassetto."""
        self.status.set_label(text)
        self.status.set_tooltip_text(text)
        if error:
            self.status.add_css_class("status-error")
        else:
            self.status.remove_css_class("status-error")
        if self._message_timer:
            GLib.source_remove(self._message_timer)

        def restore() -> bool:
            self._message_timer = 0
            self.status.set_label(HINT)
            self.status.set_tooltip_text(None)
            self.status.remove_css_class("status-error")
            return False

        self._message_timer = GLib.timeout_add_seconds(seconds, restore)
