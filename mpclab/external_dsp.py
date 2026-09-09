"""External instrument and master-effect routing shared by playback and export."""

from __future__ import annotations

from contextlib import ExitStack


class ExternalDSP:
    def __init__(self):
        self.instrument = None
        self.effect = None
        self.events = []
        self.ends = []
        self.reset_requested = False

    def note_on(self, note, velocity, offset=0, gate=None, live=True):
        channel = 0 if live else 1
        self.events.append(
            ([0x90 | channel, note, max(1, min(127, round(velocity * 127)))], offset)
        )
        if gate is not None:
            self.ends.append((offset + gate, channel, note))

    def note_off(self, note):
        self.events.append(([0x80, note, 0], 0))

    def panic(self):
        self.events = [([0xB0 | channel, 123, 0], 0) for channel in range(16)]
        self.ends.clear()
        self.reset_requested = True

    def render_instrument(self, destination, frames, sample_rate):
        instrument = self.instrument
        if instrument is None:
            self.events.clear()
            self.ends.clear()
            return
        future = []
        for remaining, channel, note in self.ends:
            if remaining < frames:
                self.events.append(([0x80 | channel, note, 0], max(0, remaining)))
            else:
                future.append((remaining - frames, channel, note))
        self.ends = future
        midi = [
            (message, offset / sample_rate)
            for message, offset in sorted(self.events, key=lambda item: item[1])
        ]
        self.events.clear()
        output = instrument.render(None, frames, midi, reset=self.reset_requested)
        self.reset_requested = False
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
                for at, note, velocity, gate in synth_events:
                    self.events.append((at, [0x91, note, max(1, min(127, round(velocity * 127)))]))
                    self.events.append((at + gate, [0x81, note, 0]))
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
