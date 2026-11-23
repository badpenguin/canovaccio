"""Menu Composer: dialoghi per inserire accordi sulla linea rossa dell'editor attivo.

- Chord…: accordo standard (fondamentale, tipo, flag, forma stretta/lata, ottava).
- Guitar Chord…: accordo di chitarra del libretto (note reali), con la pennata.

"Inserisci" inserisce e lascia aperto il dialogo: la linea rossa avanza, così si
scrive una sequenza di accordi; "Chiudi" o Esc chiudono. Durata: valore della palette
(notazione) o Snap (griglia).
"""

from __future__ import annotations

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gtk  # noqa: E402

from ..core.chords import CLOSE, FLAGS, OPEN, TYPE_NAMES, ChordTone, build_chord, chord_name  # noqa: E402
from ..core.guitar import KINDS, ROOTS, guitar_chord  # noqa: E402
from ..core.pitch import Spelling  # noqa: E402

ROOT_NAMES = ["A", "A#", "Bb", "B", "C", "C#", "Db", "D", "D#", "Eb", "E", "F", "F#", "Gb", "G", "G#", "Ab"]  # da A a G
GUITAR_ORDER = [9, 10, 11, 0, 1, 2, 3, 4, 5, 6, 7, 8]  # fondamentali della chitarra da A a G (classi d'altezza)
NOTE_NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]
STRUMS = [(None, "none"), ("down", "down (low to high)"), ("up", "up (high to low)")]


def _pretty(text: str) -> str:
    return text.replace("#", "♯").replace("b", "♭") if len(text) > 1 else text


def _label(tone: ChordTone) -> str:
    name = str(tone.spelling) if tone.spelling is not None else NOTE_NAMES[tone.midi % 12]
    return f"{_pretty(name)}{tone.midi // 12 - 1}"


class _Base(Gtk.Window):
    def __init__(self, parent: Gtk.Window, view, title: str) -> None:
        super().__init__(title=title, transient_for=parent, modal=True, resizable=False)
        self.view = view
        self.grid = Gtk.Grid(row_spacing=10, column_spacing=12)
        self.row = 0
        self.preview = Gtk.Label(xalign=0, wrap=True, css_classes=["heading"])
        close = Gtk.Button(label="Close")
        close.connect("clicked", lambda *_: self.close())
        insert = Gtk.Button(label="Insert")
        insert.add_css_class("suggested-action")
        insert.connect("clicked", lambda *_: self.insert())
        buttons = Gtk.Box(spacing=8, halign=Gtk.Align.END, margin_top=8)
        buttons.append(close)
        buttons.append(insert)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12,
                      margin_start=18, margin_end=18, margin_top=18, margin_bottom=18)
        box.append(self.grid)
        box.append(self.preview)
        box.append(buttons)
        self.set_child(box)
        self.set_default_widget(insert)
        esc = Gtk.ShortcutController()
        esc.add_shortcut(Gtk.Shortcut.new(Gtk.ShortcutTrigger.parse_string("Escape"), Gtk.NamedAction.new("window.close")))
        self.add_controller(esc)

    def add(self, label: str, widget: Gtk.Widget) -> None:
        self.grid.attach(Gtk.Label(label=label, xalign=1), 0, self.row, 1, 1)
        self.grid.attach(widget, 1, self.row, 1, 1)
        self.row += 1

    def dropdown(self, items: list[str], selected: int = 0) -> Gtk.DropDown:
        d = Gtk.DropDown.new_from_strings(items)
        d.set_selected(selected)
        d.connect("notify::selected", lambda *_: self.update())
        return d


class ChordDialog(_Base):
    """Composer > Chord…: accordo standard."""

    def __init__(self, parent: Gtk.Window, view) -> None:
        super().__init__(parent, view, "Chord")
        self.root = self.dropdown([_pretty(n) for n in ROOT_NAMES], ROOT_NAMES.index("C"))
        self.add("Root", self.root)
        self.kind = self.dropdown([label for _k, label in TYPE_NAMES])
        self.add("Type", self.kind)
        flags = Gtk.FlowBox(selection_mode=Gtk.SelectionMode.NONE, max_children_per_line=4, column_spacing=6)
        self.flags = {}
        for f in FLAGS:
            check = Gtk.CheckButton(label=f, tooltip_text="root doubled (4 voices)" if f == "8" else None)
            check.connect("toggled", lambda *_: self.update())
            self.flags[f] = check
            flags.insert(check, -1)
        self.add("Extensions", flags)
        self.form = self.dropdown(["close", "open"])
        self.add("Voicing", self.form)
        clef = view.insert_point()[0].clef
        self.octave = Gtk.SpinButton.new_with_range(1, 7, 1)
        self.octave.set_value(3 if clef.name == "bass" else 4)  # attorno al Do centrale (basso: un'ottava sotto)
        self.octave.connect("value-changed", lambda *_: self.update())
        self.add("Octave", self.octave)
        self.update()

    def chord(self) -> tuple[str, list[ChordTone]]:
        root = Spelling.parse(ROOT_NAMES[self.root.get_selected()])
        kind = TYPE_NAMES[self.kind.get_selected()][0]
        flags = {f for f, c in self.flags.items() if c.get_active()}
        form = OPEN if self.form.get_selected() == 1 else CLOSE
        tones = build_chord(root, kind, flags, form, self.octave.get_value_as_int())
        return chord_name(root, kind, flags), tones  # sigla in ASCII (#, b): va anche nel MIDI

    def update(self) -> None:
        if not hasattr(self, "octave"):
            return
        name, tones = self.chord()
        self.preview.set_label(f"{_pretty(name)}:  " + "  ".join(_label(t) for t in tones))

    def insert(self) -> None:
        name, tones = self.chord()
        self.view.insert_chord(tones, symbol=name)


