"""External instrument and master-effect routing shared by playback and export."""

from __future__ import annotations

from contextlib import ExitStack


class _OwnedNote(list):
    """Internal MIDI event carrying its live/sequence ownership until rendering."""

    def __init__(self, message, *, live, token=None, gated_end=False):
        super().__init__(message)
        self.live = live
        self.token = token
        self.gated_end = gated_end


class ExternalDSP:
    def __init__(self):
        self.instrument = None
        self.effect = None
        self.events = []
        self.ends = []
        self.reset_requested = False
        self.instances = {}
        self.mix_buffers = {}
        self.mix_delays = {}
        self._mix_paths = (self.mix_buffers, self.mix_delays)
        self._mix_plugins = {}
        self.live_notes = set()
        self._note_owners = {}
        self._note_owner_plugin = None
        self._note_serial = 0

    def route(self, instrument_id=None):
        return self if instrument_id is None else self.instances.get(instrument_id)

    def ensure_route(self, instrument_id=None):
        if instrument_id is None:
            return self
        if instrument_id not in self.instances:
            self.instances = {**self.instances, instrument_id: ExternalDSP()}
        return self.instances[instrument_id]

    def routes(self):
        return [(None, self), *self.instances.items()]

    def prepare_mix(self, frames):
        """Allocate independent alignment paths off the audio callback."""
        import numpy as np
        from .plugin_latency import PluginDelayCompensator, plugin_path_latency_samples

        paths = [
            (identity, route) for identity, route in self.routes() if route.instrument is not None
        ]
        latencies = {
            identity: plugin_path_latency_samples(route.instrument) for identity, route in paths
        }
        maximum = max(latencies.values(), default=0)
        buffers, delays = {}, {}
        for identity, route in paths:
            delay = self.mix_delays.get(identity)
            if (
                self._mix_plugins.get(identity) is route.instrument
                and delay is not None
                and delay.blocksize == frames
                and delay.delay_samples == maximum - latencies[identity]
            ):
                buffers[identity] = self.mix_buffers[identity]
            else:
                buffers[identity] = np.zeros((frames, 2), np.float32)
                delay = PluginDelayCompensator(1, frames)
                delay.configure(maximum - latencies[identity])
            delays[identity] = delay
        self.mix_buffers, self.mix_delays = buffers, delays
        self._mix_plugins = {identity: route.instrument for identity, route in paths}
        self._mix_paths = (buffers, delays)
        return maximum

    def render_tracks(self, project, tracks, frames, sample_rate, dry_delay=None):
        buffers, delays = self._mix_paths
        if dry_delay is not None and dry_delay.delay_samples > 0:
            dry_delay.process(tracks, frames)
        for identity, route in self.routes():
            if route.instrument is None:
                continue
            # Project replacement can precede disposal of an old plugin bridge.
            if identity is not None and not any(
                item.id == identity for item in project.instruments
            ):
                continue
            track = project.instrument_patch(identity).track
            scratch = buffers.get(identity)
            if scratch is None or len(scratch) < frames:
                # Legacy callers without routing extensions retain direct rendering.
                route.render_instrument(tracks[track], frames, sample_rate)
                continue
            scratch = scratch[:frames]
            scratch.fill(0)
            route.render_instrument(scratch, frames, sample_rate)
            delays[identity].process(scratch[None, :, :], frames)
            tracks[track] += scratch

    def note_on(self, note, velocity, offset=0, gate=None, live=True, channel=None):
        channel = (0 if live else 1) if channel is None else channel
        if live:
            self.live_notes.add((channel, note))
        self._note_serial += 1
        token = (live, self._note_serial if gate is not None or not live else 0)
        self.events.append(
            (
                _OwnedNote(
                    [0x90 | channel, note, max(1, min(127, round(velocity * 127)))],
                    live=live,
                    token=token,
                ),
                offset,
            )
        )
        if gate is not None:
            self.ends.append((offset + gate, channel, note, token))

    def note_off(self, note, channel=0, velocity=0, offset=0):
        self.live_notes.discard((channel, note))
        self.events.append((_OwnedNote([0x80 | channel, note, velocity], live=True), offset))

    def release_live(self):
        for channel, note in self.live_notes:
            self.events.append((_OwnedNote([0x80 | channel, note, 0], live=True), 0))
        self.live_notes.clear()

    def panic(self, *, include_instances=True):
        self.events = [([0xB0 | channel, 123, 0], 0) for channel in range(16)]
        self.ends.clear()
        self.live_notes.clear()
        self._note_owners.clear()
        self.reset_requested = True
        if include_instances:
            for route in self.instances.values():
                route.panic()

    def render_instrument(self, destination, frames, sample_rate):
        instrument = self.instrument
        if instrument is not self._note_owner_plugin:
            self._note_owners.clear()
            self._note_owner_plugin = instrument
        if instrument is None:
            self.events.clear()
            self.ends.clear()
            return
        future = []
        for remaining, channel, note, token in self.ends:
            if remaining < frames:
                self.events.append(
                    (
                        _OwnedNote(
                            [0x80 | channel, note, 0], live=token[0], token=token, gated_end=True
                        ),
                        max(0, remaining),
                    )
                )
            else:
                future.append((remaining - frames, channel, note, token))
        self.ends = future
        midi = []
        for message, offset in sorted(
            self.events, key=lambda item: (item[1], -int(getattr(item[0], "gated_end", False)))
        ):
            if isinstance(message, _OwnedNote):
                key = (message[0] & 15, message[1])
                owners = self._note_owners.setdefault(key, set())
                previously_active = bool(owners)
                note_on = message[0] & 0xF0 == 0x90
                retrigger = note_on and owners == {message.token}
                if note_on:
                    owners.add(message.token)
                elif message.token is not None:
                    owners.discard(message.token)
                else:
                    owners.difference_update([owner for owner in owners if owner[0]])
                active = bool(owners)
                if not active:
                    self._note_owners.pop(key, None)
                # MIDI 1 has no per-note ID: keep a shared pitch sounding until
                # both the live input and every sequenced gate have released it.
                if active == previously_active and not retrigger:
                    continue
            elif message[0] & 0xF0 == 0xB0 and message[1] in (120, 123):
                channel = message[0] & 15
                for key in tuple(self._note_owners):
                    if key[0] == channel:
                        self._note_owners.pop(key)
            midi.append((list(message), offset / sample_rate))
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
        instances, self.instances = self.instances, {}
        for route in instances.values():
            route.close()
        self.mix_buffers, self.mix_delays = {}, {}
        self._mix_paths = (self.mix_buffers, self.mix_delays)
        self._mix_plugins.clear()
        self.live_notes.clear()
        self._note_owners.clear()
        self._note_owner_plugin = None
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
        self._note_owners = {}
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
                for token, event in enumerate(synth_events):
                    at, note, velocity, gate = event[:4]
                    channel = event[4] if len(event) > 4 else 1
                    self.events.append(
                        (
                            at,
                            _OwnedNote(
                                [0x90 | channel, note, max(1, min(127, round(velocity * 127)))],
                                live=False,
                                token=token,
                            ),
                        )
                    )
                    self.events.append(
                        (at + gate, _OwnedNote([0x80 | channel, note, 0], live=False, token=token))
                    )
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
            self.index += 1
            if isinstance(message, _OwnedNote):
                key = (message[0] & 15, message[1])
                owners = self._note_owners.setdefault(key, set())
                previous = bool(owners)
                if message[0] & 0xF0 == 0x90:
                    owners.add(message.token)
                else:
                    owners.discard(message.token)
                active = bool(owners)
                if not active:
                    self._note_owners.pop(key, None)
                if active == previous:
                    continue
            elif message[0] & 0xF0 == 0xB0 and message[1] in (120, 123):
                channel = message[0] & 15
                for key in tuple(self._note_owners):
                    if key[0] == channel:
                        self._note_owners.pop(key)
            midi.append((list(message), max(0, at - start) / self.sample_rate))
        destination += self.instrument.render(None, frames, midi)

    def render_effect(self, block):
        if self.effect is not None:
            block[:] = self.effect.render(block, len(block))

    def close(self):
        self.stack.close()
