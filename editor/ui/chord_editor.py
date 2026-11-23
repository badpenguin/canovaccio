"""Chord Editor: gli eventi accordo di un segmento della traccia accordi, come lista.

In cima: Stile (fra quelli del brano, proprietà della traccia accordi), Sezione (con la
durata in misure) e Fill finale (nessuno di predefinito) da generare per questo segmento.

Una riga per accordo: posizione nel brano (misura.movimento.tick, 480 tick per
semiminima; esatta, non agganciata), fondamentale, tipo (i tipi Yamaha del Chord
Looper), basso (accordo con barra). Aggiungi / ✕ per riga; Quantizza (al movimento, a
1/8 o alla misura: gli accordi finiti nello stesso punto si riducono all'ultimo, quelli
suonati come passaggio); Salva (un passo di Ctrl+Z) / Annulla o Esc. Il contenuto è del
gruppo: i cloni del segmento cambiano insieme.
"""

from __future__ import annotations

from fractions import Fraction

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gtk  # noqa: E402

from ..core.chord_track import CANCEL, ROOTS, ChordEvent, kind_name  # noqa: E402
from pathlib import Path  # noqa: E402

from ..core.document import Compound, Document, SetAttribute  # noqa: E402
from ..core.model import Segment, Track  # noqa: E402
from ..importer.style import style_sections  # noqa: E402

TICKS = 1920  # tick per semibreve (480 per semiminima)
QUANTIZE = [("Beat", None), ("1/8", Fraction(1, 8)), ("Bar", "bar")]  # None = movimento del metro


