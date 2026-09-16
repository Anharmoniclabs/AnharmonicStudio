"""External instrument and master-effect routing shared by playback and export."""

from __future__ import annotations

from contextlib import ExitStack
from dataclasses import dataclass


def prism_tempo_events(plugin, bpm):
    """Return the Prism MIDI CC protocol for a tempo update."""
    if getattr(plugin, "info", {}).get("name") != "Anharmonic Prism":
        return []
    if type(bpm) not in (int, float) or not 20 <= bpm <= 400:
        raise ValueError("Prism tempo must be between 20 and 400 BPM")
    value = round(float(bpm) * 100)
    return [
        ([0xBF, 99, 125], 0),
        ([0xBF, 98, 80 + (value >> 14)], 0),
        ([0xBF, 6, (value >> 7) & 127], 0),
        ([0xBF, 38, value & 127], 0),
    ]


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

    def note_on(
        self,
        note,
        velocity,
        offset=0,
        gate=None,
        live=True,
        channel=None,
        event_source=None,
        trigger_id=None,
    ):
        channel = (0 if live else 1) if channel is None else channel
        onset = [0x90 | channel, note, max(1, min(127, round(velocity * 127)))]
        voice = ExternalNote(
            self,
            note,
            channel,
            event_source=event_source,
            trigger_id=trigger_id,
            onset=onset,
        )
        self.voices.append(voice)
        self.events.append((onset, offset))
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
        # Prism handles CC123 itself. Recreating its processor here stalls the
        # live worker long enough to overflow the audio queue.
        self.reset_requested = (
            getattr(self.instrument, "info", {}).get("name") != "Anharmonic Prism"
        )

    def render_instrument(self, destination, frames, sample_rate, bpm=120, parameters=None):
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
        midi = prism_tempo_events(instrument, bpm) + [
            (message, offset / sample_rate)
            for message, offset in sorted(self.events, key=lambda item: item[1])
        ]
        self.events.clear()
        options = {"parameters": parameters} if parameters else {}
        output = instrument.render(
            None,
            frames,
            midi,
            reset=self.reset_requested,
            **options,
        )
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
    def __init__(self, specifications, sample_rate, synth_events, bpm=120):
        from .plugin_host import IsolatedPlugin, PluginError

        self.stack = ExitStack()
        self.sample_rate = sample_rate
        self.bpm = bpm
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

    def render_instrument(self, destination, start, frames, parameters=None):
        if self.instrument is None:
            return
        midi = prism_tempo_events(self.instrument, self.bpm)
        while self.index < len(self.events) and self.events[self.index][0] < start + frames:
            at, message = self.events[self.index]
            midi.append((message, max(0, at - start) / self.sample_rate))
            self.index += 1
        options = {"parameters": parameters} if parameters else {}
        output = self.instrument.render(None, frames, midi, **options)
        if output is not None:
            destination += output

    def render_effect(self, block):
        if self.effect is not None:
            output = self.effect.render(block, len(block))
            if output is not None:
                block[:] = output

    def close(self):
        self.stack.close()
