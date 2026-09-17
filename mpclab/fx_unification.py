"""Install shared delay/filter primitives under existing realtime effects."""

from __future__ import annotations

import numpy as np

from .delay_line import BlockDelayLine
from .filter_primitives import biquad, cascade_ir, cascade_response

_INSTALLED = False


class StereoEdgeDelay:
    """PDC edge delay backed by the same block-delay primitive as effects."""

    def __init__(self, delay: int, blocksize: int):
        self.delay = max(1, int(delay))
        self.line = BlockDelayLine(self.delay + max(2, int(blocksize)) + 2, 2)
        self.output = np.zeros((max(1, int(blocksize)), 2), dtype=np.float32)

    @property
    def history(self):
        return self.line.buf

    def ensure_blocksize(self, frames: int) -> None:
        if frames > len(self.output):
            self.output = np.zeros((frames, 2), dtype=np.float32)

    def process(self, source: np.ndarray) -> np.ndarray:
        frames = len(source)
        self.ensure_blocksize(frames)
        out = self.output[:frames]
        self.line.read(frames, self.delay, out)
        self.line.write(source)
        return out

    def reset(self) -> None:
        self.line.reset()
        self.output.fill(0.0)


def install_fx_unification() -> None:
    """Redirect existing effect classes without changing their public API."""
    global _INSTALLED
    if _INSTALLED:
        return
    from . import fx
    from . import plugin_chain_runtime

    # DelaySend, ReverbSend and Allpass resolve this module global when their
    # instances are created, so replacing it before Engine construction makes
    # the shared callback-safe ring the real production implementation.
    fx.DelayLine = BlockDelayLine
    fx._biquad = biquad
    fx.cascade_response = cascade_response
    fx.cascade_ir = cascade_ir
    plugin_chain_runtime._StereoDelay = StereoEdgeDelay
    _INSTALLED = True
