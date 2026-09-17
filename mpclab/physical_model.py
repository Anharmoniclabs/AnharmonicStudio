"""Physical-audio building blocks: exciters, modal bodies and FDN networks."""

from __future__ import annotations

from dataclasses import dataclass
import math

import numpy as np

from .delay_line import BlockDelayLine
from .dsp_contracts import ProcessorSpec, validate_audio_block, validate_prepare


@dataclass(frozen=True, slots=True)
class Exciter:
    kind: str = "impulse"
    brightness: float = 0.65
    level: float = 1.0

    def render(self, frames: int, seed: int = 0) -> np.ndarray:
        frames = max(0, int(frames))
        if self.kind == "impulse":
            out = np.zeros(frames, dtype=np.float32)
            if frames:
                out[0] = np.float32(self.level)
            return out
        if self.kind == "noise":
            rng = np.random.default_rng(int(seed))
            noise = rng.uniform(-1.0, 1.0, frames).astype(np.float32)
            if frames > 1:
                # One-zero color control keeps this deterministic and bounded.
                previous = np.empty_like(noise)
                previous[0] = 0.0
                previous[1:] = noise[:-1]
                noise = noise * np.float32(self.brightness) + previous * np.float32(
                    1.0 - self.brightness
                )
            return noise * np.float32(self.level)
        raise ValueError("exciter kind must be impulse or noise")


class ModalResonator:
    """Vectorized damped modal bank: Exciter → Resonator → stereo Body."""

    def __init__(self, sample_rate: int, modes=None):
        self.sample_rate = int(sample_rate)
        raw = modes or (
            (1.00, 1.00, 2.8),
            (2.01, 0.55, 2.0),
            (3.91, 0.34, 1.35),
            (5.23, 0.22, 0.95),
            (7.11, 0.13, 0.70),
            (9.37, 0.08, 0.52),
        )
        if len(raw) > 128:
            raise ValueError("modal resonator supports at most 128 modes")
        self.modes = tuple((float(r), float(g), float(d)) for r, g, d in raw)
        self.age = 0

    def reset(self) -> None:
        self.age = 0

    def process(self, excitation: np.ndarray, fundamental: float) -> np.ndarray:
        excitation = np.asarray(excitation, dtype=np.float32)
        if excitation.ndim != 1:
            raise ValueError("modal excitation must be mono")
        frames = len(excitation)
        if not frames:
            return np.zeros((0, 2), dtype=np.float32)
        time = (self.age + np.arange(frames, dtype=np.float64)) / self.sample_rate
        body = np.zeros(frames, dtype=np.float64)
        for ratio, gain, decay in self.modes:
            frequency = min(self.sample_rate * 0.45, max(1.0, float(fundamental) * ratio))
            response = np.sin(2.0 * np.pi * frequency * time) * np.exp(-time / max(0.005, decay))
            # Excitation energy scales the modal response; sustained exciters
            # may drive a new processor instance per strike in the instrument.
            body += response * gain * float(np.max(np.abs(excitation), initial=0.0))
        self.age += frames
        left = body * 0.7071067811865476
        right = np.roll(body, 1) * 0.7071067811865476
        if frames:
            right[0] = body[0] * 0.7071067811865476
        return np.column_stack((left, right)).astype(np.float32)


