"""Calibrated mastering measurements and bounded live analysis helpers.

The loudness implementation follows the BS.1770/EBU R128 measurement model for
mono/stereo programme material: K-weighting, 400 ms overlapping blocks,
absolute and relative gating for integrated loudness, and 3 s short-term blocks
for loudness range. True peak is reconstructed at 4x with a windowed-sinc
interpolator. No audio device is opened here.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
import math
import threading

import numpy as np

ABSOLUTE_GATE_LUFS = -70.0
INTEGRATED_RELATIVE_GATE_LU = -10.0
LRA_RELATIVE_GATE_LU = -20.0
TRUE_PEAK_OVERSAMPLE = 4


def _audio(value) -> np.ndarray:
    data = np.asarray(value, dtype=np.float64)
    if data.ndim == 1:
        data = data[:, None]
    if data.ndim != 2 or data.shape[1] < 1 or data.shape[1] > 64:
        raise ValueError("audio must be a frame-by-channel array with 1-64 channels")
    if not np.isfinite(data).all():
        raise ValueError("audio contains nonfinite samples")
    return np.ascontiguousarray(data)


def _loudness(energy: float) -> float:
    return -math.inf if energy <= 0.0 else -0.691 + 10.0 * math.log10(energy)


def _db(amplitude: float) -> float:
    return -math.inf if amplitude <= 0.0 else 20.0 * math.log10(amplitude)


def _shelf_coefficients(sample_rate: int):
    # ITU/EBU K-weighting pre-filter constants used by the De Man reference form.
    gain = 3.99984385397
    q = 0.7071752369554196
    f0 = 1681.974450955533
    k = math.tan(math.pi * f0 / sample_rate)
    vh = 10.0 ** (gain / 20.0)
    vb = vh**0.4996667741545416
    a0 = 1.0 + k / q + k * k
    return (
        np.array(
            [
                (vh + vb * k / q + k * k) / a0,
                2.0 * (k * k - vh) / a0,
                (vh - vb * k / q + k * k) / a0,
            ]
        ),
        np.array([1.0, 2.0 * (k * k - 1.0) / a0, (1.0 - k / q + k * k) / a0]),
    )


def _highpass_coefficients(sample_rate: int):
    q = 0.5003270373238773
    f0 = 38.13547087602444
    k = math.tan(math.pi * f0 / sample_rate)
    a0 = 1.0 + k / q + k * k
    return (
        np.array([1.0 / a0, -2.0 / a0, 1.0 / a0]),
        np.array([1.0, 2.0 * (k * k - 1.0) / a0, (1.0 - k / q + k * k) / a0]),
    )


def _biquad_impulse(b: np.ndarray, a: np.ndarray, length: int) -> np.ndarray:
    impulse = np.zeros(length, dtype=np.float64)
    impulse[0] = 1.0
    output = np.zeros(length, dtype=np.float64)
    x1 = x2 = y1 = y2 = 0.0
    for index, x0 in enumerate(impulse):
        y0 = b[0] * x0 + b[1] * x1 + b[2] * x2 - a[1] * y1 - a[2] * y2
        output[index] = y0
        x2, x1 = x1, x0
        y2, y1 = y1, y0
    return output


def _fft_convolve(signal: np.ndarray, kernel: np.ndarray) -> np.ndarray:
    size = len(signal) + len(kernel) - 1
    nfft = 1 << max(1, size - 1).bit_length()
    spectrum = np.fft.rfft(signal, nfft) * np.fft.rfft(kernel, nfft)
    return np.fft.irfft(spectrum, nfft)[: len(signal)]


@lru_cache(maxsize=16)
def _k_weight_kernel(sample_rate: int) -> np.ndarray:
    sample_rate = int(sample_rate)
    if not 8_000 <= sample_rate <= 768_000:
        raise ValueError("sample rate must be between 8 kHz and 768 kHz")
    # 0.35 s captures the IIR decay well below measurement significance while
    # retaining exact reference coefficients at the requested rate.
    length = max(4096, min(65536, int(round(sample_rate * 0.35))))
    shelf = _biquad_impulse(*_shelf_coefficients(sample_rate), length)
    highpass = _biquad_impulse(*_highpass_coefficients(sample_rate), length)
    combined = _fft_convolve(shelf, highpass)
    # Remove a numerically irrelevant tail so short inputs stay cheap.
    significant = np.flatnonzero(np.abs(combined) > 1e-12)
    return combined[: int(significant[-1]) + 1] if len(significant) else combined[:1]


def k_weight(audio, sample_rate: int) -> np.ndarray:
    data = _audio(audio)
    kernel = _k_weight_kernel(int(sample_rate))
    result = np.empty_like(data)
    for channel in range(data.shape[1]):
        result[:, channel] = _fft_convolve(data[:, channel], kernel)
    return result


def _channel_weights(channels: int) -> np.ndarray:
    if channels <= 3:
        return np.ones(channels, dtype=np.float64)
    if channels == 4:
        return np.array([1.0, 1.0, 1.41, 1.41], dtype=np.float64)
    if channels == 5:
        return np.array([1.0, 1.0, 1.0, 1.41, 1.41], dtype=np.float64)
    if channels == 6:
        # Common L R C LFE Ls Rs ordering. LFE is excluded by BS.1770.
        return np.array([1.0, 1.0, 1.0, 0.0, 1.41, 1.41], dtype=np.float64)
    return np.ones(channels, dtype=np.float64)


def _block_energies(weighted: np.ndarray, sample_rate: int, seconds: float, hop_fraction: float):
    block = max(1, int(round(seconds * sample_rate)))
    hop = max(1, int(round(block * hop_fraction)))
    if len(weighted) < block:
        padded = np.zeros((block, weighted.shape[1]), dtype=np.float64)
        padded[: len(weighted)] = weighted
        weighted = padded
    weights = _channel_weights(weighted.shape[1])
    energies = []
    for start in range(0, len(weighted) - block + 1, hop):
        section = weighted[start : start + block]
        channel_energy = np.mean(section * section, axis=0)
        energies.append(float(np.dot(weights, channel_energy)))
    return np.asarray(energies, dtype=np.float64)


def integrated_loudness(audio, sample_rate: int) -> float:
    weighted = k_weight(audio, sample_rate)
    energies = _block_energies(weighted, sample_rate, 0.400, 0.25)
    if not len(energies):
        return -math.inf
    loudness = np.array([_loudness(value) for value in energies])
    absolute = energies[loudness > ABSOLUTE_GATE_LUFS]
    if not len(absolute):
        return -math.inf
    ungated = _loudness(float(np.mean(absolute)))
    threshold = ungated + INTEGRATED_RELATIVE_GATE_LU
    gated = energies[(loudness > ABSOLUTE_GATE_LUFS) & (loudness > threshold)]
    return _loudness(float(np.mean(gated))) if len(gated) else -math.inf


def loudness_range(audio, sample_rate: int) -> float:
    data = _audio(audio)
    integrated = integrated_loudness(data, sample_rate)
    if not math.isfinite(integrated):
        return 0.0
    weighted = k_weight(data, sample_rate)
    energies = _block_energies(weighted, sample_rate, 3.0, 1.0 / 3.0)
    if not len(energies):
        return 0.0
    values = np.array([_loudness(value) for value in energies])
    gated = values[(values > ABSOLUTE_GATE_LUFS) & (values > integrated + LRA_RELATIVE_GATE_LU)]
    if len(gated) < 2:
        return 0.0
    return float(np.percentile(gated, 95) - np.percentile(gated, 10))


@lru_cache(maxsize=4)
def _true_peak_kernel(factor: int = TRUE_PEAK_OVERSAMPLE) -> np.ndarray:
    # Symmetric low-pass interpolation kernel. Zero-stuffed samples are scaled
    # by factor so original sample positions retain their amplitude.
    half = 32 * factor
    index = np.arange(-half, half + 1, dtype=np.float64)
    kernel = np.sinc(index / factor) * np.hanning(len(index))
    kernel *= factor / np.sum(kernel)
    return kernel


def true_peak(audio, oversample: int = TRUE_PEAK_OVERSAMPLE) -> tuple[float, float]:
    data = _audio(audio)
    oversample = int(oversample)
    if oversample not in (2, 4, 8):
        raise ValueError("true-peak oversampling must be 2x, 4x or 8x")
    kernel = _true_peak_kernel(oversample)
    highest = 0.0
    for channel in range(data.shape[1]):
        up = np.zeros(len(data) * oversample, dtype=np.float64)
        up[::oversample] = data[:, channel]
        reconstructed = np.convolve(up, kernel, mode="same")
        highest = max(highest, float(np.max(np.abs(reconstructed), initial=0.0)))
    return highest, _db(highest)


def spectrum(audio, sample_rate: int, *, bins: int = 512) -> tuple[np.ndarray, np.ndarray]:
    data = _audio(audio)
    if not len(data):
        return np.array([], dtype=np.float64), np.array([], dtype=np.float64)
    mono = data.mean(axis=1)
    exponent = max(8, min(18, max(1, len(mono) - 1).bit_length()))
    fft_size = 1 << exponent
    take = min(len(mono), fft_size)
    windowed = np.zeros(fft_size, dtype=np.float64)
    windowed[:take] = mono[-take:] * np.hanning(take)
    values = np.abs(np.fft.rfft(windowed)) / max(1.0, np.sum(np.hanning(take)) / 2.0)
    freqs = np.fft.rfftfreq(fft_size, 1.0 / sample_rate)
    if len(values) <= bins:
        return freqs, values
    edges = np.geomspace(max(1.0, freqs[1]), max(2.0, freqs[-1]), bins + 1)
    out_f = np.empty(bins, dtype=np.float64)
    out_v = np.zeros(bins, dtype=np.float64)
    for index in range(bins):
        mask = (freqs >= edges[index]) & (freqs < edges[index + 1])
        out_f[index] = math.sqrt(edges[index] * edges[index + 1])
        if mask.any():
            out_v[index] = float(np.max(values[mask]))
    return out_f, out_v


def stereo_field(audio) -> dict[str, float]:
    data = _audio(audio)
    if data.shape[1] == 1:
        left = right = data[:, 0]
    else:
        left, right = data[:, 0], data[:, 1]
    left_ac = left - left.mean() if len(left) else left
    right_ac = right - right.mean() if len(right) else right
    denominator = float(np.linalg.norm(left_ac) * np.linalg.norm(right_ac))
    correlation = float(np.dot(left_ac, right_ac) / denominator) if denominator > 0 else 1.0
    mid = (left + right) / math.sqrt(2.0)
    side = (left - right) / math.sqrt(2.0)
    mid_energy = float(np.dot(mid, mid))
    side_energy = float(np.dot(side, side))
    total = mid_energy + side_energy
    return {
        "correlation": float(np.clip(correlation, -1.0, 1.0)),
        "side_percent": 0.0 if total <= 0 else side_energy / total * 100.0,
        "mid_rms_dbfs": _db(math.sqrt(mid_energy / max(1, len(mid)))),
        "side_rms_dbfs": _db(math.sqrt(side_energy / max(1, len(side)))),
    }


@dataclass(frozen=True)
class MasteringMeasurement:
    integrated_lufs: float
    loudness_range_lu: float
    true_peak: float
    true_peak_dbtp: float
    sample_peak_dbfs: float
    correlation: float
    side_percent: float


def measure_mastering(audio, sample_rate: int) -> MasteringMeasurement:
    data = _audio(audio)
    tp, dbtp = true_peak(data)
    field = stereo_field(data)
    sample_peak = float(np.max(np.abs(data), initial=0.0))
    return MasteringMeasurement(
        integrated_loudness(data, sample_rate),
        loudness_range(data, sample_rate),
        tp,
        dbtp,
        _db(sample_peak),
        field["correlation"],
        field["side_percent"],
    )


class LiveAnalysisTap:
    """Bounded non-blocking history for callback-to-UI analysis.

    The audio callback never waits for the UI: if the UI currently owns the
    buffer lock, that block is deliberately skipped rather than stalling audio.
    """

    def __init__(self, sample_rate: int, seconds: float = 4.0):
        self.sample_rate = int(sample_rate)
        self.capacity = max(1024, min(self.sample_rate * 10, int(self.sample_rate * seconds)))
        self.buffer = np.zeros((self.capacity, 2), dtype=np.float32)
        self.write = 0
        self.filled = 0
        self._lock = threading.Lock()

    def push(self, audio) -> bool:
        if not self._lock.acquire(blocking=False):
            return False
        try:
            data = np.asarray(audio, dtype=np.float32)
            if data.ndim != 2 or data.shape[1] < 1 or not len(data):
                return False
            if data.shape[1] == 1:
                data = np.repeat(data, 2, axis=1)
            data = data[:, :2]
            if len(data) >= self.capacity:
                self.buffer[:] = data[-self.capacity :]
                self.write = 0
                self.filled = self.capacity
                return True
            first = min(len(data), self.capacity - self.write)
            self.buffer[self.write : self.write + first] = data[:first]
            rest = len(data) - first
            if rest:
                self.buffer[:rest] = data[first:]
            self.write = (self.write + len(data)) % self.capacity
            self.filled = min(self.capacity, self.filled + len(data))
            return True
        finally:
            self._lock.release()

    def snapshot(self, frames: int | None = None) -> np.ndarray:
        with self._lock:
            count = self.filled if frames is None else min(self.filled, max(0, int(frames)))
            if not count:
                return np.zeros((0, 2), dtype=np.float32)
            start = (self.write - count) % self.capacity
            if start + count <= self.capacity:
                return self.buffer[start : start + count].copy()
            first = self.buffer[start:].copy()
            second = self.buffer[: count - len(first)].copy()
            return np.concatenate((first, second), axis=0)

    def reset(self) -> None:
        with self._lock:
            self.write = 0
            self.filled = 0
            self.buffer.fill(0)
