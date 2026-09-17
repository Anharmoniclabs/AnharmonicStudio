"""Shared filter design primitives used by mixer, synth and future processors."""

from __future__ import annotations

import numpy as np

DEFAULT_SAMPLE_RATE = 48_000
DEFAULT_RESPONSE_FFT = 4096
DEFAULT_TAPS = 1536


def biquad(
    kind: str,
    freq: float,
    q: float,
    gain_db: float = 0.0,
    sr: int = DEFAULT_SAMPLE_RATE,
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
        slope = 2.0 * np.sqrt(amp) * alpha
        b = np.array(
            [
                amp * ((amp + 1) - (amp - 1) * cos_w + slope),
                2 * amp * ((amp - 1) - (amp + 1) * cos_w),
                amp * ((amp + 1) - (amp - 1) * cos_w - slope),
            ]
        )
        a = np.array(
            [
                (amp + 1) + (amp - 1) * cos_w + slope,
                -2 * ((amp - 1) + (amp + 1) * cos_w),
                (amp + 1) + (amp - 1) * cos_w - slope,
            ]
        )
    elif kind == "highshelf":
        slope = 2.0 * np.sqrt(amp) * alpha
        b = np.array(
            [
                amp * ((amp + 1) + (amp - 1) * cos_w + slope),
                -2 * amp * ((amp - 1) + (amp + 1) * cos_w),
                amp * ((amp + 1) + (amp - 1) * cos_w - slope),
            ]
        )
        a = np.array(
            [
                (amp + 1) - (amp - 1) * cos_w + slope,
                2 * ((amp - 1) - (amp + 1) * cos_w),
                (amp + 1) - (amp - 1) * cos_w - slope,
            ]
        )
    else:
        raise ValueError(f"unknown biquad kind: {kind}")
    return b / a[0], a / a[0]


def cascade_response(sections, freqs: np.ndarray, sr: int = DEFAULT_SAMPLE_RATE) -> np.ndarray:
    z = np.exp(-2j * np.pi * np.asarray(freqs, dtype=float) / sr)
    response = np.ones(len(z), dtype=complex)
    for b, a in sections:
        response *= (b[0] + b[1] * z + b[2] * z * z) / (
            a[0] + a[1] * z + a[2] * z * z
        )
    return response


def cascade_ir(
    sections,
    taps: int = DEFAULT_TAPS,
    *,
    sr: int = DEFAULT_SAMPLE_RATE,
    response_fft: int = DEFAULT_RESPONSE_FFT,
) -> np.ndarray:
    taps = max(1, int(taps))
    if not sections:
        impulse = np.zeros(taps, dtype=np.float32)
        impulse[0] = 1.0
        return impulse
    grid = np.fft.rfftfreq(response_fft, 1.0 / sr)
    impulse = np.fft.irfft(cascade_response(sections, grid, sr), response_fft)[:taps]
    fade = max(8, taps // 8)
    impulse[-fade:] *= np.linspace(1.0, 0.0, fade)
    return np.ascontiguousarray(impulse, dtype=np.float32)
