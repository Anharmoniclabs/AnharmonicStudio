"""Engine-owned MIDI routing, timing, recording and external clock following.

Only the render worker calls process(). UI controls publish immutable routes;
device callbacks enqueue messages. No performance callback invokes a widget.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
import math
import queue
import threading
import time

from .midi_devices import MidiRouter
from .music import Note, MidiControl
from .midi_playback import apply_expression


@dataclass
class MidiTake:
    origin: float
    notes: list = field(default_factory=list)
    controls: list = field(default_factory=list)
    held: dict = field(default_factory=dict)
    error: str = ""


class MidiClock:
    def __init__(self):
        self.source = None
        self.last = None
        self.intervals = deque(maxlen=48)
        self.position = 0.0
        self.running = False
        self.status = "Internal clock"

    def receive(self, engine, port, message, timestamp):
        if self.source not in (None, port):
            return
        self.source = port
        status = message[0]
        if status == 0xF2:
            if len(message) == 3 and all(type(v) is int and 0 <= v < 128 for v in message[1:]):
                self.position = (message[1] | message[2] << 7) / 4.0
                engine.set_position(self.position)
        elif status in (0xFA, 0xFB):
            if status == 0xFA:
                self.position = 0.0
                self.intervals.clear()
            self.last = timestamp
            self.running = True
            engine.play(self.position)
            self.status = "Following external clock"
        elif status == 0xFC:
            self.running = False
            engine.stop_transport(rewind=False)
            self.status = "External clock stopped"
        elif status == 0xF8:
            if self.last is not None:
                interval = timestamp - self.last
                if 0.002 <= interval <= 0.25:
                    self.intervals.append(interval)
                    if len(self.intervals) >= 4:
                        intervals = sorted(self.intervals)
                        engine.project.bpm = max(
                            20.0, min(300.0, 60 / (24 * intervals[len(intervals) // 2]))
                        )
            self.last = timestamp
            if self.running:
                self.position += 1 / 24

    def check(self, engine, now):
        if self.running and self.last is not None and now - self.last > 1.0:
            self.running = False
            engine.stop_transport(rewind=False)
            self.status = "External clock lost — playback stopped"


class MidiPerformance:
    def __init__(self, engine):
        self.engine = engine
        self.events = queue.Queue(maxsize=2048)
        self.commands = queue.Queue(maxsize=128)
        self.notifications = deque(maxlen=2048)
        self.source = None
        self.output = None
        self.route = (None, None, 0)  # sample slot, instrument ID, pad bank
        self.take = None
        self.pattern_take = None
        self.clock = MidiClock()
        self.now = time.monotonic()
        self.event_beat = 0.0
        self.offset = 0
        self.late_events = 0
        self.pending = []
        self.endings = []
        self.overflow = False
        self.router = MidiRouter(
            self._on,
            self._off,
            self._pad_on,
            self._pad_off,
            lambda: self.route[2],
            self._control,
            self._expression,
        )
        self.router.resolve_note = lambda note, port, channel: (
            "note",
            (note, self.route[0], self.route[1], port, channel),
        )
        self.router.clock = lambda port, message, timestamp: self.clock.receive(
            engine, port, message, timestamp
        )
        self.router.current_values = self._current_value

    def submit(self, port, message, timestamp=None):
        try:
            self.events.put_nowait(
                (port, tuple(message), time.monotonic() if timestamp is None else timestamp)
            )
        except queue.Full:
            self.overflow = True

    def release(self, port=""):
        self.submit(port, ())

    def begin_take(self, origin):
        take = MidiTake(origin)
        self.commands.put_nowait(("begin", take, None))
        return take

    def end_take(self):
        done = threading.Event()
        result = []
        self.commands.put_nowait(("end", result, done))
        if self.engine.stream is None:
            self.process(0, time.monotonic())
        if not done.wait(2):
            raise RuntimeError("MIDI take is still finishing; retry save")
        return result[0]

    def _close_take(self, take):
        if take:
            for key in tuple(take.held):
                self._finish_note(take, key, 0)

    def process(self, frames, now, *, deferred=False):
        self.now = now
        self.event_beat = self.engine.beat
        # Begin commands precede incoming messages; end commands follow them.
        endings = []
        for _ in range(128):
            try:
                kind, value, done = self.commands.get_nowait()
            except queue.Empty:
                break
            if kind == "begin":
                self.take = value
            else:
                endings.append((value, done))
        events = self.source.drain(2048) if self.source else []
        if self.overflow:
            self.overflow = False
            self.router.release_port()
            self._close_take(self.take)
            if self.take:
                self.take.error = "MIDI queue overflow; affected notes were released"
        for _ in range(2048):
            try:
                events.append(self.events.get_nowait())
            except queue.Empty:
                break
        e = self.engine
        events = self.pending + events
        self.pending = []
        scheduled = []
        for event in events:
            timestamp = event[2]
            if not isinstance(timestamp, (int, float)) or not math.isfinite(timestamp):
                continue
            if frames and timestamp > now:
                if len(self.pending) < 2048:
                    self.pending.append(event)
                else:
                    self.overflow = True
                continue
            # Preserve spacing within the preceding callback period. This
            # bounded one-block collector avoids collapsing short notes to zero.
            offset = min(max(0, frames - 1), max(0, round((timestamp - now) * e.sr + frames)))
            scheduled.append((offset, event))
        scheduled.sort(key=lambda item: (item[0], item[1][2]))
        self.endings = endings
        if not deferred:
            for offset, event in scheduled:
                self.apply_event(event, offset)
        self.clock.check(e, now)
        if self.output is not None:
            presentation = e.audio_clock[0] if e.audio_clock else now
            self.output.transport = (e.playing, e.beat, e.project.bpm, presentation)
        if self.pattern_take and not (e.recording and e.playing and e.mode == "pattern"):
            self._close_take(self.pattern_take)
            self.pattern_take = None
        if not deferred:
            self.finish_block()
        return scheduled

    def apply_event(self, event, offset=0):
        port, message, timestamp = event
        e = self.engine
        anchor = e.audio_clock or (self.now + e.latency_ms / 1000, e.beat, e.project.bpm, e.playing)
        self.event_beat = max(0.0, anchor[1] + (timestamp - anchor[0]) * anchor[2] / 60)
        self.offset = offset
        self._capture(port, message)
        self.router.handle(port, message, timestamp)

    def finish_block(self):
        e = self.engine
        for result, done in self.endings:
            self.event_beat = max(self.event_beat, e.beat - e.latency_ms * e.project.bpm / 60000)
            self._close_take(self.take)
            result.append(self.take)
            self.take = None
            done.set()
        self.endings = []

    def _capture(self, port, message):
        e = self.engine
        take = self.take
        if take is None and e.recording and e.playing and e.mode == "pattern":
            if self.pattern_take is None:
                pat = e.project.pattern()
                self.pattern_take = MidiTake(0, pat.notes, pat.midi_controls)
            take = self.pattern_take
            e.pattern_dirty = True
        if take is None:
            return
        if not message:
            for key in tuple(take.held):
                if not port or key[0] == port:
                    self._finish_note(take, key, 0)
            return
        status = message[0]
        if type(status) is not int or not 0x80 <= status < 0xF0:
            return
        kind, channel = status & 0xF0, status & 15
        needed = 2 if kind in (0xC0, 0xD0) else 3
        if len(message) != needed or any(
            type(v) is not int or not 0 <= v < 128 for v in message[1:]
        ):
            return
        settings = self.router.settings.get(port, {})
        if settings.get("channel", -1) not in (-1, channel) or self.router.learn:
            return
        beat = max(0.0, self.event_beat - take.origin)
        if take is self.pattern_take:
            beat %= e.project.pattern().length_beats
        if len(take.notes) + len(take.controls) >= 100000:
            take.error = "MIDI take reached its event limit"
            return
        if kind in (0x80, 0x90):
            key = (port, channel, message[1])
            if kind == 0x80 or message[2] == 0:
                self._finish_note(take, key, message[2])
            else:
                self._finish_note(take, key, 0)
                pitch = max(0, min(127, message[1] + settings.get("transpose", 0)))
                pad, instrument, bank = self.route
                mode = settings.get("mode", "Keys + drum channel")
                if (
                    mode == "Pads"
                    or (mode == "Keys + drum channel" and channel == 9)
                    or str(message[1]) in settings.get("pads", {})
                ):
                    local = settings.get("pads", {}).get(
                        str(message[1]), message[1] - settings.get("pad_base", 36)
                    )
                    if not 0 <= local < 16:
                        return
                    pad, instrument = bank * 16 + local, None
                    pitch = e.project.pads[pad].root_note
                take.held[key] = (
                    self.event_beat,
                    beat,
                    pitch,
                    pad,
                    instrument,
                    self.router.velocity(message[2], settings),
                    channel,
                )
        else:
            take.controls.append(MidiControl(beat, list(message), self.route[1], self.route[0]))

    def _finish_note(self, take, key, release):
        held = take.held.pop(key, None)
        if held:
            start, beat, pitch, pad, instrument, velocity, channel = held
            take.notes.append(
                Note(
                    pitch,
                    beat,
                    min(4096, max(1 / self.engine.sr, self.event_beat - start)),
                    velocity,
                    pad,
                    instrument,
                    channel,
                    release,
                )
            )

    def _on(self, destination, velocity):
        if isinstance(destination, int):
            note, pad, instrument, port, channel = destination, None, None, "", 0
        else:
            note, pad, instrument, port, channel = destination
        e = self.engine
        if pad is not None:
            e._spawn(e.project.pads[pad], pad, velocity, self.offset, note=note)
        elif e.external.instrument_for(instrument) is not None:
            e.external.note_on(
                note,
                velocity,
                self.offset,
                live=True,
                channel=channel,
                instrument_id=instrument,
            )
        else:
            e._spawn_synth(
                note,
                velocity,
                self.offset,
                instrument_id=instrument,
                midi_channel=channel,
                midi_owner=port,
            )
        self.notifications.append(("note", note, True))

    def _off(self, destination):
        if isinstance(destination, int):
            note, pad, instrument, port, channel = destination, None, None, "", 0
        else:
            note, pad, instrument, port, channel = destination
        e = self.engine
        if pad is not None:
            e.sample_note_off(pad, note)
        elif e.external.instrument_for(instrument) is not None:
            e.external.note_off(
                note,
                instrument_id=instrument,
                channel=channel,
                offset=self.offset,
            )
        else:
            e._release_synth(note, instrument, midi_owner=port, midi_channel=channel)
        self.notifications.append(("note", note, False))

    def _pad_on(self, pad, velocity):
        self.engine._spawn(self.engine.project.pads[pad], pad, velocity, self.offset)
        if self.engine.recording and self.engine.playing and self.engine.mode == "pattern":
            self.engine._record(pad, velocity)

    def _pad_off(self, pad):
        self.engine.release_pad(pad)

    def _expression(self, message):
        instrument_id = self.route[1]
        if not self.engine.external.queue_event(message, self.offset, instrument_id=instrument_id):
            self.engine.external.queue_event(message, self.offset, instrument_id=None)
        control = MidiControl(0, list(message), self.route[1], self.route[0])
        apply_expression(
            [
                voice
                for voice in self.engine.synth_voices
                if voice.midi_owner == self.router.port_id
            ],
            control,
        )

    def _current_value(self, target):
        if target == "master":
            return self.engine.project.master * 127
        if target.startswith("track:"):
            return self.engine.project.tracks[int(target.split(":")[1])].gain * 127
        return None

    def _control(self, target, value):
        e = self.engine
        if target == "play":
            e.play()
        elif target == "stop":
            e.stop_transport(rewind=False)
        elif target == "master":
            e.project.master = value / 127
        elif target.startswith("track:"):
            index = int(target.split(":")[1])
            if 0 <= index < len(e.project.tracks):
                e.project.tracks[index].gain = value / 127
        elif target in ("bank_next", "bank_previous"):
            self.route = (
                *self.route[:2],
                (self.route[2] + (1 if target == "bank_next" else -1)) % 4,
            )
        self.notifications.append(("control", target, value))