class GuitarChordDialog(_Base):
    """Composer > Guitar Chord…: accordo di chitarra del libretto, note reali."""

    def __init__(self, parent: Gtk.Window, view) -> None:
        super().__init__(parent, view, "Guitar Chord")
        self.root = self.dropdown([_pretty(ROOTS[pc]) for pc in GUITAR_ORDER], GUITAR_ORDER.index(0))
        self.add("Root", self.root)
        self.kind = self.dropdown([label for _k, label in KINDS])
        self.add("Type", self.kind)
        self.strum = self.dropdown([label for _s, label in STRUMS], 1)
        self.add("Strum", self.strum)
        self.update()

    def chord(self):
        return guitar_chord(self._pc(), KINDS[self.kind.get_selected()][0])

    def _pc(self) -> int:
        return GUITAR_ORDER[self.root.get_selected()]

    def update(self) -> None:
        if not hasattr(self, "strum"):
            return
        c = self.chord()
        notes = "  ".join(_label(ChordTone(m, None)) for m in c.midi)
        name = _pretty(ROOTS[self._pc()]) + {"maj": "", "min": "m"}.get(
            KINDS[self.kind.get_selected()][0], KINDS[self.kind.get_selected()][0])
        self.preview.set_label(f"{name}   {c.shape}   ({c.description})\n{notes}")

    def name(self) -> str:
        kind = KINDS[self.kind.get_selected()][0]
        return ROOTS[self._pc()] + {"maj": "", "min": "m"}.get(kind, kind)  # ASCII

    def insert(self) -> None:
        tones = [ChordTone(m, None) for m in self.chord().midi]
        self.view.insert_chord(tones, STRUMS[self.strum.get_selected()][0], symbol=self.name())


class ChordSymbolDialog(Gtk.Window):
    """Composer > Chord Symbol…: sigla d'accordo a mano, testo libero, sulla linea rossa
    (segmento del rigo attivo o della griglia). Se lì c'è già una sigla la si modifica;
    vuota = cancellata. Scritta così com'è (senza la regola "solo dove cambia")."""

    def __init__(self, parent: Gtk.Window, view) -> None:
        super().__init__(title="Chord Symbol", transient_for=parent, modal=True, resizable=False)
        self.view = view
        found = view.symbol_at_insert_point()
        self.entry = Gtk.Entry(text=found[2] if found else "", activates_default=True,
                               placeholder_text="e.g. Am7, F#m, Bbmaj7, D/F#")
        grid = Gtk.Grid(row_spacing=10, column_spacing=12)
        grid.attach(Gtk.Label(label="Chord symbol", xalign=1), 0, 0, 1, 1)
        grid.attach(self.entry, 1, 0, 1, 1)
        hint = "Empty = delete the chord symbol at this point." if found else "No segment here: no chord symbol."
        grid.attach(Gtk.Label(label=hint, xalign=0), 0, 1, 2, 1)
        cancel = Gtk.Button(label="Cancel")
        cancel.connect("clicked", lambda *_: self.close())
        ok = Gtk.Button(label="Save")
        ok.add_css_class("suggested-action")
        ok.connect("clicked", lambda *_: self.view.set_symbol(self.entry.get_text()) and self.close())
        buttons = Gtk.Box(spacing=8, halign=Gtk.Align.END, margin_top=8)
        buttons.append(cancel)
        buttons.append(ok)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12,
                      margin_start=18, margin_end=18, margin_top=18, margin_bottom=18)
        box.append(grid)
        box.append(buttons)
        self.set_child(box)
        self.set_default_widget(ok)
        esc = Gtk.ShortcutController()
        esc.add_shortcut(Gtk.Shortcut.new(Gtk.ShortcutTrigger.parse_string("Escape"), Gtk.NamedAction.new("window.close")))
        self.add_controller(esc)
