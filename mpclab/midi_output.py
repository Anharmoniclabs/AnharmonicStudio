"""Owned MIDI-output worker with monotonic clock scheduling and hotplug recovery."""

from __future__ import annotations

import queue
import threading
import time

from .midi_devices import stable_ports


class MidiOutputService:
    def __init__(self, factory=None, clock=time.monotonic):
        self.factory, self.clock = factory, clock
        self.ports = ()
        self.selected = ""
        self.clock_enabled = False
        self.transport = (False, 0.0, 120.0, 0.0)
        self.messages = queue.Queue(maxsize=2048)
        self.error = ""
        self.dropped = 0
        self.late_ticks = 0
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._thread = None
        self._running = False
        self._next_tick = None
        self._last_position = None

    def start(self):
        if self._thread is None:
            self._thread = threading.Thread(
                target=self._run, name="Anharmonic MIDI output", daemon=True
            )
            self._thread.start()

    def select(self, port_id, clock_enabled=False):
        self.selected = port_id or ""
        self.clock_enabled = bool(clock_enabled)
        self._wake.set()

    def send(self, message):
        message = tuple(message)
        if (
            not message
            or len(message) > 1024
            or any(type(v) is not int or not 0 <= v <= 255 for v in message)
        ):
            raise ValueError("Invalid outgoing MIDI message")
        try:
            self.messages.put_nowait(message)
        except queue.Full:
            self.dropped += 1
        self._wake.set()

    def pump_clock(self, send, now):
        """Deterministic scheduling core, shared by the worker and fake-clock tests."""
        playing, beat, bpm, presentation = self.transport
        playing = playing and self.clock_enabled
        interval = 60.0 / max(20.0, min(300.0, bpm)) / 24
        expected = beat + max(0.0, now - presentation) * bpm / 60
        if playing and not self._running:
            if beat <= 1e-6:
                send([0xFA])
            else:
                position = max(0, min(16383, round(beat * 4)))
                send([0xF2, position & 127, position >> 7])
                send([0xFB])
            self._next_tick = max(now, presentation)
        elif not playing and self._running:
            send([0xFC])
            self._next_tick = None
        elif (
            playing
            and self._last_position is not None
            and abs(expected - self._last_position[0] - (now - self._last_position[1]) * bpm / 60)
            > 0.5
        ):
            position = max(0, min(16383, round(beat * 4)))
            send([0xF2, position & 127, position >> 7])
            self._next_tick = max(now, presentation)
        self._running = playing
        self._last_position = (expected, now)
        if playing and self._next_tick is not None and now >= self._next_tick:
            late = int((now - self._next_tick) / interval)
            self.late_ticks += late
            send([0xF8])
            # Never burst old clock ticks after suspension or scheduler stalls.
            self._next_tick += (late + 1) * interval

    def _run(self):
        inventory = port = None
        opened = ""
        address = ""
        next_scan = 0.0
        try:
            factory = self.factory
            if factory is None:
                import rtmidi

                def factory():
                    return rtmidi.MidiOut(name="Anharmonic Studio output")

            while not self._stop.is_set():
                now = self.clock()
                try:
                    if now >= next_scan or opened != self.selected:
                        next_scan = now + 1
                        if inventory is None:
                            inventory = factory()
                        names = inventory.get_ports()
                        backend = "alsa" if any(":" in name for name in names) else "midi"
                        self.ports = tuple(stable_ports(names, backend))
                        selected = next((p for p in self.ports if p.id == self.selected), None)
                        endpoint = names[selected.index] if selected else ""
                        if port is not None and (opened != self.selected or endpoint != address):
                            self._release(port)
                            port = None
                            self._running = False
                        if self.selected and selected and port is None:
                            port = factory()
                            fresh = stable_ports(port.get_ports(), backend)
                            match = next(p for p in fresh if p.id == self.selected)
                            port.open_port(match.index, "Anharmonic Studio output")
                            address = endpoint
                            self.error = ""
                        opened = self.selected
                        if self.selected and selected is None:
                            self.error = "MIDI output disconnected; waiting for reconnect"
                    if port is not None:
                        for _ in range(256):
                            try:
                                message = self.messages.get_nowait()
                            except queue.Empty:
                                break
                            port.send_message(list(message))
                        self.pump_clock(port.send_message, now)
                except Exception as exc:
                    self.error = str(exc) or "MIDI output unavailable"
                    if port is not None:
                        self._release(port)
                    port = None
                    self._running = False
                    next_scan = now + 1
                self._wake.wait(0.001)
                self._wake.clear()
        finally:
            if port is not None:
                self._release(port)
            if inventory is not None:
                self._dispose(inventory)

    @staticmethod
    def _dispose(port):
        try:
            port.close_port()
        finally:
            if hasattr(port, "delete"):
                port.delete()

    def _release(self, port):
        try:
            if self._running:
                port.send_message([0xFC])
            for channel in range(16):
                port.send_message([0xB0 | channel, 64, 0])
                port.send_message([0xB0 | channel, 123, 0])
        except Exception:
            pass
        finally:
            self._dispose(port)

    def close(self):
        self._stop.set()
        self._wake.set()
        if self._thread is not None:
            self._thread.join(timeout=3)
            if self._thread.is_alive():
                raise RuntimeError("MIDI output worker is still closing")
