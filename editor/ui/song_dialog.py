"""Finestra Song Properties: tempo, metro e tonalità del brano in un'unica tabella.

Ogni riga è un evento: Misura, Battuta, Proprietà, Valore (il campo Valore cambia
con la proprietà). Le tre righe in misura 1, battuta 1 (valori iniziali) ci sono
sempre: si cambia solo il valore. Le altre hanno il cestino a fine riga.
Metro e Scala cambiano solo sulla battuta 1 di una misura. Salva applica tutto
come un solo passo di Ctrl+Z; Annulla o Esc scartano.
Sopra la tabella il campo Copyright (2026-11-23): l'avviso di copyright del brano
(meta-evento MIDI "Copyright Notice"), salvato nello stesso passo.
"""

from __future__ import annotations

from fractions import Fraction

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gtk  # noqa: E402

from ..core.document import Compound, Document, SetAttribute, SetSongTables  # noqa: E402
from ..core.key import MAJOR, MINOR, Key  # noqa: E402
from ..core.timeline import KeyTable, MeterTable, TempoTable, TimeSignature  # noqa: E402
from .icons import themed  # noqa: E402
from .tracks_view import key_label  # noqa: E402

TEMPO, KEY, METER = "Tempo", "Scala", "Metro"
PROPERTIES = [TEMPO, KEY, METER]
# 2026-11-20: interfaccia in inglese. TEMPO/KEY/METER restano identificatori (righe della
# tabella, nomi delle pagine dello Stack, test); a video si mostra l'etichetta inglese.
PROPERTY_LABELS = {TEMPO: "Tempo", KEY: "Key", METER: "Time Signature"}
COLUMNS = [("Bar", 70), ("Beat", 70), ("Property", 110), ("Value", 230)]
DELETE_WIDTH = 34


def song_rows(song) -> list[tuple[int, Fraction, str, str]]:
    """Righe della tabella: (misura 1.., battuta 1.., proprietà, valore)."""
    meters = song.meters
    rows = []
    for ev in song.tempos:
        bar = meters.bar_at(ev.at)
        beat = (ev.at - meters.bar_start(bar)) / meters.at(bar).beat + 1
        rows.append((bar + 1, beat, TEMPO, f"{ev.value:g}"))
    rows += [(ev.at + 1, Fraction(1), KEY, str(ev.value)) for ev in song.keys]
    rows += [(ev.at + 1, Fraction(1), METER, str(ev.value)) for ev in meters]
    order = {p: i for i, p in enumerate(PROPERTIES)}
    return sorted(rows, key=lambda r: (r[0], r[1], order[r[2]]))


def parse_meter(text: str) -> TimeSignature:
    num, den = text.replace(" ", "").split("/")
    ts = TimeSignature(int(num), int(den))
    if ts.numerator < 1 or ts.denominator not in (1, 2, 4, 8, 16, 32):
        raise ValueError
    return ts


def _overlapping_segments(song, meters: MeterTable) -> str | None:
    """Con la nuova tabella dei metri, il primo caso di due segmenti della stessa traccia che
    si sovrappongono (messaggio per l'utente), o None.

    Decisione 2026-11-20: i segmenti restano agganciati alla loro misura ma la durata è in
    semibrevi; accorciando le misure (es. 4/4 → 3/4) un segmento può finire sopra il
    successivo. Si rifiuta il salvataggio invece di riscalare (riscalare sposterebbe il contenuto)."""
    for track in song.tracks:
        # inizio e fine di ogni segmento con i nuovi metri, in ordine d'inizio
        starts = [(meters.bar_start(s.start_bar) + s.offset, s.length) for s in track.segments]
        spans = sorted((start, start + length) for start, length in starts)
        for (_a0, a1), (b0, _b1) in zip(spans, spans[1:]):
            if a1 > b0:
                bar = meters.bar_at(b0) + 1
                return (f"With this time signature the segments of “{track.name}” would overlap "
                        f"(bar {bar}): shorten or move them first.")
    return None


