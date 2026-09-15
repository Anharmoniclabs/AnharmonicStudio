"""External instrument and master-effect routing shared by playback and export."""

from __future__ import annotations

from contextlib import ExitStack
from dataclasses import dataclass


@dataclass(eq=False)
class ExternalNote:
    routing: object
    note: int
    channel: int
    event_source: object = None
    trigger_id: object = None
    dead: bool = False
    onset: object = None

    def note_off(self, _release=0):
        self.routing.release_voice(self)


class ExternalDSP:
    def __init__(self):
        self.instrument = None
        self.effect = None
        self.events = []
        self.ends = []
        self.voices = []
        self._gate_owners = {}
        self.reset_requested = False

    def note_on(self, note, velocity, offset=0, gate=None, live=True, channel=None):
        channel = (0 if live else 1) if channel is None else channel
        self.events.append(
            ([0x90 | channel, note, max(1, min(127, round(velocity * 127)))], offset)
        )
        if gate is not None:
            ending = (offset + gate, channel, note)
            self.ends.append(ending)
            self._gate_owners[id(ending)] = voice

    def _has_peer(self, voice):
        return any(
            not other.dead
            and other is not voice
            and other.note == voice.note
            and other.channel == voice.channel
            for other in self.voices
        )

    def release_voice(self, voice, offset=0):
        if voice.dead:
            return
        voice.dead = True
        self.events[:] = [event for event in self.events if event[0] is not voice.onset]
        kept = []
        for ending in self.ends:
            if self._gate_owners.get(id(ending)) is voice:
                self._gate_owners.pop(id(ending), None)
            else:
                kept.append(ending)
        self.ends = kept
        # MIDI1 cannot address an individual overlapping note on one channel.
        # Keep another event's gate intact instead of silencing its shared pitch.
        if not self._has_peer(voice):
            self.events.append(([0x80 | voice.channel, voice.note, 0], offset))

    def note_off(self, note):
        owned = [
            voice
            for voice in self.voices
            if voice.note == note and voice.channel == 0 and not voice.dead
        ]
        if not owned:
            self.events.append(([0x80, note, 0], 0))
        for voice in owned:
            self.release_voice(voice)

    def panic(self):
        self.events = [([0xB0 | channel, 123, 0], 0) for channel in range(16)]
        self.ends.clear()
        self.voices.clear()
        self._gate_owners.clear()
        self.reset_requested = True

    def render_instrument(self, destination, frames, sample_rate):
        instrument = self.instrument
        if instrument is None:
            self.events.clear()
            self.ends.clear()
            self.voices.clear()
            self._gate_owners.clear()
            return
        future = []
        for ending in sorted(self.ends, key=lambda item: item[0]):
            remaining, channel, note = ending
            voice = self._gate_owners.pop(id(ending), None)
            if voice is not None and voice.dead:
                continue
            if remaining < frames:
                if voice is not None:
                    voice.dead = True
                if voice is None or not self._has_peer(voice):
                    self.events.append(([0x80 | channel, note, 0], max(0, remaining)))
            else:
                next_ending = (remaining - frames, channel, note)
                future.append(next_ending)
                if voice is not None:
                    self._gate_owners[id(next_ending)] = voice
        self.ends = future
        midi = [
            (message, offset / sample_rate)
            for message, offset in sorted(self.events, key=lambda item: item[1])
        ]
        self.events.clear()
        output = instrument.render(None, frames, midi, reset=self.reset_requested)
        self.reset_requested = False
        self.voices[:] = [voice for voice in self.voices if not voice.dead]
        if output is not None:
            destination += output

    def render_effect(self, block):
        effect = self.effect
        if effect is not None:
            output = effect.render(block, len(block))
            if output is not None:
                block[:] = output

    def close(self):
        for name in ("instrument", "effect"):
            plugin = getattr(self, name)
            setattr(self, name, None)
            if plugin is not None:
                plugin.close()
        self.events.clear()
        self.ends.clear()
        self.voices.clear()
        self._gate_owners.clear()


class OfflinePlugins:
    def __init__(self, specifications, sample_rate, synth_events):
        from .plugin_host import IsolatedPlugin, PluginError

        self.stack = ExitStack()
        self.sample_rate = sample_rate
        self.instrument = self.effect = None
        self.events = []
        self.index = 0
        try:
            for slot, specification in specifications.items():
                if specification.get("bypass"):
                    continue
                plugin = IsolatedPlugin(specification, sample_rate)
                self.stack.callback(plugin.close)
                if not plugin.info.get(slot):
                    raise PluginError(f"Plugin is not an {slot}")
                setattr(self, slot, plugin)
            if self.instrument is not None:
                for event in synth_events:
                    at, note, velocity, gate = event[:4]
                    channel = event[4] if len(event) > 4 else 1
                    self.events.append(
                        (at, [0x90 | channel, note, max(1, min(127, round(velocity * 127)))])
                    )
                    self.events.append((at + gate, [0x80 | channel, note, 0]))
                self.events.sort(key=lambda item: (item[0], item[1][0]))
        except Exception:
            self.stack.close()
            raise

    def render_instrument(self, destination, start, frames):
        if self.instrument is None:
            return
        midi = []
        while self.index < len(self.events) and self.events[self.index][0] < start + frames:
            at, message = self.events[self.index]
            midi.append((message, max(0, at - start) / self.sample_rate))
            self.index += 1
        destination += self.instrument.render(None, frames, midi)

    def render_effect(self, block):
        if self.effect is not None:
            block[:] = self.effect.render(block, len(block))

    def close(self):
        self.stack.close()
