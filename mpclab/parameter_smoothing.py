"""Reusable bounded parameter smoothing for realtime DSP control paths."""

from __future__ import annotations

import math

import numpy as np

from .native_dsp import NATIVE


def smoothing_frames(sample_rate: float, time_ms: float) -> int:
    """Return the number of frames in a smoothing interval."""
    if not math.isfinite(sample_rate) or sample_rate <= 0:
        raise ValueError("sample_rate must be positive and finite")
    if not math.isfinite(time_ms):
        raise ValueError("time_ms must be finite")
    return max(0, round(sample_rate * max(0.0, time_ms) / 1000.0))


def _native_destination(out: np.ndarray) -> bool:
    return (
        NATIVE is not None
        and out.dtype == np.float32
        and out.flags.c_contiguous
        and out.flags.writeable
    )


class LinearSmoother:
    """Sample-accurate linear ramp with an exact, bounded target."""

    def __init__(self, value: float, sample_rate: float, time_ms: float = 10.0):
        if not math.isfinite(value):
            raise ValueError("value must be finite")
        self.sample_rate = float(sample_rate)
        self.time_ms = float(time_ms)
        smoothing_frames(self.sample_rate, self.time_ms)
        self.current = float(value)
        self.target = float(value)
        self.remaining = 0
        self.step = 0.0
        self._native_state = np.empty(4, dtype=np.float64)

    def reset(self, value: float) -> None:
        if not math.isfinite(value):
            raise ValueError("value must be finite")
        self.current = self.target = float(value)
        self.remaining = 0
        self.step = 0.0

    def set_target(self, value: float, *, time_ms: float | None = None) -> None:
        if not math.isfinite(value):
            raise ValueError("target must be finite")
        frames = smoothing_frames(
            self.sample_rate,
            self.time_ms if time_ms is None else float(time_ms),
        )
        self.target = float(value)
        if frames == 0 or self.target == self.current:
            self.current = self.target
            self.remaining = 0
            self.step = 0.0
            return
        self.remaining = frames
        self.step = (self.target - self.current) / frames

    def next_value(self) -> float:
        if self.remaining <= 0:
            return self.current
        self.current += self.step
        self.remaining -= 1
        if self.remaining == 0:
            self.current = self.target
            self.step = 0.0
        return self.current

    def fill(self, out: np.ndarray) -> np.ndarray:
        """Fill a one-dimensional destination without callback-time allocation."""
        if out.ndim != 1:
            raise ValueError("out must be one-dimensional")
        if not len(out):
            return out
        if _native_destination(out):
            state = self._native_state
            state[:] = self.current, self.target, self.step, self.remaining
            NATIVE.control_linear(out, state)
            self.current = float(state[0])
            self.target = float(state[1])
            self.step = float(state[2])
            self.remaining = int(state[3])
            return out
        for index in range(len(out)):
            out[index] = self.next_value()
        return out

    def process(self, frames: int, *, dtype=np.float32) -> np.ndarray:
        if frames < 0:
            raise ValueError("frames must be non-negative")
        return self.fill(np.empty(frames, dtype=dtype))


class OnePoleSmoother:
    """One-pole smoother whose ``time_ms`` is one exponential time constant."""

    def __init__(self, value: float, sample_rate: float, time_ms: float = 10.0):
        if not math.isfinite(value):
            raise ValueError("value must be finite")
        if not math.isfinite(sample_rate) or sample_rate <= 0:
            raise ValueError("sample_rate must be positive and finite")
        if not math.isfinite(time_ms):
            raise ValueError("time_ms must be finite")
        self.sample_rate = float(sample_rate)
        self.time_ms = max(0.0, float(time_ms))
        self.current = float(value)
        self.target = float(value)
        self._coefficient = self._make_coefficient(self.time_ms)
        self._native_state = np.empty(3, dtype=np.float64)

    def _make_coefficient(self, time_ms: float) -> float:
        frames = self.sample_rate * max(0.0, time_ms) / 1000.0
        return 0.0 if frames <= 0.0 else math.exp(-1.0 / frames)

    def reset(self, value: float) -> None:
        if not math.isfinite(value):
            raise ValueError("value must be finite")
        self.current = self.target = float(value)

    def set_target(self, value: float, *, time_ms: float | None = None) -> None:
        if not math.isfinite(value):
            raise ValueError("target must be finite")
        if time_ms is not None:
            if not math.isfinite(time_ms):
                raise ValueError("time_ms must be finite")
            self.time_ms = max(0.0, float(time_ms))
            self._coefficient = self._make_coefficient(self.time_ms)
        self.target = float(value)
        if self._coefficient == 0.0:
            self.current = self.target

    def next_value(self) -> float:
        if self._coefficient == 0.0:
            return self.current
        self.current = self.target + self._coefficient * (self.current - self.target)
        return self.current

    def fill(self, out: np.ndarray) -> np.ndarray:
        if out.ndim != 1:
            raise ValueError("out must be one-dimensional")
        if not len(out):
            return out
        if _native_destination(out):
            state = self._native_state
            state[:] = self.current, self.target, self._coefficient
            NATIVE.control_onepole(out, state)
            self.current = float(state[0])
            return out
        for index in range(len(out)):
            out[index] = self.next_value()
        return out

    def process(self, frames: int, *, dtype=np.float32) -> np.ndarray:
        if frames < 0:
            raise ValueError("frames must be non-negative")
        return self.fill(np.empty(frames, dtype=dtype))
