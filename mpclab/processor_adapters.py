"""Lifecycle adapters that make existing first-party DSP contract-addressable."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict, is_dataclass

from .dsp_contracts import ProcessorSpec, validate_audio_block, validate_prepare


def _state(value) -> dict:
    if is_dataclass(value):
        return asdict(value)
    if isinstance(value, dict):
        return deepcopy(value)
    return {
        key: deepcopy(item)
        for key, item in vars(value).items()
        if not key.startswith("_") and isinstance(item, (str, int, float, bool, type(None)))
    }


def _restore(target, state: dict) -> None:
    if not isinstance(state, dict):
        raise ValueError("processor state must be an object")
    for key, value in state.items():
        if hasattr(target, key) and not key.startswith("_"):
            setattr(target, key, deepcopy(value))


class TrackProcessor:
    spec = ProcessorSpec("track-chain", "Track tone / drive / compressor")

    def __init__(self, chain, settings):
        self.chain = chain
        self.settings = settings
        self.sample_rate = 48_000.0
        self.blocksize = 0
        self.channels = getattr(chain, "channels", 2)

    def prepare(self, sample_rate: float, max_block_size: int, channels: int = 2) -> None:
        self.sample_rate, self.blocksize, self.channels = validate_prepare(
            sample_rate, max_block_size, channels
        )
        prepared = self.chain.prepare_tone(self.settings, channels, max_block_size)
        self.chain.install_tone(prepared)

    def reset(self) -> None:
        self.chain.reset()

    def process(self, block) -> None:
        validate_audio_block(block, channels=self.channels)
        self.chain.process(block, self.settings, prepared_only=True)

    def latency_samples(self) -> int:
        return 0

    def tail_samples(self) -> int:
        return max(0, round(self.sample_rate)) if getattr(self.settings, "active", False) else 0

    def save_state(self) -> dict:
        return _state(self.settings)

    def restore_state(self, state: dict) -> None:
        _restore(self.settings, state)


class MasterProcessor:
    spec = ProcessorSpec("master-chain", "Master tone / drive / glue")

    def __init__(self, chain, settings):
        self.chain = chain
        self.settings = settings
        self.sample_rate = 48_000.0
        self.blocksize = 0
        self.channels = getattr(chain, "channels", 2)

    def prepare(self, sample_rate: float, max_block_size: int, channels: int = 2) -> None:
        self.sample_rate, self.blocksize, self.channels = validate_prepare(
            sample_rate, max_block_size, channels
        )
        prepared = self.chain.prepare_tone(self.settings, channels, max_block_size)
        self.chain.install_tone(prepared)

    def reset(self) -> None:
        self.chain.reset()

    def process(self, block) -> None:
        validate_audio_block(block, channels=self.channels)
        self.chain.process(block, self.settings, prepared_only=True)

    def latency_samples(self) -> int:
        return 0

    def tail_samples(self) -> int:
        return max(0, round(self.sample_rate * 0.5)) if getattr(self.settings, "active", False) else 0

    def save_state(self) -> dict:
        return _state(self.settings)

    def restore_state(self, state: dict) -> None:
        _restore(self.settings, state)


class DelayProcessor:
    spec = ProcessorSpec("delay-send", "Tempo delay send")

    def __init__(self, processor, settings, bpm=120.0):
        self.processor = processor
        self.settings = settings
        self.bpm = float(bpm)
        self.sample_rate = 48_000.0
        self.blocksize = 0
        self.channels = 2

    def prepare(self, sample_rate: float, max_block_size: int, channels: int = 2) -> None:
        self.sample_rate, self.blocksize, self.channels = validate_prepare(
            sample_rate, max_block_size, channels
        )

    def reset(self) -> None:
        self.processor.reset()

    def process(self, block) -> None:
        validate_audio_block(block, channels=self.channels)
        block[:] = self.processor.process(block, self.settings, self.bpm)

    def latency_samples(self) -> int:
        return 0

    def tail_samples(self) -> int:
        feedback = min(max(float(getattr(self.settings, "feedback", 0.0)), 0.0), 0.95)
        if not getattr(self.settings, "enabled", False) or feedback <= 0:
            return 0
        # Conservative bounded tail estimate; engine already caps live send tails.
        return min(round(self.sample_rate * 30), round(self.sample_rate * (2.0 / (1.0 - feedback))))

    def save_state(self) -> dict:
        return _state(self.settings)

    def restore_state(self, state: dict) -> None:
        _restore(self.settings, state)


class ReverbProcessor:
    spec = ProcessorSpec("reverb-send", "Algorithmic reverb send")

    def __init__(self, processor, settings):
        self.processor = processor
        self.settings = settings
        self.sample_rate = 48_000.0
        self.blocksize = 0
        self.channels = 2

    def prepare(self, sample_rate: float, max_block_size: int, channels: int = 2) -> None:
        self.sample_rate, self.blocksize, self.channels = validate_prepare(
            sample_rate, max_block_size, channels
        )
        self.processor.prepare(max_block_size)

    def reset(self) -> None:
        self.processor.reset()

    def process(self, block) -> None:
        validate_audio_block(block, channels=self.channels)
        block[:] = self.processor.process(block, self.settings)

    def latency_samples(self) -> int:
        return 0

    def tail_samples(self) -> int:
        if not getattr(self.settings, "enabled", False):
            return 0
        size = min(max(float(getattr(self.settings, "size", 0.5)), 0.0), 1.0)
        return round(self.sample_rate * (1.0 + 7.0 * size))

    def save_state(self) -> dict:
        return _state(self.settings)

    def restore_state(self, state: dict) -> None:
        _restore(self.settings, state)


def build_processor_graph(rack, project, sample_rate: int, blocksize: int) -> dict[str, object]:
    """Return contract processors backed by the exact objects used by MixRack."""
    graph = {
        f"track:{track.id}": TrackProcessor(rack.tracks[index], track.fx)
        for index, track in enumerate(project.tracks)
    }
    graph["send:delay"] = DelayProcessor(rack.delay, project.delay_fx, project.bpm)
    graph["send:reverb"] = ReverbProcessor(rack.reverb, project.reverb_fx)
    graph["master"] = MasterProcessor(rack.master, project.master_fx)
    for processor in graph.values():
        processor.prepare(sample_rate, blocksize, rack.channels)
    return graph
