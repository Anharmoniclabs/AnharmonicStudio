"""Reference delay-line primitives for effects and physical-modeling DSP."""

from __future__ import annotations

import math

import numpy as np


class DelayLine:
    """Preallocated integer/fractional delay with linear interpolation."""

    def __init__(self, max_delay_samples: int, channels: int = 2, *, dtype=np.float32):
        if type(max_delay_samples) is not int or max_delay_samples < 1:
            raise ValueError("max_delay_samples must be a positive integer")
        if type(channels) is not int or channels < 1:
            raise ValueError("channels must be a positive integer")
        self.max_delay_samples = max_delay_samples
        self.channels = channels
        self.capacity = max_delay_samples + 2
        self.buffer = np.zeros((self.capacity, channels), dtype=dtype)
        self.write_index = 0

    def clear(self) -> None:
        self.buffer.fill(0)
        self.write_index = 0

    def _validate_delay(self, delay_samples: float) -> float:
        if not math.isfinite(delay_samples):
            raise ValueError("delay must be finite")
        delay = float(delay_samples)
        if not 1.0 <= delay <= self.max_delay_samples:
            raise ValueError("delay must be between 1 and max_delay_samples")
        return delay

    def read(self, delay_samples: float) -> np.ndarray:
        """Read a frame ``delay_samples`` behind the next write position."""
        delay = self._validate_delay(delay_samples)
        whole = int(math.floor(delay))
        fraction = delay - whole
        recent = self.buffer[(self.write_index - whole) % self.capacity]
        if fraction == 0.0:
            return recent.copy()
        older = self.buffer[(self.write_index - whole - 1) % self.capacity]
        return recent * (1.0 - fraction) + older * fraction

    def write(self, frame) -> None:
        values = np.asarray(frame, dtype=self.buffer.dtype)
        if values.shape != (self.channels,):
            raise ValueError(f"frame must have shape ({self.channels},)")
        self.buffer[self.write_index] = values
        self.write_index = (self.write_index + 1) % self.capacity

    def process(
        self,
        block: np.ndarray,
        delay_samples: float | np.ndarray,
        *,
        feedback: float = 0.0,
        out: np.ndarray | None = None,
    ) -> np.ndarray:
        """Render delayed audio while feeding input plus bounded feedback into the line."""
        data = np.asarray(block, dtype=self.buffer.dtype)
        if data.ndim != 2 or data.shape[1] != self.channels:
            raise ValueError("block must be frames-by-channels")
        if not math.isfinite(feedback) or abs(feedback) >= 1.0:
            raise ValueError("feedback must be finite and have magnitude below 1")
        if out is None:
            out = np.empty_like(data)
        if out.shape != data.shape:
            raise ValueError("out must match the input block shape")
        if np.isscalar(delay_samples):
            delays = None
            scalar_delay = self._validate_delay(float(delay_samples))
        else:
            delays = np.asarray(delay_samples, dtype=np.float64)
            if delays.ndim != 1 or len(delays) != len(data):
                raise ValueError("delay array must contain one value per frame")
            scalar_delay = 0.0
        for index, frame in enumerate(data):
            delay = scalar_delay if delays is None else self._validate_delay(float(delays[index]))
            delayed = self.read(delay)
            out[index] = delayed
            self.write(frame + delayed * feedback)
        return out
