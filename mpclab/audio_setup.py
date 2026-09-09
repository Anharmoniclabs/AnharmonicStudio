"""Audio onboarding, loopback calibration, and adaptive buffer advice.

This module deliberately has no Qt or engine dependency.  The setup dialog,
tests, and future command-line diagnostics can share the same policy without
opening a PortAudio stream during import.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .audio_kernel import (
    AUDIO_BUFFER_PROFILES,
    AUDIO_SAMPLE_RATE,
    BALANCED_BLOCKSIZE,
    BLUETOOTH_SAFE_BLOCKSIZE,
    LOW_LATENCY_BLOCKSIZE,
    ULTRA_LOW_LATENCY_BLOCKSIZE,
)


WORKFLOW_PROFILES = {
    "build": BALANCED_BLOCKSIZE,
    "production": LOW_LATENCY_BLOCKSIZE,
    "live": ULTRA_LOW_LATENCY_BLOCKSIZE,
}


@dataclass(frozen=True)
class LatencyCalibration:
    """A measured output-to-input round trip."""

    samples: int
    sample_rate: int = AUDIO_SAMPLE_RATE
    confidence: float = 0.0

    @property
    def milliseconds(self) -> float:
        return self.samples * 1000.0 / max(1, self.sample_rate)


@dataclass(frozen=True)
class AudioHealthAdvice:
    """User-facing diagnosis derived from callback timing, never a mutation."""

    unsafe: bool
    recommended_frames: int | None = None
    reason: str = ""


def recommended_buffer(workflow: str) -> int:
    """Return the documented safe starting point for a working style."""
    return WORKFLOW_PROFILES.get(str(workflow).lower(), BALANCED_BLOCKSIZE)


def next_buffer_profile(frames: int) -> int | None:
    sizes = [size for _label, size in AUDIO_BUFFER_PROFILES]
    for size in sizes:
        if size > int(frames):
            return size
    return None


def assess_audio_health(frames: int, stats: dict) -> AudioHealthAdvice:
    """Recommend the next profile when p99 is risky or an xrun occurred."""
    period = max(float(stats.get("period", 0.0)), 1e-9)
    p99_ratio = float(stats.get("p99", 0.0)) / period
    xruns = int(stats.get("xruns", 0))
    worst_over = float(stats.get("max", 0.0)) >= period
    unsafe = xruns > 0 or p99_ratio >= 0.5 or worst_over
    if not unsafe:
        return AudioHealthAdvice(False)
    recommended = next_buffer_profile(frames)
    reason = (
        f"{xruns} xrun{'s' if xruns != 1 else ''}"
        if xruns
        else (
            "worst callback exceeded its period"
            if worst_over
            else f"p99 DSP load reached {p99_ratio:.0%}"
        )
    )
    return AudioHealthAdvice(True, recommended, reason)


def _mono_peak(signal: np.ndarray) -> tuple[int, float, float]:
    data = np.asarray(signal, dtype=np.float32)
    if data.ndim == 2:
        data = np.max(np.abs(data), axis=1)
    elif data.ndim != 1:
        raise ValueError("loopback signals must be mono or channel-last audio")
    if not len(data):
        raise ValueError("loopback signal is empty")
    magnitude = np.abs(data)
    index = int(np.argmax(magnitude))
    peak = float(magnitude[index])
    floor = float(np.sqrt(np.mean(np.square(magnitude, dtype=np.float64))))
    return index, peak, floor


def estimate_loopback_latency(
    stimulus: np.ndarray, captured: np.ndarray, sample_rate: int = AUDIO_SAMPLE_RATE
) -> LatencyCalibration:
    """Measure a click's capture delay using its strongest transient.

    The calibration stimulus intentionally contains one unambiguous transient,
    making peak location more robust and much cheaper than full correlation.
    """
    emitted, _stimulus_peak, _stimulus_floor = _mono_peak(stimulus)
    received, peak, floor = _mono_peak(captured)
    confidence = float(np.clip((peak - floor) / max(peak, 1e-9), 0.0, 1.0))
    if peak < 1e-4 or confidence < 0.2:
        raise ValueError("loopback click was not detected; check the cable and input")
    samples = received - emitted
    if samples < 0:
        raise ValueError("captured loopback arrived before the calibration click")
    return LatencyCalibration(samples, int(sample_rate), confidence)


def loopback_stimulus(sample_rate: int = AUDIO_SAMPLE_RATE, duration: float = 0.75) -> np.ndarray:
    """Return a quiet stereo calibration click with headroom."""
    frames = max(int(sample_rate * duration), 2048)
    audio = np.zeros((frames, 2), dtype=np.float32)
    start = min(frames - 64, int(sample_rate * 0.08))
    pulse = np.hanning(64).astype(np.float32) * np.float32(0.35)
    audio[start : start + len(pulse), :] = pulse[:, None]
    return audio


def run_loopback_calibration(
    input_device=None, output_device=None, sample_rate: int = AUDIO_SAMPLE_RATE
) -> LatencyCalibration:
    """Play and capture the calibration click through a physical loopback.

    This short blocking helper is called by the setup dialog before it starts
    or while it temporarily owns no engine callback.  It stays separate from
    both the live engine and vocal recorder.
    """
    import sounddevice as sd

    stimulus = loopback_stimulus(sample_rate)
    device = (input_device, output_device)
    captured = sd.playrec(
        stimulus, samplerate=sample_rate, channels=1, dtype="float32", device=device, blocking=True
    )
    return estimate_loopback_latency(stimulus, captured, sample_rate)


def profile_description(frames: int) -> str:
    if frames == ULTRA_LOW_LATENCY_BLOCKSIZE:
        return "128 frames · experimental, light tracking only"
    if frames == LOW_LATENCY_BLOCKSIZE:
        return "256 frames · normal production"
    if frames == BALANCED_BLOCKSIZE:
        return "512 frames · builds, heavy sessions, and mixing"
    if frames == BLUETOOTH_SAFE_BLOCKSIZE:
        return "1024 frames · maximum stability / Bluetooth"
    return f"{int(frames)} frames"