class ChordEditorDialog(Gtk.Window):
    def __init__(self, parent: Gtk.Window, doc: Document, track: Track, seg: Segment,
                 on_generate=None) -> None:
        super().__init__(title=f"Chord Editor — {seg.label or 'Chords'}", transient_for=parent, modal=True,
                         default_width=620, default_height=520)
        self.doc, self.seg = doc, seg
        self.on_generate = on_generate  # Genera: salva e genera questo segmento nelle tracce dello stile
        self.group = doc.song.groups[seg.group_id]
        self.start, self.end = doc.song.segment_span(seg)
        extra = sorted({e.kind for e in self.group.chord_events if not 0 <= e.kind <= CANCEL})
        self.kinds = list(range(CANCEL + 1)) + extra
        self.rows: list[dict] = []

        # stile, sezione e fill finale del segmento
        self.style_paths = [""] + list(track.styles) + ([seg.style] if seg.style and seg.style not in track.styles else [])
        self.style = Gtk.DropDown.new_from_strings(["—"] + [Path(p).name for p in self.style_paths[1:]])
        self.style.set_selected(self.style_paths.index(seg.style) if seg.style in self.style_paths else 0)
        self.style.set_tooltip_text("Song styles: add them in the chord track properties "
                                    "(double-click the name)")
        self.style.connect("notify::selected", lambda *_: self._load_sections())
        self.section = Gtk.DropDown.new_from_strings([])
        self.fill = Gtk.DropDown.new_from_strings([])
        self.sections: list[str] = []
        self.fills: list[str] = []
        head = Gtk.Grid(row_spacing=6, column_spacing=8)
        for c, (label, widget) in enumerate((("Style", self.style), ("Section", self.section),
                                             ("Final fill", self.fill))):
            head.attach(Gtk.Label(label=label, xalign=0), c, 0, 1, 1)
            head.attach(widget, c, 1, 1, 1)
        self._load_sections(seg.section, seg.fill)

        self.list = Gtk.Grid(row_spacing=4, column_spacing=8, margin_start=8, margin_end=8, margin_top=8)
        for col, title in enumerate(("Position", "Root", "Type", "Bass", "")):
            self.list.attach(Gtk.Label(label=title, xalign=0, css_classes=["heading"]), col, 0, 1, 1)
        scroll = Gtk.ScrolledWindow(vexpand=True, child=self.list)
        self.error = Gtk.Label(xalign=0, css_classes=["error"])
        add = Gtk.Button(label="Add")
        add.connect("clicked", lambda *_: self._add_after_last())
        self.grid_choice = Gtk.DropDown.new_from_strings([label for label, _g in QUANTIZE])
        quantize = Gtk.Button(label="Quantize")
        quantize.set_tooltip_text("Move the chords to the nearest point of the chosen grid")
        quantize.connect("clicked", lambda *_: self.quantize())
        cancel = Gtk.Button(label="Cancel")
        cancel.connect("clicked", lambda *_: self.close())
        save = Gtk.Button(label="Save")
        save.add_css_class("suggested-action")
        save.connect("clicked", lambda *_: self.save())
        buttons = Gtk.Box(spacing=8)
        buttons.append(add)
        buttons.append(Gtk.Box(width_request=12))
        buttons.append(self.grid_choice)
        buttons.append(quantize)
        buttons.append(Gtk.Box(hexpand=True))
        buttons.append(cancel)
        if on_generate is not None:
            generate = Gtk.Button(label="Generate")
            generate.set_tooltip_text("Save and generate the accompaniment of this segment in the style tracks")
            generate.connect("clicked", lambda *_: self.save(generate=True))
            buttons.append(generate)
        buttons.append(save)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10,
                      margin_start=14, margin_end=14, margin_top=14, margin_bottom=14)
        box.append(head)
        box.append(Gtk.Frame(child=scroll))
        box.append(self.error)
        box.append(buttons)
        self.set_child(box)
        esc = Gtk.ShortcutController()
        esc.add_shortcut(Gtk.Shortcut.new(Gtk.ShortcutTrigger.parse_string("Escape"), Gtk.NamedAction.new("window.close")))
        self.add_controller(esc)

        for e in sorted(self.group.chord_events, key=lambda e: e.pos):
            rel = e.pos - seg.trim
            if 0 <= rel < seg.length:
                self._add_row(self.start + rel, e)

    # --- stile / sezione / fill ---------------------------------------------------

    def _load_sections(self, section: str | None = None, fill: str | None = None) -> None:
        """Sezioni dello stile scelto, con la durata ("Main A — 4 misure"), e i suoi fill."""
        path = self.style_paths[self.style.get_selected()]
        found: list[tuple[str, int]] = []
        if path and Path(path).is_file():
            try:
                found = style_sections(path)
            except Exception:  # noqa: BLE001 — stile illeggibile: niente sezioni
                found = []
        style_change = section is None
        if style_change:  # cambio di stile: si tiene la sezione se c'è anche nel nuovo
            i = self.section.get_selected()
            section = self.sections[i] if 0 < i < len(self.sections) else ""
            j = self.fill.get_selected()
            fill = self.fills[j] if 0 < j < len(self.fills) else ""
        if style_change and found:
            # 2026-11-20: il commento sopra non era applicato: la sezione (o il fill) che il nuovo
            # stile non ha restava scelta, e Generate cancellava l'accompagnamento senza avvisi
            names = {n for n, _b in found}
            section = section if section in names else ""
            fill = fill if fill in names else ""
        main = [(n, b) for n, b in found if not n.startswith("Fill")]
        self.sections = [""] + [n for n, _b in main]
        labels = ["—"] + [f"{n} — {b} {'bar' if b == 1 else 'bars'}" for n, b in main]
        if section and section not in self.sections:  # sezione salvata ma stile mancante
            self.sections.append(section)
            labels.append(section)
        self.section.set_model(Gtk.StringList.new(labels))
        self.section.set_selected(self.sections.index(section) if section in self.sections else 0)
        self.fills = [""] + [n for n, _b in found if n.startswith("Fill")]
        if fill and fill not in self.fills:
            self.fills.append(fill)
        self.fill.set_model(Gtk.StringList.new(["none"] + self.fills[1:]))
        self.fill.set_selected(self.fills.index(fill) if fill in self.fills else 0)

    # --- posizioni ---------------------------------------------------------

    def _format(self, pos: Fraction) -> str:
        song = self.doc.song
        bar, off = song.split_position(pos)
        beat = song.meters.at(bar).beat
        n = int(off // beat)
        tick = (off - n * beat) * TICKS
        return f"{bar + 1}.{n + 1}.{round(tick)}"

    def _parse(self, text: str) -> Fraction | None:
        try:
            parts = [int(p) for p in text.strip().split(".")]
        except ValueError:
            return None
        while len(parts) < 3:
            parts.append(1 if len(parts) < 2 else 0)
        bar, beat, tick = parts[:3]
        if bar < 1 or beat < 1 or tick < 0:
            return None
        song = self.doc.song
        return song.meters.bar_start(bar - 1) + (beat - 1) * song.meters.at(bar - 1).beat + Fraction(tick, TICKS)

    # --- righe ---------------------------------------------------------------

    def _add_row(self, pos: Fraction, e: ChordEvent) -> None:
        r = len(self.rows) + 1
        position = Gtk.Entry(text=self._format(pos), width_chars=10)
        root = Gtk.DropDown.new_from_strings(ROOTS)
        root.set_selected(ROOTS.index(e.root) if e.root in ROOTS else 0)
        kind = Gtk.DropDown.new_from_strings([kind_name(k) or "Maj" for k in self.kinds])
        kind.set_selected(self.kinds.index(e.kind) if e.kind in self.kinds else 0)
        bass = Gtk.DropDown.new_from_strings(["—"] + ROOTS)
        bass.set_selected(ROOTS.index(e.bass) + 1 if e.bass in ROOTS else 0)
        remove = Gtk.Button(icon_name="window-close-symbolic", tooltip_text="Delete the chord")
        # "orig": testo mostrato e posizione esatta; se il testo non cambia vale quella esatta
        # (2026-11-20: gli accordi del Chord Looper, a 1/1920 di semiminima, si spostavano a
        # ogni Save anche senza modifiche: passo di Undo inutile e brano modificato)
        row = {"position": position, "root": root, "kind": kind, "bass": bass, "remove": remove,
               "orig": (position.get_text(), pos)}
        remove.connect("clicked", lambda *_: self._remove(row))
        for col, w in enumerate((position, root, kind, bass, remove)):
            self.list.attach(w, col, r, 1, 1)
        self.rows.append(row)

    def _remove(self, row: dict) -> None:
        for key, w in row.items():
            if key != "orig":  # "orig" non è un widget
                self.list.remove(w)
        self.rows.remove(row)

    def _add_after_last(self) -> None:
        """Nuova riga: un movimento dopo l'ultimo accordo, con lo stesso accordo."""
        events = self._events(check=False)
        song = self.doc.song
        if events:
            last = max(events, key=lambda e: e.pos)
            pos = self.start + last.pos - self.seg.trim
            pos += song.meters.at(song.meters.bar_at(pos)).beat
            e = last
        else:
            pos, e = self.start, ChordEvent(Fraction(0), "C", 0)
        self._add_row(min(pos, self.end - Fraction(1, TICKS)), e)

    def _events(self, check: bool = True) -> list[ChordEvent] | None:
        out = []
        for row in self.rows:
            text = row["position"].get_text().strip()
            pos = row["orig"][1] if text == row["orig"][0] else self._parse(text)
            if pos is None or not self.start <= pos < self.end:
                if check:
                    self.error.set_label(f"Invalid position: “{row['position'].get_text()}” "
                                         f"(bar.beat.tick, inside the segment)")
                    return None
                continue
            bass = row["bass"].get_selected()
            out.append(ChordEvent(pos - self.start + self.seg.trim, ROOTS[row["root"].get_selected()],
                                  self.kinds[row["kind"].get_selected()], ROOTS[bass - 1] if bass else ""))
        return sorted(out, key=lambda e: e.pos)

    def quantize(self) -> None:
        """Accordi sul punto più vicino della griglia (dentro la loro misura). Se più accordi
        finiscono nello stesso punto resta l'ultimo suonato; quelli che finirebbero alla
        fine del segmento si tolgono."""
        events = self._events()
        if events is None:
            return
        song, grid = self.doc.song, QUANTIZE[self.grid_choice.get_selected()][1]
        snapped: dict[Fraction, ChordEvent] = {}
        for e in events:  # in ordine di posizione: a parità di punto vince l'ultimo
            pos = self.start + e.pos - self.seg.trim
            bar, off = song.split_position(pos)
            meter = song.meters.at(bar)
            step = meter.bar_length if grid == "bar" else (grid or meter.beat)
            new = song.meters.bar_start(bar) + round(off / step) * step
            if new < self.end:
                snapped[new] = e
        for row in list(self.rows):
            self._remove(row)
        for pos, e in sorted(snapped.items()):
            self._add_row(pos, e)
        self.error.set_label("")

    def save(self, generate: bool = False) -> None:
        events = self._events()
        if events is None:
            return
        hidden = [e for e in self.group.chord_events if not 0 <= e.pos - self.seg.trim < self.seg.length]
        new = sorted(hidden + events, key=lambda e: e.pos)  # quelli nascosti dal Resize restano
        commands = []
        if new != self.group.chord_events:
            commands.append(SetAttribute(self.group, "chord_events", new, "Edit chords"))
        arrangement = {"style": self.style_paths[self.style.get_selected()],
                       "section": self.sections[self.section.get_selected()] if self.sections else "",
                       "fill": self.fills[self.fill.get_selected()] if self.fills else ""}
        for attr, value in arrangement.items():
            if getattr(self.seg, attr) != value:
                commands.append(SetAttribute(self.seg, attr, value))
        if commands:
            self.doc.execute(Compound(commands, "Edit chords"))
        self.close()
        if generate:
            self.on_generate([self.seg])
