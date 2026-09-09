"""Mix effects: per-track tone, drive and compression, plus delay and reverb sends.

Everything here runs inside the PortAudio callback, so nothing may loop over
samples in Python.  Two tricks make that possible:

* **Any IIR becomes a short FIR.**  A biquad cascade's impulse response is
  taken from its frequency response with one inverse FFT, then convolved with
  the block by zero-latency overlap-add.  Knob moves rebuild the response on
  the GUI thread; the audio thread only ever multiplies spectra.  The same
  response doubles as the curve the mixer draws.
* **One-poles use compiled recurrences.** If native DSP is unavailable,
  `y[n] = a·x[n] + b·y[n-1]` is solved as convolution with `a·bⁿ` plus
  `bⁿ⁺¹·y[-1]`. Both paths retain state across callbacks without truncation.

Delay and reverb are ring buffers read a block at a time.  A line longer than
the block never reads what the same block is about to write, so those need no
loop either; the short diffusion allpasses are the one exception and step
through their own delay length instead.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .native_dsp import NATIVE

SR = 48_000
TONE_TAPS = 1_536  # 32 ms of impulse response — plenty for shelves,
RESPONSE_FFT = 4_096  # bells and a resonant sweep, and cheap to convolve


# ── biquad design (RBJ cookbook) ─────────────────────────────
def _biquad(
    kind: str, freq: float, q: float, gain_db: float = 0.0, sr: int = SR
) -> tuple[np.ndarray, np.ndarray]:
    freq = float(np.clip(freq, 20.0, sr * 0.45))
    q = max(0.05, float(q))
    w = 2.0 * np.pi * freq / sr
    cos_w, sin_w = np.cos(w), np.sin(w)
    alpha = sin_w / (2.0 * q)
    amp = 10.0 ** (gain_db / 40.0)

    if kind == "lowpass":
        b = np.array([(1 - cos_w) / 2, 1 - cos_w, (1 - cos_w) / 2])
        a = np.array([1 + alpha, -2 * cos_w, 1 - alpha])
    elif kind == "highpass":
        b = np.array([(1 + cos_w) / 2, -(1 + cos_w), (1 + cos_w) / 2])
        a = np.array([1 + alpha, -2 * cos_w, 1 - alpha])
    elif kind == "peak":
        b = np.array([1 + alpha * amp, -2 * cos_w, 1 - alpha * amp])
        a = np.array([1 + alpha / amp, -2 * cos_w, 1 - alpha / amp])
    elif kind == "lowshelf":
        s = 2.0 * np.sqrt(amp) * alpha
        b = np.array(
            [
                amp * ((amp + 1) - (amp - 1) * cos_w + s),
                2 * amp * ((amp - 1) - (amp + 1) * cos_w),
                amp * ((amp + 1) - (amp - 1) * cos_w - s),
            ]
        )
        a = np.array(
            [
                (amp + 1) + (amp - 1) * cos_w + s,
                -2 * ((amp - 1) + (amp + 1) * cos_w),
                (amp + 1) + (amp - 1) * cos_w - s,
            ]
        )
    elif kind == "highshelf":
        s = 2.0 * np.sqrt(amp) * alpha
        b = np.array(
            [
                amp * ((amp + 1) + (amp - 1) * cos_w + s),
                -2 * amp * ((amp - 1) + (amp + 1) * cos_w),
                amp * ((amp + 1) + (amp - 1) * cos_w - s),
            ]
        )
        a = np.array(
            [
                (amp + 1) - (amp - 1) * cos_w + s,
                2 * ((amp - 1) - (amp + 1) * cos_w),
                (amp + 1) - (amp - 1) * cos_w - s,
            ]
        )
    else:
        raise ValueError(f"unknown biquad kind: {kind}")
    return b / a[0], a / a[0]


def cascade_response(sections, freqs: np.ndarray, sr: int = SR) -> np.ndarray:
    """Complex frequency response of a biquad cascade at `freqs` hertz."""
    z = np.exp(-2j * np.pi * np.asarray(freqs, dtype=float) / sr)
    h = np.ones(len(z), dtype=complex)
    for b, a in sections:
        h *= (b[0] + b[1] * z + b[2] * z * z) / (a[0] + a[1] * z + a[2] * z * z)
    return h


def cascade_ir(sections, taps: int = TONE_TAPS) -> np.ndarray:
    """Impulse response of a biquad cascade, taken through the spectrum.

    Sampling H(e^jw) on a grid and inverting gives the *circular* response, so
    anything ringing past `RESPONSE_FFT` folds back onto the head.  At 4096
    points that is 85 ms — far longer than these filters ring — and the tail
    fade below keeps a truncated resonance from ending on a step.
    """
    if not sections:
        ir = np.zeros(taps, dtype=np.float32)
        ir[0] = 1.0
        return ir
    grid = np.fft.rfftfreq(RESPONSE_FFT, 1.0 / SR)
    ir = np.fft.irfft(cascade_response(sections, grid), RESPONSE_FFT)[:taps]
    fade = max(8, taps // 8)
    ir[-fade:] *= np.linspace(1.0, 0.0, fade)
    return np.ascontiguousarray(ir, dtype=np.float32)


def _next_pow2(n: int) -> int:
    return 1 << int(np.ceil(np.log2(max(1, n))))


# ── zero-latency block convolution ───────────────────────────
class BlockConvolver:
    """Overlap-add FIR with no added latency and no per-sample work.

    The first output sample of a block is the first sample of the convolution,
    so the mixer stays as tight as it was before the effect was inserted.
    """

    def __init__(self, ir: np.ndarray, channels: int = 2):
        self.channels = channels
        self._plans: dict[int, np.ndarray] = {}
        self.set_ir(ir)

    def set_ir(self, ir: np.ndarray) -> None:
        self.ir = np.ascontiguousarray(ir, dtype=np.float32)
        self.m = len(self.ir)
        self._plans.clear()
        self.tail = np.zeros((max(1, self.m - 1), self.channels), dtype=np.float32)

    def reset(self) -> None:
        self.tail.fill(0.0)

    def _spectrum(self, size: int) -> np.ndarray:
        h = self._plans.get(size)
        if h is None:
            h = np.fft.rfft(self.ir, size)
            self._plans[size] = h
        return h

    def prepare(self, frames: int) -> None:
        """Prepare every FFT size used by full blocks and loop-boundary slices."""
        frames = max(0, int(frames))
        if frames:
            size = _next_pow2(self.m)
            maximum = _next_pow2(frames + self.m - 1)
            while size <= maximum:
                self._spectrum(size)
                size *= 2

    def process(self, block: np.ndarray, *, prepared_only: bool = False) -> np.ndarray:
        n = len(block)
        if n == 0:
            return block
        size = _next_pow2(n + self.m - 1)
        spec = self._plans.get(size) if prepared_only else self._spectrum(size)
        if spec is None:
            # A fixed PortAudio stream should always use its configured block
            # size. If a backend surprises us, bypass one block instead of
            # creating a filter FFT on the real-time thread.
            return block
        full = np.fft.irfft(np.fft.rfft(block, size, axis=0) * spec[:, None], size, axis=0)[
            : n + self.m - 1
        ]
        overlap = min(n, self.m - 1)
        full[:overlap] += self.tail[:overlap]
        block[:] = full[:n]
        carry = full[n : n + self.m - 1]
        tail = np.zeros_like(self.tail)
        tail[: len(carry)] = carry
        if self.m - 1 > n:
            tail[: self.m - 1 - n] += self.tail[n:]
        self.tail = tail
        return block


@dataclass(frozen=True)
class PreparedTone:
    """A tone kernel whose expensive work is complete before queue transfer.

    The convolver is deliberately owned by this value until the audio thread
    installs it. Its overlap tail is mutable after installation, but no GUI
    code retains or edits that state.
    """

    key: tuple
    sections: tuple
    convolver: BlockConvolver | None
    blocksize: int


def _prepare_tone(key: tuple, sections: list, channels: int, blocksize: int) -> PreparedTone:
    frozen_sections = tuple(sections)
    convolver = None
    if frozen_sections:
        convolver = BlockConvolver(cascade_ir(frozen_sections), channels)
        convolver.prepare(blocksize)
    return PreparedTone(key, frozen_sections, convolver, int(blocksize))


# ── exact vectorised one-pole ────────────────────────────────
class OnePole:
    """`y[n] = (1-b)·x[n] + b·y[n-1]`, solved for a whole block at once."""

    def __init__(self, channels: int = 2):
        self.channels = channels
        self.state = np.zeros(channels, dtype=np.float32)
        self._cache: dict[tuple[float, int], tuple[np.ndarray, np.ndarray]] = {}

    def reset(self) -> None:
        self.state.fill(0.0)

    @staticmethod
    def coefficient(seconds: float, sr: int = SR) -> float:
        """Pole for a time constant, clamped away from 0 and 1."""
        return float(np.clip(np.exp(-1.0 / max(1e-5, seconds * sr)), 0.0, 0.999999))

    def _plan(self, b: float, n: int):
        key = (round(b, 7), n)
        plan = self._cache.get(key)
        if plan is None:
            powers = b ** np.arange(n, dtype=np.float64)
            kernel = ((1.0 - b) * powers).astype(np.float32)
            size = _next_pow2(2 * n)
            plan = (np.fft.rfft(kernel, size), (powers * b).astype(np.float32))
            self._cache[key] = plan
            if len(self._cache) > 24:  # knob sweeps, not growth
                self._cache.pop(next(iter(self._cache)))
        return plan

    def process(self, block: np.ndarray, b: float) -> np.ndarray:
        n = len(block)
        if n == 0:
            return block
        b = float(np.clip(b, 0.0, 0.999999))
        if (
            NATIVE is not None
            and block.dtype == np.float32
            and self.state.dtype == np.float32
            and block.flags.c_contiguous
        ):
            NATIVE.onepole(block, self.state, b)
            return block
        spec, decay = self._plan(b, n)
        size = (len(spec) - 1) * 2
        conv = np.fft.irfft(np.fft.rfft(block, size, axis=0) * spec[:, None], size, axis=0)[:n]
        block[:] = conv + decay[:, None] * self.state[None, :]
        self.state = block[-1].copy()
        return block


# ── delay lines ──────────────────────────────────────────────
class DelayLine:
    def __init__(self, max_samples: int, channels: int = 2):
        self.size = max(2, int(max_samples))
        self.buf = np.zeros((self.size, channels), dtype=np.float32)
        self.pos = 0

    def reset(self) -> None:
        self.buf.fill(0.0)
        self.pos = 0

    def _slice(self, start: int, n: int) -> np.ndarray:
        start %= self.size
        end = start + n
        if end <= self.size:
            return self.buf[start:end]
        return np.concatenate((self.buf[start:], self.buf[: end - self.size]))

    def read(self, n: int, delay: int) -> np.ndarray:
        """The n samples that were written `delay` samples ago."""
        delay = int(np.clip(delay, 1, self.size - 1))
        return self._slice(self.pos - delay, n).copy()

    def write(self, block: np.ndarray) -> None:
        n = len(block)
        start = self.pos % self.size
        end = start + n
        if end <= self.size:
            self.buf[start:end] = block
        else:
            split = self.size - start
            self.buf[start:] = block[:split]
            self.buf[: end - self.size] = block[split:]
        self.pos += n


class Allpass:
    """Freeverb diffusion stage.  Steps in chunks of its own delay length."""

    def __init__(self, delay: int, feedback: float = 0.5, channels: int = 2):
        self.delay = max(1, int(delay))
        self.feedback = float(feedback)
        self.line = DelayLine(self.delay + 4, channels)

    def reset(self) -> None:
        self.line.reset()

    def process(self, block: np.ndarray) -> np.ndarray:
        n, at = len(block), 0
        while at < n:
            take = min(self.delay, n - at)
            chunk = block[at : at + take]
            stored = self.line.read(take, self.delay)
            self.line.write(chunk + stored * self.feedback)
            block[at : at + take] = stored - chunk
            at += take
        return block


# ── dynamics ─────────────────────────────────────────────────
class Compressor:
    """Feed-forward peak compressor with a soft knee and a decoupled detector."""

    KNEE_DB = 6.0

    def __init__(self, channels: int = 2):
        self.fast = OnePole(1)
        self.slow = OnePole(1)
        self.gain_reduction_db = 0.0

    def reset(self) -> None:
        self.fast.reset()
        self.slow.reset()
        self.gain_reduction_db = 0.0

    def process(
        self,
        block: np.ndarray,
        threshold_db: float,
        ratio: float,
        attack: float,
        release: float,
        makeup_db: float = 0.0,
    ) -> np.ndarray:
        n = len(block)
        if n == 0:
            return block
        rect = np.max(np.abs(block), axis=1)[:, None]
        # Decoupled: the slow branch holds the peak up so release governs how
        # the gain comes back, while attack alone decides how fast it clamps.
        held = np.maximum(rect, self.slow.process(rect.copy(), OnePole.coefficient(release)))
        env = self.fast.process(held, OnePole.coefficient(attack))

        level = 20.0 * np.log10(np.maximum(env[:, 0], 1e-7))
        over = level - threshold_db
        knee = self.KNEE_DB
        slope = 1.0 - 1.0 / max(1.0, ratio)
        reduction = np.where(
            over <= -knee / 2,
            0.0,
            np.where(over >= knee / 2, slope * over, slope * (over + knee / 2) ** 2 / (2 * knee)),
        )
        self.gain_reduction_db = float(reduction.max()) if n else 0.0
        gain = 10.0 ** ((makeup_db - reduction) / 20.0)
        block *= gain[:, None].astype(np.float32)
        return block


def saturate(block: np.ndarray, drive: float) -> np.ndarray:
    """Asymmetry-free tanh drive with unity make-up, so the knob only adds grit."""
    if drive <= 1e-4:
        return block
    pre = 1.0 + drive * 11.0
    np.tanh(block * pre, out=block)
    # Normalising by tanh(pre) keeps full scale where it was; the second term
    # trades back the level a hard drive would otherwise add.
    block *= np.float32((1.0 - 0.45 * drive) / np.tanh(pre))
    return block


# ── per-track chain ──────────────────────────────────────────
class TrackChain:
    """TONE → DRIVE → COMP for one mixer track, plus its two send levels."""

    def __init__(self, channels: int = 2):
        self.channels = channels
        self.tone: BlockConvolver | None = None
        self.comp = Compressor(channels)
        self._tone_key: tuple | None = None
        self.sections: tuple = ()

    def reset(self) -> None:
        if self.tone is not None:
            self.tone.reset()
        self.comp.reset()

    @staticmethod
    def tone_key(fx) -> tuple:
        return (
            round(fx.low, 3),
            round(fx.mid, 3),
            round(fx.high, 3),
            round(fx.mid_freq, 1),
            fx.filter_type,
            round(fx.cutoff, 2),
            round(fx.resonance, 3),
        )

    @staticmethod
    def tone_sections(fx) -> list:
        sections = []
        if abs(fx.low) > 0.05:
            sections.append(_biquad("lowshelf", 120.0, 0.7, fx.low))
        if abs(fx.mid) > 0.05:
            sections.append(_biquad("peak", fx.mid_freq, 0.9, fx.mid))
        if abs(fx.high) > 0.05:
            sections.append(_biquad("highshelf", 6_000.0, 0.7, fx.high))
        if fx.filter_type == "lowpass" and fx.cutoff < 19_000.0:
            q = 0.707 + fx.resonance * 7.0
            sections.append(_biquad("lowpass", fx.cutoff, q))
        elif fx.filter_type == "highpass" and fx.cutoff > 22.0:
            q = 0.707 + fx.resonance * 7.0
            sections.append(_biquad("highpass", fx.cutoff, q))
        return sections

    @classmethod
    def prepare_tone(cls, fx, channels: int = 2, blocksize: int = 0) -> PreparedTone:
        return _prepare_tone(cls.tone_key(fx), cls.tone_sections(fx), channels, blocksize)

    def install_tone(self, prepared: PreparedTone) -> None:
        """Adopt a fully built kernel; intended as a callback queue command."""
        self._tone_key = prepared.key
        self.sections = prepared.sections
        self.tone = prepared.convolver

    def _sync_tone(self, fx) -> bool:
        """Lazy preparation for offline/direct callers, never the live engine."""
        key = self.tone_key(fx)
        if key != self._tone_key:
            self.install_tone(self.prepare_tone(fx, self.channels))
        return bool(self.sections)

    def process(self, block: np.ndarray, fx, *, prepared_only: bool = False) -> np.ndarray:
        has_tone = bool(self.sections) if prepared_only else self._sync_tone(fx)
        if has_tone and self.tone is not None:
            self.tone.process(block, prepared_only=prepared_only)
        saturate(block, fx.drive)
        if fx.comp:
            self.comp.process(block, fx.threshold, fx.ratio, fx.attack, fx.release, fx.makeup)
        return block


# ── sends ────────────────────────────────────────────────────
class DelaySend:
    """Tempo-synced stereo delay with damped feedback and optional ping-pong."""

    DIVISIONS = {"1/4": 1.0, "1/8.": 0.75, "1/8": 0.5, "1/8T": 1 / 3, "1/16": 0.25, "1/16T": 1 / 6}

    def __init__(self, channels: int = 2):
        self.line = DelayLine(int(SR * 4.0), channels)
        self.damp = OnePole(channels)

    def reset(self) -> None:
        self.line.reset()
        self.damp.reset()

    def delay_samples(self, fx, bpm: float, block: int) -> int:
        beats = self.DIVISIONS.get(fx.sync, 0.5)
        seconds = beats * 60.0 / max(20.0, bpm)
        # A line shorter than the block would have to read what it is about to
        # write.  Musical divisions never get that short; clamping is a guard.
        return int(np.clip(seconds * SR, max(block, 64), self.line.size - 2))

    def process(self, send: np.ndarray, fx, bpm: float) -> np.ndarray:
        n = len(send)
        delayed = self.line.read(n, self.delay_samples(fx, bpm, n))
        fed = self.damp.process(delayed.copy(), 0.15 + 0.8 * fx.damping)
        fed *= np.float32(np.clip(fx.feedback, 0.0, 0.95))
        if fx.ping_pong:
            fed = fed[:, ::-1].copy()
        self.line.write(send + fed)
        return delayed * np.float32(fx.level)


class ReverbSend:
    """Freeverb topology: eight damped combs into four diffusion allpasses."""

    COMBS = (1214, 1293, 1390, 1476, 1548, 1623, 1695, 1760)
    ALLPASS = (605, 480, 371, 245)
    SPREAD = 25

    def __init__(self, channels: int = 2):
        self.predelay = DelayLine(int(SR * 0.25), channels)
        self.combs = [DelayLine(d + self.SPREAD + 4, channels) for d in self.COMBS]
        self.damps = [OnePole(channels) for _ in self.COMBS]
        self.allpass = [Allpass(d, 0.5, channels) for d in self.ALLPASS]
        self._acc = np.zeros((0, channels), dtype=np.float32)

    def reset(self) -> None:
        self.predelay.reset()
        for line in self.combs:
            line.reset()
        for damp in self.damps:
            damp.reset()
        for ap in self.allpass:
            ap.reset()

    def prepare(self, frames: int) -> None:
        """Size reusable wet-bus scratch before real-time processing starts."""
        frames = max(0, int(frames))
        if len(self._acc) < frames:
            self._acc = np.zeros((frames, 2), dtype=np.float32)

    def process(self, send: np.ndarray, fx) -> np.ndarray:
        n = len(send)
        if len(self._acc) < n:
            self._acc = np.zeros((n, send.shape[1]), dtype=np.float32)
        wet = self._acc[:n]
        wet.fill(0.0)

        pre = max(1, int(fx.predelay * SR))
        self.predelay.write(send)
        source = self.predelay.read(n, pre) if pre > n else send
        source = source * np.float32(0.11)

        feedback = np.float32(0.70 + 0.28 * np.clip(fx.size, 0.0, 1.0))
        damping = 0.05 + 0.9 * float(np.clip(fx.damping, 0.0, 1.0))
        for i, (line, damp) in enumerate(zip(self.combs, self.damps, strict=True)):
            delay = self.COMBS[i]
            # One channel runs a few samples longer so the two sides decorrelate.
            left = line.read(n, delay)
            right = line.read(n, delay + self.SPREAD)
            out = np.empty_like(left)
            out[:, 0] = left[:, 0]
            out[:, 1] = right[:, 1]
            wet += out
            line.write(source + damp.process(out.copy(), damping) * feedback)

        for ap in self.allpass:
            ap.process(wet)

        width = float(np.clip(fx.width, 0.0, 1.0))
        if width < 0.999:
            mid = (wet[:, 0] + wet[:, 1]) * 0.5
            wet[:, 0] = mid + (wet[:, 0] - mid) * width
            wet[:, 1] = mid + (wet[:, 1] - mid) * width
        return wet * np.float32(fx.level)


# ── master bus ───────────────────────────────────────────────
class MasterChain:
    """Broad tone shaping and a slow glue compressor, before the limiter."""

    def __init__(self, channels: int = 2):
        self.channels = channels
        self.tone: BlockConvolver | None = None
        self.glue = Compressor(channels)
        self._tone_key: tuple | None = None
        self.sections: tuple = ()

    def reset(self) -> None:
        if self.tone is not None:
            self.tone.reset()
        self.glue.reset()

    @staticmethod
    def tone_key(fx) -> tuple:
        return (round(fx.low, 3), round(fx.mid, 3), round(fx.high, 3))

    @staticmethod
    def tone_sections(fx) -> list:
        sections = []
        if abs(fx.low) > 0.05:
            sections.append(_biquad("lowshelf", 110.0, 0.7, fx.low))
        if abs(fx.mid) > 0.05:
            sections.append(_biquad("peak", 1_200.0, 0.8, fx.mid))
        if abs(fx.high) > 0.05:
            sections.append(_biquad("highshelf", 7_000.0, 0.7, fx.high))
        return sections

    @classmethod
    def prepare_tone(cls, fx, channels: int = 2, blocksize: int = 0) -> PreparedTone:
        return _prepare_tone(cls.tone_key(fx), cls.tone_sections(fx), channels, blocksize)

    def install_tone(self, prepared: PreparedTone) -> None:
        self._tone_key = prepared.key
        self.sections = prepared.sections
        self.tone = prepared.convolver

    def _sync_tone(self, fx) -> bool:
        """Lazy preparation for offline/direct callers, never the live engine."""
        key = self.tone_key(fx)
        if key != self._tone_key:
            self.install_tone(self.prepare_tone(fx, self.channels))
        return bool(self.sections)

    def process(self, block: np.ndarray, fx, *, prepared_only: bool = False) -> np.ndarray:
        has_tone = bool(self.sections) if prepared_only else self._sync_tone(fx)
        if has_tone and self.tone is not None:
            self.tone.process(block, prepared_only=prepared_only)
        saturate(block, fx.drive)
        if fx.glue:
            amount = float(np.clip(fx.glue_amount, 0.0, 1.0))
            self.glue.process(
                block, -18.0 + 6.0 * (1 - amount), 2.0 + 2.0 * amount, 0.012, 0.24, 2.5 * amount
            )
        return block


# ── the rack the engine owns ─────────────────────────────────
class MixRack:
    """All mixer effects for one engine, addressed by track index."""

    def __init__(self, tracks: int, channels: int = 2):
        self.tracks = [TrackChain(channels) for _ in range(tracks)]
        self.delay = DelaySend(channels)
        self.reverb = ReverbSend(channels)
        self.master = MasterChain(channels)
        self.channels = channels
        self._delay_send = np.zeros((0, channels), dtype=np.float32)
        self._reverb_send = np.zeros((0, channels), dtype=np.float32)

    def prepare(self, frames: int) -> None:
        """Allocate mixer buses outside the audio callback."""
        frames = max(0, int(frames))
        if len(self._delay_send) < frames:
            self._delay_send = np.zeros((frames, self.channels), dtype=np.float32)
            self._reverb_send = np.zeros((frames, self.channels), dtype=np.float32)
        self.reverb.prepare(frames)

    def reset(self) -> None:
        for chain in self.tracks:
            chain.reset()
        self.delay.reset()
        self.reverb.reset()
        self.master.reset()

    def send_buffers(self, frames: int):
        """The two scratch send buses, cleared and sized for this block."""
        if len(self._delay_send) < frames:
            self._delay_send = np.zeros((frames, self.channels), dtype=np.float32)
            self._reverb_send = np.zeros((frames, self.channels), dtype=np.float32)
        delay = self._delay_send[:frames]
        reverb = self._reverb_send[:frames]
        delay.fill(0.0)
        reverb.fill(0.0)
        return delay, reverb
