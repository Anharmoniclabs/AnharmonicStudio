"""Shared first-party DSP lifecycle contracts.

This module deliberately contains no GUI or device code. It gives first-party
processors one vocabulary for preparation, reset, processing, latency, tail,
and state persistence before existing effects are migrated incrementally.
"""

from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Protocol, runtime_checkable

import numpy as np


@dataclass(frozen=True)
class ProcessorSpec:
    id: str
    name: str
    version: int = 1
    channels: int = 2

    def __post_init__(self) -> None:
        if not self.id or not self.name:
            raise ValueError("processor id and name must be non-empty")
        if self.version <= 0:
            raise ValueError("processor version must be positive")
        if self.channels <= 0:
            raise ValueError("processor channel count must be positive")


@runtime_checkable
class AudioProcessor(Protocol):
    spec: ProcessorSpec

    def prepare(self, sample_rate: float, max_block_size: int, channels: int = 2) -> None: ...

    def reset(self) -> None: ...

    def process(self, block: np.ndarray) -> None: ...

    def latency_samples(self) -> int: ...

    def tail_samples(self) -> int: ...

    def save_state(self) -> dict: ...

    def restore_state(self, state: dict) -> None: ...


def validate_audio_block(block: np.ndarray, *, channels: int | None = None) -> np.ndarray:
    if not isinstance(block, np.ndarray):
        raise TypeError("audio block must be a numpy array")
    if block.ndim != 2:
        raise ValueError("audio block must be frame-major [frames, channels]")
    if channels is not None and block.shape[1] != channels:
        raise ValueError(f"audio block must have {channels} channels")
    if block.dtype not in (np.float32, np.float64):
        raise TypeError("audio block must use float32 or float64")
    if not np.isfinite(block).all():
        raise ValueError("audio block contains NaN or Inf")
    return block


def validate_prepare(
    sample_rate: float, max_block_size: int, channels: int
) -> tuple[float, int, int]:
    sample_rate = float(sample_rate)
    max_block_size = int(max_block_size)
    channels = int(channels)
    if not math.isfinite(sample_rate) or sample_rate <= 0:
        raise ValueError("sample_rate must be finite and positive")
    if max_block_size <= 0:
        raise ValueError("max_block_size must be positive")
    if channels <= 0:
        raise ValueError("channels must be positive")
    return sample_rate, max_block_size, channels


def contract_errors(processor) -> tuple[str, ...]:
    required = (
        "prepare",
        "reset",
        "process",
        "latency_samples",
        "tail_samples",
        "save_state",
        "restore_state",
    )
    errors = []
    if not isinstance(getattr(processor, "spec", None), ProcessorSpec):
        errors.append("missing ProcessorSpec")
    for name in required:
        if not callable(getattr(processor, name, None)):
            errors.append(f"missing callable {name}")
    return tuple(errors)
