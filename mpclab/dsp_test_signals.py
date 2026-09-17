"""Small deterministic signal generators for DSP regression tests and diagnostics."""

from __future__ import annotations

import math

import numpy as np


def _frames(value: int) -> int:
    if type(value) is not int or value < 0:
        raise ValueError("frames must be a non-negative integer")
    return value


def impulse(frames: int, amplitude: float = 1.0, *, dtype=np.float32) -> np.ndarray:
    out = np.zeros(_frames(frames), dtype=dtype)
    if frames:
        out[0] = amplitude
    return out


def dc(frames: int, level: float, *, dtype=np.float32) -> np.ndarray:
    return np.full(_frames(frames), level, dtype=dtype)


def step(frames: int, at: int, level: float = 1.0, *, dtype=np.float32) -> np.ndarray:
    frames = _frames(frames)
    if type(at) is not int or not 0 <= at <= frames:
        raise ValueError("step index must be within the signal")
    out = np.zeros(frames, dtype=dtype)
    out[at:] = level
    return out


def sine(
    frames: int,
    frequency: float,
    sample_rate: float,
    amplitude: float = 1.0,
    phase: float = 0.0,
    *,
    dtype=np.float32,
) -> np.ndarray:
    frames = _frames(frames)
    if not all(math.isfinite(value) for value in (frequency, sample_rate, amplitude, phase)):
        raise ValueError("sine parameters must be finite")
    if sample_rate <= 0 or frequency < 0 or frequency > sample_rate / 2:
        raise ValueError("frequency must be within the Nyquist range")
    index = np.arange(frames, dtype=np.float64)
    signal = amplitude * np.sin(2.0 * np.pi * frequency * index / sample_rate + phase)
    return signal.astype(dtype)


def nyquist(frames: int, amplitude: float = 1.0, *, dtype=np.float32) -> np.ndarray:
    frames = _frames(frames)
    return (amplitude * np.where(np.arange(frames) % 2 == 0, 1.0, -1.0)).astype(dtype)


def noise(
    frames: int,
    seed: int | None = None,
    amplitude: float = 1.0,
    *,
    dtype=np.float32,
) -> np.ndarray:
    frames = _frames(frames)
    if not math.isfinite(amplitude) or amplitude < 0:
        raise ValueError("amplitude must be finite and non-negative")
    return np.random.default_rng(seed).uniform(-amplitude, amplitude, frames).astype(dtype)
