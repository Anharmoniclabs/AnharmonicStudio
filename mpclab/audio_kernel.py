"""Small real-time-safe primitives for the master audio path."""

from __future__ import annotations

import numpy as np


AUDIO_SAMPLE_RATE = 48_000
ULTRA_LOW_LATENCY_BLOCKSIZE = 128
LOW_LATENCY_BLOCKSIZE = 256
BALANCED_BLOCKSIZE = 512
BLUETOOTH_SAFE_BLOCKSIZE = 1_024
DEFAULT_BLOCKSIZE = BALANCED_BLOCKSIZE
AUDIO_BUFFER_PROFILES = (
    ("EXPERIMENTAL · LIGHT", ULTRA_LOW_LATENCY_BLOCKSIZE),
    ("PRODUCTION", LOW_LATENCY_BLOCKSIZE),
    ("BUILD / MIX SAFE", BALANCED_BLOCKSIZE),
    ("SAFE / BT", BLUETOOTH_SAFE_BLOCKSIZE),
)
MASTER_CEILING_DB = -1.0


class MasteringKernel:
    """Stateful stereo-linked peak limiter with AAC-safe headroom.

    The old engine normalized each PortAudio block independently.  A transient
    near a block edge could therefore make the gain jump at the next callback.
    This kernel attacks immediately, releases continuously across callbacks and
    uses the same gain for both channels so the stereo image cannot wander.

    Scratch arrays are retained and reused; processing does not allocate after
    the largest requested block has been prepared.
    """

    def __init__(
        self,
        sample_rate: int,
        ceiling_db: float = MASTER_CEILING_DB,
        release_seconds: float = 0.080,
        blocksize: int = 0,
    ):
        self.sample_rate = max(1, int(sample_rate))
        self.ceiling = float(10.0 ** (ceiling_db / 20.0))
        self.release_step = 1.0 / max(1.0, release_seconds * self.sample_rate)
        self.gain = 1.0
        self.gain_reduction_db = 0.0
        self._capacity = 0
        self._peak = np.empty(0, dtype=np.float32)
        self._target = np.empty(0, dtype=np.float32)
        self._ramp = np.empty(0, dtype=np.float32)
        if blocksize:
            self.prepare(blocksize)

    def prepare(self, frames: int) -> None:
        frames = max(0, int(frames))
        if frames <= self._capacity:
            return
        self._capacity = frames
        self._peak = np.empty(frames, dtype=np.float32)
        self._target = np.empty(frames, dtype=np.float32)
        self._ramp = np.arange(frames, dtype=np.float32) * np.float32(self.release_step)

    def reset(self) -> None:
        self.gain = 1.0
        self.gain_reduction_db = 0.0

    def process(self, audio: np.ndarray) -> np.ndarray:
        """Limit a stereo float block in place and return it."""
        if audio.ndim != 2 or audio.shape[1] != 2:
            raise ValueError("mastering kernel expects (frames, 2) stereo audio")
        frames = len(audio)
        if not frames:
            return audio
        self.prepare(frames)
        np.nan_to_num(audio, copy=False, nan=0.0, posinf=0.0, neginf=0.0)

        peak = self._peak[:frames]
        target = self._target[:frames]
        ramp = self._ramp[:frames]
        np.abs(audio[:, 0], out=peak)
        np.abs(audio[:, 1], out=target)
        np.maximum(peak, target, out=peak)

        target.fill(1.0)
        np.divide(self.ceiling, peak, out=target, where=peak > self.ceiling)

        # Exact vector form of gain[n] = min(target[n], gain[n-1] + step).
        # It gives zero overshoot with a smooth, block-independent release.
        np.subtract(target, ramp, out=target)
        np.minimum.accumulate(target, out=target)
        np.minimum(target, self.gain + self.release_step, out=target)
        np.add(target, ramp, out=target)
        np.minimum(target, 1.0, out=target)

        self.gain = float(target[-1])
        minimum_gain = float(np.min(target))
        self.gain_reduction_db = float(-20.0 * np.log10(max(minimum_gain, 1e-9)))
        audio *= target[:, None]
        return audio
