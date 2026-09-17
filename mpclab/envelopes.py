"""Reusable control envelopes and detector followers for synths, dynamics and modulation."""

from __future__ import annotations

import math

import numpy as np


class EnvelopeGenerator:
    """A bounded delay/attack/hold/decay/sustain/release envelope."""

    STAGES = ("off", "delay", "attack", "hold", "decay", "sustain", "release")

    def __init__(
        self,
        sample_rate: float,
        *,
        attack_ms: float = 10.0,
        decay_ms: float = 100.0,
        sustain: float = 0.8,
        release_ms: float = 200.0,
        delay_ms: float = 0.0,
        hold_ms: float = 0.0,
    ):
        if not math.isfinite(sample_rate) or sample_rate <= 0:
            raise ValueError("sample_rate must be positive and finite")
        self.sample_rate = float(sample_rate)
        self.configure(
            attack_ms=attack_ms,
            decay_ms=decay_ms,
            sustain=sustain,
            release_ms=release_ms,
            delay_ms=delay_ms,
            hold_ms=hold_ms,
        )
        self.reset()

    def _frames(self, time_ms: float) -> int:
        if not math.isfinite(time_ms) or time_ms < 0:
            raise ValueError("envelope times must be finite and non-negative")
        return max(0, round(self.sample_rate * time_ms / 1000.0))

    def configure(
        self,
        *,
        attack_ms: float,
        decay_ms: float,
        sustain: float,
        release_ms: float,
        delay_ms: float = 0.0,
        hold_ms: float = 0.0,
    ) -> None:
        if not math.isfinite(sustain) or not 0.0 <= sustain <= 1.0:
            raise ValueError("sustain must be between 0 and 1")
        self.attack_frames = self._frames(attack_ms)
        self.decay_frames = self._frames(decay_ms)
        self.release_frames = self._frames(release_ms)
        self.delay_frames = self._frames(delay_ms)
        self.hold_frames = self._frames(hold_ms)
        self.sustain = float(sustain)

    def reset(self) -> None:
        self.stage = "off"
        self.value = 0.0
        self.gate = False
        self._remaining = 0
        self._step = 0.0

    def _enter(self, stage: str) -> None:
        self.stage = stage
        if stage == "delay":
            self._remaining = self.delay_frames
        elif stage == "attack":
            self._remaining = self.attack_frames
            self._step = (1.0 - self.value) / max(1, self._remaining)
        elif stage == "hold":
            self.value = 1.0
            self._remaining = self.hold_frames
        elif stage == "decay":
            self._remaining = self.decay_frames
            self._step = (self.sustain - self.value) / max(1, self._remaining)
        elif stage == "sustain":
            self.value = self.sustain
            self._remaining = 0
            self._step = 0.0
        elif stage == "release":
            self._remaining = self.release_frames
            self._step = -self.value / max(1, self._remaining)
        elif stage == "off":
            self.value = 0.0
            self._remaining = 0
            self._step = 0.0

    def _advance_zero_length_stages(self) -> None:
        while True:
            if self.stage == "delay" and self._remaining == 0:
                self._enter("attack")
            elif self.stage == "attack" and self._remaining == 0:
                self.value = 1.0
                self._enter("hold" if self.hold_frames else "decay")
            elif self.stage == "hold" and self._remaining == 0:
                self._enter("decay")
            elif self.stage == "decay" and self._remaining == 0:
                self._enter("sustain")
            elif self.stage == "release" and self._remaining == 0:
                self._enter("off")
            else:
                return

    def trigger(self, mode: str = "zero") -> None:
        if mode not in ("zero", "current", "legato"):
            raise ValueError("retrigger mode must be zero, current or legato")
        if mode == "legato" and self.gate:
            return
        if mode == "zero":
            self.value = 0.0
        self.gate = True
        self._enter("delay" if self.delay_frames else "attack")
        self._advance_zero_length_stages()

    def gate_off(self) -> None:
        self.gate = False
        if self.stage == "off":
            return
        self._enter("release")
        self._advance_zero_length_stages()

    def next_value(self) -> float:
        self._advance_zero_length_stages()
        if self.stage in ("delay", "hold"):
            self._remaining -= 1
        elif self.stage in ("attack", "decay", "release"):
            self.value += self._step
            self._remaining -= 1
        self._advance_zero_length_stages()
        return self.value

    def fill(self, out: np.ndarray) -> np.ndarray:
        if out.ndim != 1:
            raise ValueError("out must be one-dimensional")
        for index in range(len(out)):
            out[index] = self.next_value()
        return out

    def process(self, frames: int, *, dtype=np.float32) -> np.ndarray:
        if frames < 0:
            raise ValueError("frames must be non-negative")
        return self.fill(np.empty(frames, dtype=dtype))


class EnvelopeFollower:
    """Peak or RMS detector with independent attack and release smoothing."""

    def __init__(
        self,
        sample_rate: float,
        *,
        attack_ms: float = 10.0,
        release_ms: float = 100.0,
        detector: str = "peak",
    ):
        if not math.isfinite(sample_rate) or sample_rate <= 0:
            raise ValueError("sample_rate must be positive and finite")
        if detector not in ("peak", "rms"):
            raise ValueError("detector must be 'peak' or 'rms'")
        self.sample_rate = float(sample_rate)
        self.detector = detector
        self.attack = self._coefficient(attack_ms)
        self.release = self._coefficient(release_ms)
        self.value = 0.0

    def _coefficient(self, time_ms: float) -> float:
        if not math.isfinite(time_ms) or time_ms < 0:
            raise ValueError("detector times must be finite and non-negative")
        frames = self.sample_rate * time_ms / 1000.0
        return 0.0 if frames <= 0 else math.exp(-1.0 / frames)

    def reset(self, value: float = 0.0) -> None:
        if not math.isfinite(value) or value < 0:
            raise ValueError("follower reset value must be finite and non-negative")
        self.value = float(value)

    def process(self, block: np.ndarray, out: np.ndarray | None = None) -> np.ndarray:
        data = np.asarray(block)
        if data.ndim == 1:
            detector_input = np.abs(data)
        elif data.ndim == 2:
            if self.detector == "peak":
                detector_input = np.max(np.abs(data), axis=1)
            else:
                detector_input = np.sqrt(np.mean(np.square(data, dtype=np.float64), axis=1))
        else:
            raise ValueError("audio block must be mono or frames-by-channels")
        if self.detector == "rms" and data.ndim == 1:
            detector_input = np.abs(data)
        if out is None:
            out = np.empty(len(detector_input), dtype=np.float32)
        if out.ndim != 1 or len(out) != len(detector_input):
            raise ValueError("out must match the frame count")
        for index, sample in enumerate(detector_input):
            target = float(sample)
            coefficient = self.attack if target > self.value else self.release
            self.value = target + coefficient * (self.value - target)
            out[index] = self.value
        return out
