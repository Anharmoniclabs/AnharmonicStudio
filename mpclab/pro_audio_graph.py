"""Pro-DAW graph infrastructure beyond the legacy stereo mixer surface."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Callable

import numpy as np

MAX_CHANNELS = 8
MAX_BUSES = 64


class Float64Accumulator:
    """Preallocated high-precision summing boundary with explicit float32 output."""

    def __init__(self, frames: int, channels: int = 2):
        self.channels = int(channels)
        self.buffer = np.zeros((max(1, int(frames)), self.channels), dtype=np.float64)

    def prepare(self, frames: int) -> None:
        if frames > len(self.buffer):
            self.buffer = np.zeros((frames, self.channels), dtype=np.float64)

    def clear(self, frames: int) -> np.ndarray:
        self.prepare(frames)
        target = self.buffer[:frames]
        target.fill(0.0)
        return target

    def add(self, block: np.ndarray, gain: float = 1.0) -> None:
        frames = len(block)
        self.prepare(frames)
        if block.ndim != 2 or block.shape[1] != self.channels:
            raise ValueError("summing block channel count does not match")
        if gain == 1.0:
            np.add(self.buffer[:frames], block, out=self.buffer[:frames], casting="unsafe")
        else:
            self.buffer[:frames] += block * float(gain)

    def to_float32(self, out: np.ndarray) -> np.ndarray:
        frames = len(out)
        if out.shape != (frames, self.channels) or out.dtype != np.float32:
            raise ValueError("float64 summing output must be matching float32 audio")
        np.copyto(out, self.buffer[:frames], casting="unsafe")
        return out


class MultichannelTrackStore:
    """Fixed-capacity 1/2/4/6/8-channel track buffers for future sources/buses."""

    def __init__(self, track_ids, channels: dict[str, int], frames: int):
        self.track_ids = tuple(track_ids)
        self.channels = {track_id: int(channels.get(track_id, 2)) for track_id in self.track_ids}
        if any(count not in (1, 2, 4, 6, 8) for count in self.channels.values()):
            raise ValueError("multichannel tracks support 1, 2, 4, 6 or 8 channels")
        self.max_channels = max(self.channels.values(), default=2)
        self.buffers = np.zeros(
            (len(self.track_ids), max(1, int(frames)), self.max_channels), dtype=np.float32
        )
        self.index = {track_id: i for i, track_id in enumerate(self.track_ids)}

    def prepare(self, frames: int) -> None:
        if frames <= self.buffers.shape[1]:
            return
        self.buffers = np.zeros((len(self.track_ids), frames, self.max_channels), dtype=np.float32)

    def clear(self, frames: int) -> None:
        self.prepare(frames)
        self.buffers[:, :frames].fill(0.0)

    def block(self, track_id: str, frames: int) -> np.ndarray:
        index = self.index[track_id]
        count = self.channels[track_id]
        return self.buffers[index, :frames, :count]

    def ingest_stereo(self, track_id: str, stereo: np.ndarray) -> np.ndarray:
        destination = self.block(track_id, len(stereo))
        destination.fill(0.0)
        if destination.shape[1] == 1:
            destination[:, 0] = stereo.mean(axis=1)
        else:
            destination[:, :2] = stereo[:, :2]
        return destination


class HardwarePatchbay:
    """Map named buses/channels to an arbitrary host-output channel count."""

    def __init__(self, mapping: dict[str, list[int]], max_output_channels: int = 128):
        self.max_output_channels = int(max_output_channels)
        self.mapping = {}
        for source, channels in mapping.items():
            if not isinstance(source, str) or not source:
                raise ValueError("patchbay source must be named")
            if not isinstance(channels, list) or any(
                type(channel) is not int or not 0 <= channel < self.max_output_channels
                for channel in channels
            ):
                raise ValueError("patchbay hardware channels are invalid")
            self.mapping[source] = tuple(channels)

    def required_outputs(self) -> int:
        return (
            max((channel for channels in self.mapping.values() for channel in channels), default=1)
            + 1
        )

    def render(self, sources: dict[str, np.ndarray], output: np.ndarray) -> None:
        output.fill(0.0)
        for name, channels in self.mapping.items():
            source = sources.get(name)
            if source is None:
                continue
            if source.ndim != 2 or len(source) != len(output):
                raise ValueError("patchbay source has the wrong shape")
            for source_channel, output_channel in enumerate(channels):
                if source_channel >= source.shape[1] or output_channel >= output.shape[1]:
                    break
                output[:, output_channel] += source[:, source_channel]


@dataclass(frozen=True, slots=True)
class GraphNode:
    id: str
    dependencies: tuple[str, ...]
    process: Callable[[object], object]


class ParallelGraphScheduler:
    """Deterministic dependency scheduler; parallelism is opt-in and bounded."""

    def __init__(self, workers: int = 0):
        self.workers = max(0, min(16, int(workers)))

    @staticmethod
    def layers(nodes: list[GraphNode]) -> tuple[tuple[GraphNode, ...], ...]:
        by_id = {node.id: node for node in nodes}
        if len(by_id) != len(nodes):
            raise ValueError("graph node IDs must be unique")
        remaining = set(by_id)
        completed = set()
        layers = []
        while remaining:
            ready = sorted(
                (
                    by_id[node_id]
                    for node_id in remaining
                    if set(by_id[node_id].dependencies) <= completed
                ),
                key=lambda node: node.id,
            )
            if not ready:
                raise ValueError("DSP graph contains a cycle or missing dependency")
            layers.append(tuple(ready))
            for node in ready:
                remaining.remove(node.id)
                completed.add(node.id)
        return tuple(layers)

    def run(self, nodes: list[GraphNode], state: dict[str, object]) -> dict[str, object]:
        for layer in self.layers(nodes):
            if self.workers <= 1 or len(layer) <= 1:
                for node in layer:
                    state[node.id] = node.process(state)
                continue
            # Intended for offline/native/plugin work that releases the GIL.
            with ThreadPoolExecutor(max_workers=min(self.workers, len(layer))) as pool:
                futures = {node.id: pool.submit(node.process, state) for node in layer}
                for node_id in sorted(futures):
                    state[node_id] = futures[node_id].result()
        return state


class ProAudioGraph:
    def __init__(self, project, blocksize: int):
        state = getattr(project, "daw_expansion", {})
        self.precision = state.get("summation_precision", "float32")
        self.track_store = MultichannelTrackStore(
            (track.id for track in project.tracks),
            state.get("track_channels", {}),
            blocksize,
        )
        self.patchbay = HardwarePatchbay(state.get("hardware_patchbay", {}))
        self.scheduler = ParallelGraphScheduler(state.get("parallel_workers", 0))
        self.accumulator = Float64Accumulator(blocksize, 2)

    def prepare(self, frames: int) -> None:
        self.track_store.prepare(frames)
        self.accumulator.prepare(frames)
