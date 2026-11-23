"""Strato 3 — transport: orologio, play/pausa/stop/salto, posizione.

Indipendente dal backend. Gli eventi partono da un thread; la UI legge
`position` a intervalli e riceve i cambi di stato tramite listener
(chiamati dal thread: la UI deve rimandarli al main loop, es. GLib.idle_add).
"""

from __future__ import annotations

import threading
import time
from bisect import bisect_left
from enum import Enum
from typing import Callable, Protocol

from .backend import OutputBackend
from .performance import CONTROL_EVENTS, Control, NoteOff, NoteOn, Performance


class Clock(Protocol):
    def now(self) -> float: ...


class MonotonicClock:
    def now(self) -> float:
        return time.monotonic()


class State(Enum):
    STOPPED = "stopped"
    PLAYING = "playing"
    PAUSED = "paused"


class Transport:
    MAX_SLEEP = 0.01

    def __init__(self, backend: OutputBackend, clock: Clock | None = None, threaded: bool = True) -> None:
        self.backend = backend
        self.clock = clock or MonotonicClock()
        self.threaded = threaded
        self.performance = Performance([], [], 0.0)
        self.state = State.STOPPED
        self._lock = threading.RLock()
        self._origin_pos = 0.0  # posizione (s) al momento del play
        self._origin_clock = 0.0
        self._paused_pos = 0.0
        self._next = 0  # indice del prossimo evento
        self._wake = threading.Event()
        self._thread: threading.Thread | None = None
        self._listeners: list[Callable[[State], None]] = []
        self.silenced: set[str] = set()  # tracce in mute (o escluse dal solo)
        self._controls_ok: set[str] | None = None  # tracce i cui controlli passano (None = da ricalcolare)
        self.loop: tuple[float, float] | None = None  # (inizio, fine) in secondi: ripete fra i due
        self._sounding: dict[tuple[str, int], int] = {}  # (traccia, nota) → note on in corso

    # --- API ---------------------------------------------------------------

    def add_listener(self, fn: Callable[[State], None]) -> None:
        self._listeners.append(fn)

    @property
    def position(self) -> float:
        with self._lock:
            if self.state is State.PLAYING:  # durante l'attesa d'avvio (Program Change) resta all'inizio
                return max(self._origin_pos + (self.clock.now() - self._origin_clock), self._origin_pos)
            return self._paused_pos

    def load(self, performance: Performance) -> None:
        """Carica una nuova esecuzione (es. dopo un cambio di strumento), mantenendo posizione e stato."""
        with self._lock:
            pos, playing = self.position, self.state is State.PLAYING
            if playing:
                self._panic()
            self.performance = performance
            self._controls_ok = None
            self.backend.setup(performance.tracks)
            self._jump(pos)
            if playing:
                # 2026-11-20: dopo il panic (che azzera anche sustain, pitch bend, expression)
                # si rimandano i controlli in vigore, come in play e seek.
                self._chase(pos)

    def set_backend(self, backend: OutputBackend) -> None:
        """Cambia uscita, anche durante il play: le note in corso si fermano, il play
        continua dalla nota successiva sulla nuova uscita."""
        with self._lock:
            pos = self.position
            self._panic()
            self.backend = backend
            backend.setup(self.performance.tracks)
            if self.state is State.PLAYING:
                # 2026-11-20: la nuova uscita riceve i controlli in vigore (volume, pan, sustain…) e
                # il play aspetta che abbia cambiato suono (Program Change), come all'avvio del play
                settle = backend.start() or 0.0
                self._origin_pos, self._origin_clock = pos, self.clock.now() + settle  # gli eventi già mandati restano tali
                self._chase(pos)

    def set_silenced(self, track_ids: set[str]) -> None:
        """Mute/Solo, anche durante il playback: le note in corso delle tracce
        appena silenziate si fermano subito; le altre ripartono dalla nota successiva."""
        with self._lock:
            newly = set(track_ids) - self.silenced
            revived = self.silenced - set(track_ids)
            self.silenced = set(track_ids)
            self._controls_ok = None
            for (track_id, key), count in list(self._sounding.items()):
                if track_id in newly:
                    for _ in range(count):
                        self.backend.send(NoteOff(track_id, key))
                    del self._sounding[(track_id, key)]
            # 2026-11-20: il pedale di sustain delle tracce appena silenziate si alza (il suo
            # CC64=0 successivo verrebbe filtrato e le note resterebbero appese), ma solo sui
            # canali che nessuna traccia ancora udibile usa (il canale è condiviso).
            channel = {t.track_id: t.channel for t in self.performance.tracks}
            audible = {channel.get(t.track_id) for t in self.performance.tracks if t.track_id not in self.silenced}
            released: set[int] = set()
            for track_id in newly:
                ch = channel.get(track_id)
                if ch is not None and ch not in audible and ch not in released:
                    released.add(ch)
                    self.backend.send(Control(track_id, 64, 0))
            # 2026-11-20: le tracce che tornano udibili durante il play ricevono i controlli in
            # vigore (volume, pan, pitch bend, pedale): prima suonavano con quelli rimasti nel synth
            if revived and self.state is State.PLAYING:
                self._chase(self.position, revived)

    def preview(self, track_id: str, key: int, velocity: int | None) -> None:
        """Suona (velocity) o ferma (None) una nota fuori dal playback, es. click sulla tastiera."""
        with self._lock:
            if velocity is None:
                self.backend.send(NoteOff(track_id, key))
            else:
                self.backend.send(NoteOn(track_id, key, velocity))

    def live(self, event) -> None:
        """Evento suonato dal vivo (ingresso MIDI in thru): subito all'uscita, anche durante il play."""
        with self._lock:
            self.backend.send(event)

    def set_loop(self, loop: tuple[float, float] | None) -> None:
        """Ripetizione fra due istanti (secondi), anche durante il play; None = niente loop."""
        with self._lock:
            self.loop = loop if loop is None or loop[1] > loop[0] else None
        self._wake.set()

    def play(self, resend: bool = True) -> None:
        """`resend=False`: i suoni sono già stati mandati (registrazione: all'inizio del
        conteggio) e si parte senza l'attesa dei Program Change."""
        with self._lock:
            if self.state is State.PLAYING:
                return
            if self._paused_pos >= self.performance.duration:
                self._paused_pos = 0.0
            if self.loop is not None and not self.loop[0] <= self._paused_pos < self.loop[1]:
                self._paused_pos = self.loop[0]  # loop: si parte dall'inizio del tratto
            self._origin_pos = self._paused_pos
            self._next = self._index_at(self._origin_pos)
            settle = (self.backend.start() or 0.0) if resend else 0.0  # Program Change sul Fantom: un attimo
            self._origin_clock = self.clock.now() + settle
            self._chase(self._origin_pos)
            self._set_state(State.PLAYING)
            if self.threaded and self._thread is None:
                self._wake.clear()
                self._thread = threading.Thread(target=self._run, name="transport", daemon=True)
                self._thread.start()

    def pause(self) -> None:
        with self._lock:
            if self.state is not State.PLAYING:
                return
            self._paused_pos = self.position
            self._panic()
            self._set_state(State.PAUSED)
        self._wake.set()

    def stop(self) -> None:
        with self._lock:
            self._panic()
            self._paused_pos = 0.0
            self._next = 0
            self._set_state(State.STOPPED)
        self._wake.set()

    def reset(self) -> None:
        """Pulsante Panic: Stop, poi l'uscita ferma ogni suono e rimette volume, pan,
        riverbero… ai valori GM. Al play successivo il chase rimanda quelli del brano."""
        with self._lock:
            self.stop()
            self.backend.reset()

    def seek(self, seconds: float) -> None:
        with self._lock:
            target = max(0.0, min(seconds, self.performance.duration))
            if self.state is State.PLAYING:
                self._panic()
                self._chase(target)
            self._jump(target)

    def tick(self) -> float | None:
        """Invia gli eventi scaduti. Ritorna i secondi al prossimo evento (None = fermo)."""
        with self._lock:
            if self.state is not State.PLAYING:
                return None
            pos = self._origin_pos + (self.clock.now() - self._origin_clock)  # durante l'attesa d'avvio è prima dell'inizio
            events = self.performance.events
            if self.loop is not None and pos >= self.loop[1]:  # loop: si torna all'inizio
                start = self.loop[0]
                self._panic()
                self._origin_pos, self._origin_clock = start, self.clock.now()
                self._next = self._index_at(start)
                self._chase(start)
                pos = start
            while self._next < len(events) and events[self._next].time <= pos:
                self._send(events[self._next].event)
                self._next += 1
            if pos >= self.performance.duration and self._next >= len(events):
                self._panic()
                self._paused_pos = 0.0
                self._next = 0
                self._set_state(State.STOPPED)
                return None
            wait = max(events[self._next].time - pos, 0.0) if self._next < len(events) else \
                max(self.performance.duration - pos, 0.0)
            if self.loop is not None:
                wait = min(wait, max(self.loop[1] - pos, 0.0))
            return wait

    def close(self) -> None:
        self.stop()
        thread = self._thread
        if thread:
            thread.join(timeout=1)

    # --- interni -----------------------------------------------------------

    def _send(self, event) -> None:
        if isinstance(event, CONTROL_EVENTS):  # controller, pitch bend, aftertouch
            if self._controls_ok is None:
                # 2026-11-20: i controlli di una traccia silenziata passano se il suo canale lo usa
                # anche una traccia udibile (stato del canale condiviso: piano a due mani sul
                # canale 1 col pedale nella mano destra; in mute il pedale restava giù fino allo Stop)
                channel = {t.track_id: t.channel for t in self.performance.tracks}
                audible = {ch for tid, ch in channel.items() if tid not in self.silenced}
                self._controls_ok = {tid for tid, ch in channel.items() if tid not in self.silenced or ch in audible}
            if event.track_id in self._controls_ok or event.track_id not in self.silenced:
                self.backend.send(event)
            return
        key = (event.track_id, getattr(event, "key", None))
        if isinstance(event, NoteOn):
            if event.track_id in self.silenced:
                return
            self._sounding[key] = self._sounding.get(key, 0) + 1
        elif isinstance(event, NoteOff):
            if key not in self._sounding:
                return  # nota mai partita (traccia silenziata) o già fermata
            self._sounding[key] -= 1
            if self._sounding[key] <= 0:
                del self._sounding[key]
        self.backend.send(event)

    def _chase(self, pos: float, tracks: set[str] | None = None) -> None:
        """Partendo da `pos` (play, salto, loop): i controlli in vigore in quel punto (ultimo
        valore di ogni controller, pitch bend, aftertouch per traccia) si mandano subito.
        `tracks`: solo quelle tracce (es. tolte dal mute durante il play)."""
        latest = {}
        for e in self.performance.events[:self._index_at(pos)]:
            ev = e.event
            if isinstance(ev, CONTROL_EVENTS) and (tracks is None or ev.track_id in tracks):
                latest[(type(ev), ev.track_id, getattr(ev, "control", getattr(ev, "key", 0)))] = ev
        for ev in latest.values():
            self._send(ev)

    def _panic(self) -> None:
        self._sounding.clear()
        self.backend.panic()

    def _jump(self, pos: float) -> None:
        if self.state is State.PLAYING:
            self._origin_pos, self._origin_clock = pos, self.clock.now()
        else:
            self._paused_pos = pos
        self._next = self._index_at(pos)

    def _index_at(self, pos: float) -> int:
        return bisect_left([e.time for e in self.performance.events], pos)

    def _set_state(self, state: State) -> None:
        if state is not self.state:
            self.state = state
            for fn in list(self._listeners):
                fn(state)

    def _run(self) -> None:
        while True:
            wait = self.tick()
            if wait is None:
                with self._lock:
                    if self.state is not State.PLAYING:
                        self._thread = None
                        return
                continue
            self._wake.wait(min(wait, self.MAX_SLEEP))
            self._wake.clear()
