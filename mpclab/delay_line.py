"""Shared delay-line primitives for effects and physical-modeling DSP."""

from __future__ import annotations

import math

import numpy as np


class BlockDelayLine:
    """Callback-safe integer block delay used by mixer sends and PDC.

    Reads and writes contiguous chunks from a preallocated ring without a
    Python loop per audio sample. This is the production primitive for static
    delays. Fractional/modulated delays use :class:`DelayLine` below.
    """

    def __init__(self, max_samples: int, channels: int = 2, *, dtype=np.float32):
        if type(max_samples) is not int or max_samples < 2:
            raise ValueError("max_samples must be an integer of at least 2")
        if type(channels) is not int or channels < 1:
            raise ValueError("channels must be a positive integer")
        self.size = int(max_samples)
        self.channels = int(channels)
        self.buf = np.zeros((self.size, self.channels), dtype=dtype)
        self.pos = 0

    def reset(self) -> None:
        self.buf.fill(0)
        self.pos = 0

    clear = reset

    def _copy_from_ring(self, start: int, n: int, out: np.ndarray) -> np.ndarray:
        start %= self.size
        first = min(n, self.size - start)
        out[:first] = self.buf[start : start + first]
        if first < n:
            out[first:n] = self.buf[: n - first]
        return out

    def read(self, n: int, delay: int, out: np.ndarray | None = None) -> np.ndarray:
        n = max(0, int(n))
        delay = int(min(max(int(delay), 1), self.size - 1))
        if n > self.size:
            raise ValueError("block delay read cannot exceed ring capacity")
        if out is None:
            out = np.empty((n, self.channels), dtype=self.buf.dtype)
        if out.shape != (n, self.channels):
            raise ValueError("block delay output has the wrong shape")
        return self._copy_from_ring(self.pos - delay, n, out)

    def write(self, block: np.ndarray) -> None:
        data = np.asarray(block, dtype=self.buf.dtype)
        if data.ndim != 2 or data.shape[1] != self.channels:
            raise ValueError("block must be frames-by-channels")
        n = len(data)
        if n > self.size:
            raise ValueError("block delay write cannot exceed ring capacity")
        start = self.pos % self.size
        first = min(n, self.size - start)
        self.buf[start : start + first] = data[:first]
        if first < n:
            self.buf[: n - first] = data[first:]
        self.pos += n


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

    reset = clear

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
        """Render a modulated/fractional delay reference path.

        This generic reference handles one delay value per frame and therefore
        iterates in Python. Realtime static delays must use BlockDelayLine; a
        future native fractional kernel can replace this method transparently.
        """
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
