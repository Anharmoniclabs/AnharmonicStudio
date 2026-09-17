"""Install shared delay/filter primitives under existing realtime effects."""

from __future__ import annotations

import math
import numpy as np

from .delay_line import BlockDelayLine
from .filter_primitives import biquad, cascade_ir, cascade_response

_INSTALLED = False
_BASE_COMBS = (1214, 1293, 1390, 1476, 1548, 1623, 1695, 1760)
_BASE_ALLPASS = (605, 480, 371, 245)
_BASE_SPREAD = 25


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


def _next_pow2(value: int) -> int:
    return 1 << max(0, int(value - 1).bit_length())


def configure_fx_sample_rate(sample_rate: int) -> None:
    """Retune first-party mixer DSP to the active project clock domain."""
    from . import fx

    rate = int(sample_rate)
    if rate not in (44_100, 48_000, 88_200, 96_000):
        raise ValueError("unsupported project sample rate")
    scale = rate / 48_000.0
    fx.SR = rate
    fx.TONE_TAPS = max(256, round(1536 * scale))
    fx.RESPONSE_FFT = _next_pow2(max(4096, round(4096 * scale)))
    fx.ReverbSend.COMBS = tuple(max(1, round(value * scale)) for value in _BASE_COMBS)
    fx.ReverbSend.ALLPASS = tuple(max(1, round(value * scale)) for value in _BASE_ALLPASS)
    fx.ReverbSend.SPREAD = max(1, round(_BASE_SPREAD * scale))

    def dynamic_biquad(kind, freq, q, gain_db=0.0, sr=None):
        return biquad(kind, freq, q, gain_db, rate if sr is None else int(sr))

    def dynamic_response(sections, freqs, sr=None):
        return cascade_response(sections, freqs, rate if sr is None else int(sr))

    def dynamic_ir(sections, taps=None):
        return cascade_ir(
            sections,
            fx.TONE_TAPS if taps is None else int(taps),
            sr=rate,
            response_fft=fx.RESPONSE_FFT,
        )

    def coefficient(seconds: float, sr=None) -> float:
        clock = rate if sr is None else float(sr)
        return float(
            min(max(math.exp(-1.0 / max(1e-5, float(seconds) * clock)), 0.0), 0.999999)
        )

    fx._biquad = dynamic_biquad
    fx.cascade_response = dynamic_response
    fx.cascade_ir = dynamic_ir
    fx.OnePole.coefficient = staticmethod(coefficient)


def install_fx_unification() -> None:
    """Redirect existing effect classes without changing their public API."""
    global _INSTALLED
    if _INSTALLED:
        return
    from . import fx
    from . import plugin_chain_runtime

    fx.DelayLine = BlockDelayLine
    plugin_chain_runtime._StereoDelay = StereoEdgeDelay
    configure_fx_sample_rate(fx.SR)
    _INSTALLED = True