def build_tables(rows) -> tuple[TempoTable, MeterTable, KeyTable]:
    """Tabelle del brano dalle righe (misura, battuta, proprietà, valore). Le posizioni dei
    cambi di tempo si ricalcolano da misura e battuta (col metro nuovo).
    Solleva ValueError con un messaggio leggibile se qualcosa non va."""
    meters, keys, tempos = {}, {}, []
    for bar, beat, prop, value in rows:
        where = f"bar {bar}, beat {beat}"
        label = PROPERTY_LABELS.get(prop, prop)  # nome della proprietà nei messaggi d'errore
        try:
            if prop == METER:
                parsed = parse_meter(value)
            elif prop == KEY:
                parsed = Key.parse(value)
            else:
                parsed = float(value.replace(",", "."))
                if not 0 < parsed <= 999:
                    raise ValueError
        except (ValueError, KeyError, IndexError):
            raise ValueError(f"Invalid {label} ({where}): \"{value}\"") from None
        if prop in (METER, KEY):
            if beat != 1:
                raise ValueError(f"{label} changes only on beat 1 ({where})")
            table = meters if prop == METER else keys
            if bar - 1 in table:
                raise ValueError(f"{label} repeated in bar {bar}")
            table[bar - 1] = parsed
        else:
            tempos.append((bar, beat, parsed))
    meter_table = MeterTable(meters.pop(0), sorted(meters.items()))
    key_table = KeyTable(keys.pop(0), sorted(keys.items()))
    positions = {}
    for bar, beat, bpm in tempos:
        ts = meter_table.at(bar - 1)
        if not 1 <= beat < ts.numerator + 1:
            raise ValueError(f"Tempo: bar {bar} ({ts}) has no beat {beat:g}")
        pos = meter_table.bar_start(bar - 1) + (beat - 1) * ts.beat
        if pos in positions:
            raise ValueError(f"Tempo repeated in bar {bar}, beat {beat:g}")
        positions[pos] = bpm
    tempo_table = TempoTable(positions.pop(0), sorted(positions.items()))
    return tempo_table, meter_table, key_table


# tonalità in ordine cromatico da A (A, A#, Bb, B, C… G#, Ab), maggiore prima della minore
KEYS = sorted((Key.from_fifths(f, mode) for mode in (MAJOR, MINOR) for f in range(-7, 8)),
              key=lambda k: ((k.tonic.pitch_class - 9) % 12, -k.tonic.alter, k.mode is not MAJOR))
DENOMINATORS = [1, 2, 4, 8, 16, 32]


class ValueEditor(Gtk.Stack):
    """Campo Valore: cambia con la proprietà (Tempo = numero, Scala = elenco, Metro = n / d)."""

    def __init__(self) -> None:
        super().__init__(hexpand=True)
        tempo_box = Gtk.Box(spacing=6)
        self.tempo = Gtk.SpinButton.new_with_range(1, 999, 1)
        self.tempo.set_activates_default(True)
        tempo_box.append(self.tempo)
        tempo_box.append(Gtk.Label(label="BPM"))
        self.key = Gtk.DropDown.new_from_strings([key_label(k) for k in KEYS])
        meter_box = Gtk.Box(spacing=6)
        self.numerator = Gtk.SpinButton.new_with_range(1, 32, 1)
        self.numerator.set_activates_default(True)
        self.denominator = Gtk.DropDown.new_from_strings([str(d) for d in DENOMINATORS])
        meter_box.append(self.numerator)
        meter_box.append(Gtk.Label(label="/"))
        meter_box.append(self.denominator)
        self.add_named(tempo_box, TEMPO)
        self.add_named(self.key, KEY)
        self.add_named(meter_box, METER)

    def set(self, prop: str, value: str) -> None:
        if prop == TEMPO:
            bpm = float(value)
            self.tempo.set_digits(0 if bpm == int(bpm) else 2)
            self.tempo.set_value(bpm)
        elif prop == KEY:
            key = Key.parse(value)
            self.key.set_selected(next((i for i, k in enumerate(KEYS) if k == key), KEYS.index(Key.from_fifths(0))))
        else:
            ts = parse_meter(value)
            self.numerator.set_value(ts.numerator)
            if ts.denominator in DENOMINATORS:
                self.denominator.set_selected(DENOMINATORS.index(ts.denominator))

    def text(self, prop: str) -> str:
        if prop == TEMPO:
            self.tempo.update()
            return f"{self.tempo.get_value():g}"
        if prop == KEY:
            return str(KEYS[self.key.get_selected()])
        self.numerator.update()
        return f"{self.numerator.get_value_as_int()}/{DENOMINATORS[self.denominator.get_selected()]}"


