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


def correlate_loopback(stimulus, captured, sample_rate=AUDIO_SAMPLE_RATE):
    """FFT correlation tolerates polarity reversal and multiple test transients."""
    reference = np.asarray(stimulus, dtype=np.float64).reshape(-1)
    received = np.asarray(captured, dtype=np.float64).reshape(-1)
    if not len(reference) or not len(received) or not np.isfinite(reference).all() or not np.isfinite(received).all():
        raise ValueError("Invalid loopback samples")
    size = 1 << (len(reference) + len(received) - 1).bit_length()
    correlation = np.fft.irfft(np.conj(np.fft.rfft(reference, size)) * np.fft.rfft(received, size), size)
    search = np.abs(correlation[:min(len(received), int(sample_rate * 0.5))])
    lag = int(np.argmax(search))
    energy = float(np.linalg.norm(reference) * np.linalg.norm(received))
    confidence = min(1.0, float(search[lag]) / max(1e-12, energy))
    if np.max(np.abs(received)) < 1e-4 or confidence < 0.35:
        raise ValueError("Loopback signal was not detected reliably; check the cable and input")
    return LatencyCalibration(lag, sample_rate, confidence)


def run_loopback_calibration(input_device=None, output_device=None,
                             sample_rate=AUDIO_SAMPLE_RATE, *, blocksize=512,
                             input_channel=0, output_channels=(0, 1)):
    """Measure the selected host route including the production output FIFO.

    A duplex calibration stream establishes a common sample timeline. A render
    producer feeds the same native output queue used by production. Capture is
    preallocated; no files or widgets are touched by the callback. This test
    measures the dry route, not a project with latency-inducing plugins.
    """
    import ctypes as ct
    import threading
    import time
    import sounddevice as sd
    from .native_dsp import NATIVE
    from .native_output import OutputQueue

    if NATIVE is None:
        raise RuntimeError("Build native audio before measuring the production route")
    if blocksize not in (128, 256, 512, 1024):
        raise ValueError("Choose a supported calibration buffer")
    if type(input_channel) is not int or not 0 <= input_channel < 64:
        raise ValueError("Invalid loopback input channel")
    mapping = tuple(output_channels)
    if len(mapping) != 2 or any(type(c) is not int or not 0 <= c < 64 for c in mapping):
        raise ValueError("Invalid loopback output pair")
    total = int(sample_rate * 1.5)
    stimulus = np.zeros(total, np.float32)
    for at, gain in ((0.1, .15), (.29, -.12), (.53, .18), (.81, .1)):
        start = int(at * sample_rate)
        stimulus[start:start + 64] = np.hanning(64) * gain
    captured = np.zeros(total, np.float32)
    fifo = OutputQueue(NATIVE.lib, blocksize * 2)
    set_channels = NATIVE.lib.anh_output_channels
    set_channels.argtypes, set_channels.restype = [ct.c_void_p, ct.c_size_t, ct.c_size_t, ct.c_size_t], ct.c_int
    if set_channels(fifo.handle, max(mapping) + 1, *mapping):
        fifo.close()
        raise ValueError("Could not prepare output channels")
    stop = threading.Event()
    finished = threading.Event()
    error = []
    position = 0
    faults = 0
    block = np.zeros((blocksize, 2), np.float32)
    fifo.write(np.zeros((blocksize * 2, 2), np.float32))

    def produce():
        generated = 0
        try:
            while not stop.is_set():
                if fifo.available <= blocksize:
                    block.fill(0)
                    count = max(0, min(blocksize, total - generated))
                    if count:
                        block[:count] = stimulus[generated:generated + count, None]
                    generated += blocksize
                    if not fifo.write(block):
                        raise RuntimeError("Calibration producer exceeded its queue")
                else:
                    stop.wait(0.0005)
        except Exception as exc:
            error.append(str(exc))
            finished.set()

    def callback(indata, outdata, frames, timing, status):
        nonlocal position, faults
        count = max(0, min(frames, total - position))
        captured[position:position + count] = indata[:count, input_channel]
        position += count
        faults += bool(status)
        fifo.lib.anh_output_callback(None, outdata.ctypes.data, frames, None, 0, fifo.handle)
        if position >= total:
            finished.set()

    worker = threading.Thread(target=produce, name="Loopback signal", daemon=True)
    worker.start()
    try:
        with sd.Stream(device=(input_device, output_device), samplerate=sample_rate,
                       blocksize=blocksize, channels=(input_channel + 1, max(mapping) + 1),
                       dtype="float32", latency="low", callback=callback):
            deadline = time.monotonic() + 5
            while not finished.wait(0.05):
                if time.monotonic() >= deadline:
                    raise RuntimeError("Loopback timed out; check the selected devices")
        if error:
            raise RuntimeError(error[0])
        if faults or fifo.underruns:
            raise RuntimeError("Loopback dropped audio; increase the buffer and measure again")
        return correlate_loopback(stimulus, captured, sample_rate)
    finally:
        stop.set()
        worker.join(timeout=3)
        if worker.is_alive():
            raise RuntimeError("Calibration producer is still stopping")
        fifo.close()


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