class FDNReverb:
    """Four-line feedback-delay network with block-chunked Hadamard feedback.

    Processing iterates by delay-sized chunks, never by individual samples in
    Python. All delay rings and scratch are allocated in prepare().
    """

    spec = ProcessorSpec("fdn-body", "Four-line physical body / FDN reverb")
    _BASE_SECONDS = (0.0297, 0.0371, 0.0411, 0.0437)

    def __init__(self, feedback: float = 0.72, damping: float = 0.35, wet: float = 0.35):
        self.feedback = float(feedback)
        self.damping = float(damping)
        self.wet = float(wet)
        self.sample_rate = 48_000
        self.max_block_size = 1024
        self.channels = 2
        self.delays = ()
        self.lines = ()
        self._read = np.zeros((4, self.max_block_size), dtype=np.float32)
        self._write = np.zeros_like(self._read)
        self._stereo = np.zeros((self.max_block_size, 2), dtype=np.float32)
        self._validate_parameters()
        self.prepare(self.sample_rate, self.max_block_size, 2)

    def _validate_parameters(self) -> None:
        if not math.isfinite(self.feedback) or not 0.0 <= self.feedback < 0.98:
            raise ValueError("FDN feedback must be between 0 and 0.98")
        if not math.isfinite(self.damping) or not 0.0 <= self.damping <= 1.0:
            raise ValueError("FDN damping must be between 0 and 1")
        if not math.isfinite(self.wet) or not 0.0 <= self.wet <= 1.0:
            raise ValueError("FDN wet must be between 0 and 1")

    def prepare(self, sample_rate: float, max_block_size: int, channels: int = 2) -> None:
        rate, block, channels = validate_prepare(sample_rate, max_block_size, channels)
        if channels != 2:
            raise ValueError("FDN body currently exposes a stereo processor contract")
        self.sample_rate = int(rate)
        self.max_block_size = block
        self.channels = channels
        # Pairwise-prime-ish odd lengths decorrelate the four lines.
        delays = [max(7, int(round(seconds * rate)) | 1) for seconds in self._BASE_SECONDS]
        self.delays = tuple(delays)
        capacity = max(delays) + block + 4
        self.lines = tuple(BlockDelayLine(capacity, 1) for _ in delays)
        self._read = np.zeros((4, block), dtype=np.float32)
        self._write = np.zeros((4, block), dtype=np.float32)
        self._stereo = np.zeros((block, 2), dtype=np.float32)

    def reset(self) -> None:
        for line in self.lines:
            line.reset()
        self._read.fill(0.0)
        self._write.fill(0.0)
        self._stereo.fill(0.0)

    @staticmethod
    def _hadamard(values: np.ndarray, out: np.ndarray) -> None:
        a, b, c, d = values
        out[0] = 0.5 * (a + b + c + d)
        out[1] = 0.5 * (a - b + c - d)
        out[2] = 0.5 * (a + b - c - d)
        out[3] = 0.5 * (a - b - c + d)

    def process(self, block: np.ndarray) -> None:
        validate_audio_block(block, channels=2)
        frames = len(block)
        if frames > self.max_block_size:
            raise ValueError("FDN block exceeds prepared size")
        self._validate_parameters()
        if not frames:
            return
        input_mono = block.mean(axis=1, dtype=np.float32)
        wet = self._stereo[:frames]
        wet.fill(0.0)
        at = 0
        chunk_limit = min(self.delays)
        damping_gain = np.float32(1.0 - 0.45 * self.damping)
        feedback = np.float32(self.feedback)
        while at < frames:
            take = min(chunk_limit, frames - at)
            reads = self._read[:, :take]
            writes = self._write[:, :take]
            for index, (line, delay) in enumerate(zip(self.lines, self.delays, strict=True)):
                line.read(take, delay, reads[index, :, None])
            self._hadamard(reads, writes)
            injection = input_mono[at : at + take] * np.float32(0.5)
            writes *= feedback * damping_gain
            writes += injection[None, :]
            for index, line in enumerate(self.lines):
                line.write(writes[index, :, None])
            wet[at : at + take, 0] = (reads[0] + reads[2] - reads[1] * 0.35) * np.float32(
                0.45
            )
            wet[at : at + take, 1] = (reads[1] + reads[3] - reads[0] * 0.35) * np.float32(
                0.45
            )
            at += take
        dry = np.float32(1.0 - self.wet)
        wet_gain = np.float32(self.wet)
        block *= dry
        block += wet * wet_gain
        np.nan_to_num(block, copy=False)

    def latency_samples(self) -> int:
        return 0

    def tail_samples(self) -> int:
        if self.feedback <= 0.0:
            return max(self.delays)
        # Time to fall roughly 80 dB, bounded to a minute.
        loops = math.log(1e-4) / math.log(max(1e-6, self.feedback))
        return min(self.sample_rate * 60, int(max(self.delays) * max(1.0, loops)))

    def save_state(self) -> dict:
        return {
            "feedback": self.feedback,
            "damping": self.damping,
            "wet": self.wet,
        }

    def restore_state(self, state: dict) -> None:
        if not isinstance(state, dict):
            raise ValueError("FDN state must be an object")
        self.feedback = float(state.get("feedback", self.feedback))
        self.damping = float(state.get("damping", self.damping))
        self.wet = float(state.get("wet", self.wet))
        self._validate_parameters()
