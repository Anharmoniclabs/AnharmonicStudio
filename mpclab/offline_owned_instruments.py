"""Offline stable-ID instruments: third-party isolated plugins plus first-party engines."""

from __future__ import annotations

from contextlib import ExitStack

import numpy as np

from .expansion_instruments import ExpansionInstrumentBridge
from .plugin_host import IsolatedPlugin, PluginError
from .plugin_latency import (
    PluginDelayCompensator,
    StereoDelayCompensator,
    plugin_path_latency_samples,
)


class OfflineOwnedInstruments:
    """One offline processor per stable project instrument identity."""

    def __init__(self, engine, project, synth_events, control_events):
        self.engine = engine
        self.project = project
        self.sample_rate = int(engine.sr)
        self.bpm = float(project.bpm)
        self.stack = ExitStack()
        self.plugins: dict[str, object] = {}
        self.events: dict[str, list[tuple[int, list[int]]]] = {}
        self.indices: dict[str, int] = {}
        self.delays: dict[str, StereoDelayCompensator] = {}
        self.max_latency = 0
        self.dry_pdc = PluginDelayCompensator(len(project.tracks), engine.blocksize)
        try:
            self._open_processors()
            self._compile_events(synth_events, control_events)
            self._configure_latency()
        except Exception:
            self.stack.close()
            raise

    def _open_processors(self) -> None:
        expansion = getattr(self.project, "daw_expansion", {})
        engines = expansion.get("instrument_engines", {}) if isinstance(expansion, dict) else {}
        external = getattr(self.project, "instrument_plugins", {})
        by_id = {instrument.id: instrument for instrument in self.project.instruments}
        for instrument_id, instrument in by_id.items():
            spec = external.get(instrument_id)
            if isinstance(spec, dict) and not spec.get("bypass", False):
                plugin = IsolatedPlugin(spec, self.sample_rate)
                if not plugin.info.get("instrument"):
                    plugin.close()
                    raise PluginError(f"Saved plugin for {instrument.name} is not an instrument")
                self.stack.callback(plugin.close)
                self.plugins[instrument_id] = plugin
                continue
            config = engines.get(instrument_id)
            if not isinstance(config, dict) or not config.get("enabled", True):
                continue
            if config.get("type") == "multisample":
                for region in config.get("state", {}).get("regions", []):
                    for key in ("sample_id", "release_sample_id"):
                        sample_id = region.get(key)
                        if sample_id:
                            self.engine.lib.audio(sample_id)
            plugin = ExpansionInstrumentBridge(
                config["type"], config.get("state", {}), self.sample_rate, self.engine.lib
            )
            self.stack.callback(plugin.close)
            self.plugins[instrument_id] = plugin

    def _compile_events(self, synth_events, control_events) -> None:
        event_map = {instrument_id: [] for instrument_id in self.plugins}
        for at, pitch, velocity, gate, instrument_id, channel, _sequence_id in synth_events:
            if instrument_id not in event_map:
                continue
            velocity_value = max(1, min(127, round(float(velocity) * 127)))
            event_map[instrument_id].append(
                (int(at), [0x90 | int(channel), int(pitch), velocity_value])
            )
            event_map[instrument_id].append(
                (int(at) + max(1, int(gate)), [0x80 | int(channel), int(pitch), 0])
            )
        for at, control in control_events:
            instrument_id = getattr(control, "instrument", None)
            if instrument_id in event_map and getattr(control, "pad", None) is None:
                event_map[instrument_id].append((int(at), list(control.message)))
        for instrument_id, events in event_map.items():
            events.sort(key=lambda item: (item[0], item[1][0]))
            self.events[instrument_id] = events
            self.indices[instrument_id] = 0

    def _configure_latency(self) -> None:
        latencies = {
            instrument_id: plugin_path_latency_samples(plugin, include_live_bridge=False)
            for instrument_id, plugin in self.plugins.items()
        }
        maximum = max(latencies.values(), default=0)
        self.max_latency = maximum
        self.dry_pdc.configure(maximum, self.engine.blocksize)
        for instrument_id, latency in latencies.items():
            delay = StereoDelayCompensator(self.engine.blocksize)
            delay.configure(maximum - latency, self.engine.blocksize)
            self.delays[instrument_id] = delay

    def process_dry(self, tracks: np.ndarray, frames: int) -> None:
        if self.max_latency:
            self.dry_pdc.process(tracks, frames)

    def render(self, instrument_id: str, destination: np.ndarray, start: int, frames: int) -> None:
        plugin = self.plugins.get(instrument_id)
        if plugin is None:
            return
        events = self.events[instrument_id]
        index = self.indices[instrument_id]
        midi = []
        end = start + frames
        while index < len(events) and events[index][0] < end:
            at, message = events[index]
            midi.append((message, max(0, at - start) / self.sample_rate))
            index += 1
        self.indices[instrument_id] = index
        output = plugin.render(None, frames, midi)
        if output is None:
            return
        block = np.asarray(output, dtype=np.float32)
        if block.shape != (frames, 2) or not np.isfinite(block).all():
            raise PluginError("Owned instrument returned invalid offline audio")
        delay = self.delays.get(instrument_id)
        if delay is not None:
            delay.process(block, frames)
        np.add(destination, block, out=destination)

    def close(self) -> None:
        self.stack.close()
