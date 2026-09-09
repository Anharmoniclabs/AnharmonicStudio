"""Offline root-pitch estimation for melodic samples.

The detector is intentionally conservative: it returns a low confidence for
noisy/percussive material instead of pretending every sample has a musical root.
It has no audio-thread responsibilities and is safe to run in a background job.
"""

from __future__ import annotations

from dataclasses import dataclass
import math

import numpy as np


@dataclass(frozen=True)
class PitchEstimate:
    midi_note: int
    frequency_hz: float
    confidence: float

    @property
    def confident(self) -> bool:
        return self.confidence >= 0.55


def _mono(audio: np.ndarray) -> np.ndarray:
    data = np.asarray(audio, dtype=np.float64)
    if data.ndim == 2:
        data = data.mean(axis=1)
    if data.ndim != 1:
        raise ValueError("audio must be mono or frames-by-channels")
    return data


def estimate_root_pitch(
    audio: np.ndarray,
    sample_rate: int,
    *,
    min_hz: float = 35.0,
    max_hz: float = 1600.0,
) -> PitchEstimate | None:
    """Estimate a sample's fundamental with normalized autocorrelation.

    Returns ``None`` for silence, too-short audio, or an invalid search range.
    The confidence combines periodicity strength and peak separation so callers
    can require manual confirmation for ambiguous/noisy sounds.
    """
    if sample_rate <= 0 or min_hz <= 0 or max_hz <= min_hz:
        raise ValueError("invalid sample rate or pitch range")

    x = _mono(audio)
    if x.size < max(64, int(sample_rate / min_hz) * 2):
        return None
    if not np.isfinite(x).all():
        return None

    x = x - float(np.mean(x))
    peak = float(np.max(np.abs(x)))
    if peak < 1e-7:
        return None

    # Bound analysis cost for long recordings while preserving enough cycles for
    # low notes. A Hann window reduces edge correlation artifacts.
    max_frames = min(x.size, max(sample_rate * 2, int(sample_rate / min_hz) * 8))
    x = x[:max_frames]
    x = x * np.hanning(x.size)

    min_lag = max(1, int(sample_rate / max_hz))
    max_lag = min(x.size // 2, int(sample_rate / min_hz))
    if max_lag <= min_lag:
        return None

    energy = float(np.dot(x, x))
    if energy <= 1e-12:
        return None

    # FFT autocorrelation keeps this practical for 1-2 second sample previews.
    fft_size = 1 << (2 * x.size - 1).bit_length()
    spectrum = np.fft.rfft(x, n=fft_size)
    corr = np.fft.irfft(spectrum * np.conj(spectrum), n=fft_size)[: max_lag + 1]
    corr0 = float(corr[0])
    if corr0 <= 1e-12:
        return None
    scores = corr[min_lag : max_lag + 1] / corr0
    best_rel = int(np.argmax(scores))
    best_lag = min_lag + best_rel
    best_score = float(scores[best_rel])

    # Parabolic interpolation around the autocorrelation peak for a less stepped
    # pitch estimate without introducing a heavyweight dependency.
    refined = float(best_lag)
    if min_lag < best_lag < max_lag:
        y0 = float(corr[best_lag - 1])
        y1 = float(corr[best_lag])
        y2 = float(corr[best_lag + 1])
        denom = y0 - 2.0 * y1 + y2
        if abs(denom) > 1e-12:
            refined += 0.5 * (y0 - y2) / denom

    frequency = sample_rate / refined
    if not math.isfinite(frequency) or not min_hz <= frequency <= max_hz:
        return None

    # Ambiguous periodic signals often have several similarly strong lags.
    neighborhood = scores.copy()
    lo = max(0, best_rel - 2)
    hi = min(neighborhood.size, best_rel + 3)
    neighborhood[lo:hi] = -1.0
    second = float(np.max(neighborhood)) if neighborhood.size else 0.0
    separation = max(0.0, best_score - max(0.0, second))
    confidence = float(np.clip(0.8 * best_score + 1.5 * separation, 0.0, 1.0))

    midi = int(round(69.0 + 12.0 * math.log2(frequency / 440.0)))
    midi = max(0, min(127, midi))
    return PitchEstimate(midi_note=midi, frequency_hz=float(frequency), confidence=confidence)