class EventRow(Gtk.ListBoxRow):
    def __init__(self, bar: int, beat: Fraction, prop: str, value: str, fixed: bool,
                 defaults: dict[str, str], on_delete) -> None:
        super().__init__(activatable=False, selectable=False)
        self.fixed = fixed
        self._orig = (bar, beat)
        box = Gtk.Box(spacing=6, margin_top=2, margin_bottom=2)
        self.bar = Gtk.SpinButton.new_with_range(1, 9999, 1)
        self.bar.set_value(bar)
        self.beat = Gtk.SpinButton.new_with_range(1, 32, 1)
        self.beat.set_digits(0 if beat.denominator == 1 else 2)
        self.beat.set_value(float(beat))
        self.prop = Gtk.DropDown.new_from_strings([PROPERTY_LABELS[p] for p in PROPERTIES])  # stesso ordine
        self.prop.set_selected(PROPERTIES.index(prop))
        self.value = ValueEditor()
        for p, v in defaults.items():  # ogni proprietà parte dal valore iniziale del brano
            self.value.set(p, v)
        self.value.set(prop, value)
        for w, (_title, width) in zip((self.bar, self.beat, self.prop, self.value), COLUMNS):
            w.set_size_request(width, -1)
            box.append(w)
        if fixed:  # valori iniziali: posizione e proprietà fisse, non si eliminano
            for w in (self.bar, self.beat, self.prop):
                w.set_sensitive(False)
            box.append(Gtk.Box(width_request=DELETE_WIDTH))
        else:
            delete = Gtk.Button(icon_name=themed("user-trash-symbolic"), tooltip_text="Delete",
                                css_classes=["flat"], width_request=DELETE_WIDTH)
            delete.connect("clicked", lambda *_: on_delete(self))
            box.append(delete)
        self.prop.connect("notify::selected", lambda *_: self._prop_changed())
        self._prop_changed()
        self.set_child(box)

    def _prop_changed(self) -> None:
        self.value.set_visible_child_name(self.prop_name())
        if self.prop_name() != TEMPO:  # Metro e Scala: solo sulla battuta 1
            self.beat.set_value(1)
            self.beat.set_sensitive(False)
        elif not self.fixed:
            self.beat.set_sensitive(True)

    def prop_name(self) -> str:
        return PROPERTIES[self.prop.get_selected()]

    def data(self) -> tuple[int, Fraction, str, str]:
        for spin in (self.bar, self.beat):
            spin.update()
        bar = self.bar.get_value_as_int()
        beat = Fraction(self.beat.get_value()).limit_denominator(64)
        obar, obeat = self._orig
        if bar == obar and abs(float(beat - obeat)) < 0.005:
            beat = obeat  # battuta non toccata: resta esattamente quella di prima (anche se frazionaria)
        return bar, beat, self.prop_name(), self.value.text(self.prop_name())


