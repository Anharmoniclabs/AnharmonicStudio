"""Bounded delay compensation for isolated plugin paths.

Live third-party plugins run two blocks behind the callback so the audio thread
never waits on arbitrary plugin code.  This class delays the non-plugin paths by
that exact bridge latency plus the plugin's own reported latency.  The ring is
allocated only when a plugin is loaded or the audio buffer changes, never by the
realtime processing call itself.
"""

from __future__ import annotations

import numpy as np

MAX_COMPENSATION_SAMPLES = 480_000  # ten seconds at 48 kHz


def plugin_path_latency_samples(plugin, *, include_live_bridge: bool = True) -> int:
    """Return intrinsic plugin latency plus the isolated live bridge when present."""
    if plugin is None:
        return 0
    info = getattr(plugin, "info", {})
    try:
        intrinsic = max(0, int(info.get("latency_samples", 0))) if isinstance(info, dict) else 0
    except (TypeError, ValueError):
        intrinsic = 0
    bridge = 0
    if include_live_bridge:
        try:
            blocksize = max(0, int(getattr(plugin, "blocksize", 0)))
        except (TypeError, ValueError):
            blocksize = 0
        bridge = 2 * blocksize
    return min(MAX_COMPENSATION_SAMPLES, intrinsic + bridge)


class PluginDelayCompensator:
    def __init__(self, tracks: int, blocksize: int):
        self.tracks = int(tracks)
        self.blocksize = int(blocksize)
        self.delay_samples = 0
        self.position = 0
        self.history = np.zeros((self.tracks, 1, 2), dtype=np.float32)
        self.output = np.zeros((self.tracks, self.blocksize, 2), dtype=np.float32)

    def configure(self, delay_samples: int, blocksize: int | None = None) -> None:
        if blocksize is not None:
            self.blocksize = max(1, int(blocksize))
        delay = max(0, min(MAX_COMPENSATION_SAMPLES, int(delay_samples)))
        self.delay_samples = delay
        self.position = 0
        self.history = np.zeros((self.tracks, max(1, delay), 2), dtype=np.float32)
        self.output = np.zeros((self.tracks, self.blocksize, 2), dtype=np.float32)

    def ensure_blocksize(self, frames: int) -> None:
        frames = int(frames)
        if frames <= self.output.shape[1]:
            return
        self.blocksize = frames
        self.output = np.zeros((self.tracks, frames, 2), dtype=np.float32)

    def reset(self) -> None:
        self.position = 0
        self.history.fill(0.0)
        self.output.fill(0.0)

    def process(self, tracks: np.ndarray, frames: int) -> None:
        """Delay all track audio in place by the configured sample count."""
        delay = self.delay_samples
        if delay <= 0 or frames <= 0:
            return
        self.ensure_blocksize(frames)
        source = tracks[:, :frames]
        destination = self.output[:, :frames]
        offset = 0
        position = self.position
        while offset < frames:
            take = min(frames - offset, delay - position)
            destination[:, offset : offset + take] = self.history[:, position : position + take]
            self.history[:, position : position + take] = source[:, offset : offset + take]
            offset += take
            position += take
            if position == delay:
                position = 0
        source[:] = destination
        self.position = position
