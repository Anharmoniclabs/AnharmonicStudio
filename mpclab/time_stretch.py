"""Bounded offline time stretching for arrangement clip rendering.

These routines never run in the PortAudio callback.  They are used by explicit
editor commands that render a new library asset, keeping the source audio
unchanged and making the result portable with the project library.
"""

from __future__ import annotations

import math
import numpy as np


MODES = ("resample", "beats", "percussion", "texture", "melodic", "complex")


def _audio(value: np.ndarray) -> np.ndarray:
    data = np.asarray(value, dtype=np.float32)
    if data.ndim == 1:
        data = data[:, None]
    if data.ndim != 2 or data.shape[1] not in (1, 2) or not len(data):
        raise ValueError("audio must contain mono or stereo frames")
    if not np.isfinite(data).all():
        raise ValueError("audio contains nonfinite samples")
    return np.ascontiguousarray(data)


def linear_resample(audio: np.ndarray, target_frames: int) -> np.ndarray:
    """Deterministic linear resampling. Duration changes together with pitch."""
    data = _audio(audio)
    target_frames = int(target_frames)
    if target_frames < 1:
        raise ValueError("target frame count must be positive")
    if target_frames == len(data):
        return data.copy()
    if len(data) == 1:
        return np.repeat(data, target_frames, axis=0)
    positions = np.linspace(0.0, len(data) - 1.0, target_frames, dtype=np.float64)
    left = np.floor(positions).astype(np.int64)
    right = np.minimum(left + 1, len(data) - 1)
    frac = (positions - left).astype(np.float32)[:, None]
    return np.asarray(data[left] * (1.0 - frac) + data[right] * frac, dtype=np.float32)