class SongDialog(Gtk.Window):
    def __init__(self, parent: Gtk.Window, doc: Document, new_song: bool = False) -> None:
        super().__init__(title="Song Properties", transient_for=parent, modal=True, default_height=420)
        self.doc = doc
        self.new_song = new_song  # aperto da File > New: il segmento vuoto iniziale segue il metro

        # 2026-11-23: avviso di copyright del brano, sopra la tabella degli eventi
        copyright_box = Gtk.Box(spacing=8)
        copyright_box.append(Gtk.Label(label="Copyright", css_classes=["heading"]))
        self.copyright = Gtk.Entry(hexpand=True, text=doc.song.copyright, activates_default=True,
                                   placeholder_text="e.g. © 2026 Author name")
        copyright_box.append(self.copyright)

        header = Gtk.Box(spacing=6, margin_start=6)
        for title, width in COLUMNS:
            header.append(Gtk.Label(label=title, xalign=0, width_request=width, css_classes=["heading"]))
        header.append(Gtk.Box(width_request=DELETE_WIDTH))
        # 2026-11-23: margine a destra, il cestino non tocca la scrollbar (segnalato dall'utente)
        self.list = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE, margin_end=8)
        rows = song_rows(doc.song)
        self.defaults = {prop: value for bar, beat, prop, value in rows if bar == 1 and beat == 1}
        fixed_left = set(PROPERTIES)
        for bar, beat, prop, value in rows:
            fixed = bar == 1 and beat == 1 and prop in fixed_left
            if fixed:
                fixed_left.discard(prop)
            self.list.append(EventRow(bar, beat, prop, value, fixed, self.defaults, self.list.remove))
        # 2026-11-23: overlay_scrolling=False: la scrollbar ha il suo spazio accanto alle righe invece di
        # disegnarsi sopra il bordo destro, dove copriva il pulsante di cancellazione
        scroller = Gtk.ScrolledWindow(vexpand=True, hscrollbar_policy=Gtk.PolicyType.NEVER, propagate_natural_width=True,
                                      overlay_scrolling=False)
        scroller.set_child(self.list)

        add = Gtk.Button(label="Add")
        add.connect("clicked", lambda *_: self.add_row())
        cancel = Gtk.Button(label="Cancel")
        cancel.connect("clicked", lambda *_: self.close())
        save = Gtk.Button(label="Save")
        save.add_css_class("suggested-action")
        save.connect("clicked", lambda *_: self.save())
        buttons = Gtk.Box(spacing=8, margin_top=8)
        for b in (add, Gtk.Box(hexpand=True), cancel, save):
            buttons.append(b)

        self.error = Gtk.Label(xalign=0, wrap=True, css_classes=["status-error"])
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8,
                      margin_start=18, margin_end=18, margin_top=18, margin_bottom=18)
        for w in (copyright_box, header, scroller, self.error, buttons):
            box.append(w)
        self.set_child(box)
        self.set_default_widget(save)

        esc = Gtk.ShortcutController()
        esc.add_shortcut(Gtk.Shortcut.new(Gtk.ShortcutTrigger.parse_string("Escape"), Gtk.NamedAction.new("window.close")))
        self.add_controller(esc)

    def rows(self) -> list[EventRow]:
        out, row = [], self.list.get_first_child()
        while row is not None:
            if isinstance(row, EventRow):
                out.append(row)
            row = row.get_next_sibling()
        return out

    def add_row(self) -> None:
        """Nuovo cambio di tempo in fondo, una misura dopo l'ultima riga."""
        rows = self.rows()
        bar = max((r.bar.get_value_as_int() for r in rows), default=1) + 1
        row = EventRow(bar, Fraction(1), TEMPO, self.defaults[TEMPO], False, self.defaults, self.list.remove)
        self.list.append(row)
        row.bar.grab_focus()

    def save(self) -> None:
        try:
            tables = build_tables([r.data() for r in self.rows()])
        except ValueError as e:
            self.error.set_label(str(e))
            return
        clash = _overlapping_segments(self.doc.song, tables[1])
        if clash:
            self.error.set_label(clash)
            return
        song = self.doc.song
        copyright = self.copyright.get_text().strip()
        tables_changed = not all(list(new) == list(old) for new, old in zip(tables, (song.tempos, song.meters, song.keys)))
        if not tables_changed and copyright == song.copyright:
            self.close()  # 2026-11-20: niente cambiato: nessun passo di Undo vuoto, brano non modificato
            return
        # 2026-11-23: tabelle e copyright in un solo passo di Ctrl+Z; solo quello che è cambiato
        commands = [SetSongTables(*tables)] if tables_changed else []
        if copyright != song.copyright:
            commands.append(SetAttribute(song, "copyright", copyright, "Copyright"))
        command = commands[0] if len(commands) == 1 else Compound(commands, commands[0].label)
        if self.new_song and tables_changed:
            # 2026-11-20: File > New crea un segmento vuoto di una misura di 4/4; scegliendo
            # qui 3/4 (o 6/8…) restava lungo 4 movimenti e il brano nuovo partiva con 2 misure
            song, meters = self.doc.song, tables[1]
            resize = []
            for track in song.tracks:
                for seg in track.segments:
                    old_bar = song.meters.bar_start(seg.start_bar + 1) - song.meters.bar_start(seg.start_bar)
                    new_bar = meters.bar_start(seg.start_bar + 1) - meters.bar_start(seg.start_bar)
                    if not song.groups[seg.group_id].notes and seg.length == old_bar != new_bar:
                        resize.append(SetAttribute(seg, "length", new_bar, "Segment length"))
            if resize:
                command = Compound([command, *resize], command.label)
        self.doc.execute(command)
        self.close()