def _grain_stretch(audio: np.ndarray, target_frames: int, grain: int, hop: int) -> np.ndarray:
    """Pitch-preserving overlap/add suitable for rhythmic or textural material."""
    data = _audio(audio)
    target_frames = int(target_frames)
    if target_frames < 1:
        raise ValueError("target frame count must be positive")
    if len(data) < 16 or target_frames < 16:
        return linear_resample(data, target_frames)
    grain = max(64, min(int(grain), max(64, len(data))))
    hop = max(16, min(int(hop), grain // 2))
    factor = target_frames / len(data)
    window = np.hanning(grain).astype(np.float32)[:, None]
    out = np.zeros((target_frames + grain, data.shape[1]), dtype=np.float32)
    weight = np.zeros((target_frames + grain, 1), dtype=np.float32)
    out_start = 0
    while out_start < target_frames:
        source_center = out_start / max(factor, 1e-9)
        source_start = int(round(source_center))
        source_start = max(0, min(max(0, len(data) - grain), source_start))
        block = data[source_start : source_start + grain]
        if len(block) < grain:
            padded = np.zeros((grain, data.shape[1]), dtype=np.float32)
            padded[: len(block)] = block
            block = padded
        last = min(len(out), out_start + grain)
        take = last - out_start
        out[out_start:last] += block[:take] * window[:take]
        weight[out_start:last] += window[:take]
        out_start += hop
    np.maximum(weight, np.float32(1e-4), out=weight)
    result = out[:target_frames] / weight[:target_frames]
    return np.asarray(np.clip(result, -1.0, 1.0), dtype=np.float32)


def _phase_vocoder_channel(
    channel: np.ndarray, target_frames: int, n_fft: int, hop: int
) -> np.ndarray:
    x = np.asarray(channel, dtype=np.float32)
    if len(x) < n_fft:
        padded = np.zeros(n_fft, dtype=np.float32)
        padded[: len(x)] = x
        x = padded
    original_length = len(channel)
    factor = target_frames / max(1, original_length)
    pad = n_fft
    padded = np.pad(x, (pad, pad), mode="constant")
    starts = np.arange(0, max(1, len(padded) - n_fft + 1), hop, dtype=np.int64)
    if len(starts) < 2:
        return linear_resample(channel[:, None], target_frames)[:, 0]
    window = np.hanning(n_fft).astype(np.float64)
    spectra = np.empty((len(starts), n_fft // 2 + 1), dtype=np.complex128)
    for index, start in enumerate(starts):
        spectra[index] = np.fft.rfft(padded[start : start + n_fft] * window)

    step = 1.0 / max(factor, 1e-9)
    positions = np.arange(0.0, max(1.0, len(spectra) - 1.0), step, dtype=np.float64)
    if not len(positions):
        return linear_resample(channel[:, None], target_frames)[:, 0]
    phase_advance = 2.0 * np.pi * hop * np.arange(n_fft // 2 + 1) / n_fft
    phase = np.angle(spectra[0])
    output_spec = np.empty((len(positions), n_fft // 2 + 1), dtype=np.complex128)
    for out_index, position in enumerate(positions):
        left = min(int(math.floor(position)), len(spectra) - 2)
        right = left + 1
        alpha = position - left
        magnitude = (1.0 - alpha) * np.abs(spectra[left]) + alpha * np.abs(spectra[right])
        delta = np.angle(spectra[right]) - np.angle(spectra[left]) - phase_advance
        delta -= 2.0 * np.pi * np.round(delta / (2.0 * np.pi))
        if out_index:
            phase = phase + phase_advance + delta
        output_spec[out_index] = magnitude * np.exp(1j * phase)

    synth_length = max(target_frames + 2 * pad + n_fft, (len(output_spec) - 1) * hop + n_fft)
    out = np.zeros(synth_length, dtype=np.float64)
    norm = np.zeros(synth_length, dtype=np.float64)
    for index, spectrum in enumerate(output_spec):
        frame = np.fft.irfft(spectrum, n_fft).real * window
        start = index * hop
        out[start : start + n_fft] += frame
        norm[start : start + n_fft] += window * window
    safe = norm > 1e-10
    out[safe] /= norm[safe]
    start = pad
    result = out[start : start + target_frames]
    if len(result) < target_frames:
        result = np.pad(result, (0, target_frames - len(result)))
    return np.asarray(np.clip(result, -1.0, 1.0), dtype=np.float32)


def phase_vocoder_stretch(
    audio: np.ndarray, target_frames: int, *, n_fft: int = 2048, hop: int = 512
) -> np.ndarray:
    """Pitch-preserving STFT stretch for melodic and full-mix material."""
    data = _audio(audio)
    target_frames = int(target_frames)
    if target_frames < 1:
        raise ValueError("target frame count must be positive")
    if target_frames == len(data):
        return data.copy()
    n_fft = int(max(256, min(8192, n_fft)))
    hop = int(max(64, min(n_fft // 2, hop)))
    channels = [
        _phase_vocoder_channel(data[:, channel], target_frames, n_fft, hop)
        for channel in range(data.shape[1])
    ]
    return np.ascontiguousarray(np.column_stack(channels), dtype=np.float32)


def stretch_audio(audio: np.ndarray, target_frames: int, mode: str = "complex") -> np.ndarray:
    """Render *audio* to exactly *target_frames* using the selected strategy."""
    mode = str(mode).casefold()
    if mode not in MODES:
        raise ValueError(f"unsupported stretch mode: {mode}")
    data = _audio(audio)
    target_frames = int(target_frames)
    if target_frames < 1:
        raise ValueError("target frame count must be positive")
    if mode == "resample":
        return linear_resample(data, target_frames)
    if mode == "beats":
        return _grain_stretch(data, target_frames, grain=512, hop=128)
    if mode == "percussion":
        return _grain_stretch(data, target_frames, grain=1024, hop=256)
    if mode == "texture":
        return _grain_stretch(data, target_frames, grain=2048, hop=384)
    if mode == "melodic":
        return phase_vocoder_stretch(data, target_frames, n_fft=2048, hop=512)
    return phase_vocoder_stretch(data, target_frames, n_fft=4096, hop=1024)


def apply_fades(audio: np.ndarray, fade_in_frames: int = 0, fade_out_frames: int = 0) -> np.ndarray:
    """Return a copy with smooth equal-power clip fades."""
    data = _audio(audio).copy()
    fade_in_frames = max(0, min(len(data), int(fade_in_frames)))
    fade_out_frames = max(0, min(len(data), int(fade_out_frames)))
    if fade_in_frames:
        phase = np.linspace(0.0, np.pi / 2.0, fade_in_frames, dtype=np.float32)
        data[:fade_in_frames] *= np.sin(phase)[:, None]
    if fade_out_frames:
        phase = np.linspace(np.pi / 2.0, 0.0, fade_out_frames, dtype=np.float32)
        data[-fade_out_frames:] *= np.sin(phase)[:, None]
    return data
