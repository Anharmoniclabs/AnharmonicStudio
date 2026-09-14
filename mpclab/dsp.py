"""Audio I/O and analysis. ffmpeg does the decoding, numpy does the thinking."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import numpy as np
from .runtime_paths import external_environment, media_tool, subprocess_options

SR = 44100
FFMPEG = media_tool("ffmpeg") or "ffmpeg"
FFPROBE = media_tool("ffprobe") or "ffprobe"


class AudioError(RuntimeError):
    pass


def probe(path: Path) -> dict:
    """Duration / channels / sample rate of any file ffmpeg can open."""
    cmd = [
        FFPROBE,
        "-v",
        "error",
        "-print_format",
        "json",
        "-show_format",
        "-show_streams",
        "-select_streams",
        "a:0",
        str(path),
    ]
    out = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        env=external_environment(FFPROBE),
        **subprocess_options(),
    )
    if out.returncode != 0:
        raise AudioError(out.stderr.strip()[:400] or "ffprobe failed")
    info = json.loads(out.stdout or "{}")
    streams = info.get("streams") or []
    if not streams:
        raise AudioError("no audio stream found")
    s = streams[0]
    dur = float(info.get("format", {}).get("duration") or s.get("duration") or 0.0)
    return {
        "duration": dur,
        "channels": int(s.get("channels") or 2),
        "sample_rate": int(s.get("sample_rate") or SR),
        "codec": s.get("codec_name") or "?",
    }


def to_wav(src: Path, dst: Path, sr: int = SR, channels: int = 2) -> Path:
    """Transcode anything to a high-quality 24-bit WAV.

    Prefer SoXR's very-high-quality resampler.  FFmpeg builds without libsoxr
    reject that filter at runtime, so retry with a tuned native SWResampler
    configuration instead of making imports depend on a particular distro
    build.
    """
    dst.parent.mkdir(parents=True, exist_ok=True)
    attempts = (
        (
            "SoXR",
            f"aresample={sr}:resampler=soxr:precision=28:cutoff=0.95:"
            "dither_method=triangular_hp:output_sample_bits=24",
        ),
        (
            "SWR fallback",
            f"aresample={sr}:resampler=swr:filter_size=64:phase_shift=10:"
            "exact_rational=1:cutoff=0.95:dither_method=triangular_hp:"
            "output_sample_bits=24",
        ),
    )
    errors: list[str] = []
    for label, resample_filter in attempts:
        cmd = [
            FFMPEG,
            "-v",
            "error",
            "-nostdin",
            "-y",
            "-i",
            str(src),
            "-map",
            "a:0",
            "-af",
            resample_filter,
            "-ac",
            str(channels),
            "-ar",
            str(sr),
            "-c:a",
            "pcm_s24le",
            str(dst),
        ]
        out = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            env=external_environment(FFMPEG),
            **subprocess_options(),
        )
        if out.returncode == 0 and dst.exists():
            return dst
        detail = (out.stderr or "").strip()[:300]
        errors.append(f"{label}: {detail or 'ffmpeg produced no output'}")

    raise AudioError("; ".join(errors)[:400] or "ffmpeg transcode failed")


def read_mono(path: Path, sr: int = SR) -> np.ndarray:
    """Decode to a mono float32 array via a raw ffmpeg pipe."""
    cmd = [
        FFMPEG,
        "-v",
        "error",
        "-i",
        str(path),
        "-map",
        "a:0",
        "-ac",
        "1",
        "-ar",
        str(sr),
        "-f",
        "f32le",
        "-c:a",
        "pcm_f32le",
        "-",
    ]
    out = subprocess.run(
        cmd, capture_output=True, env=external_environment(FFMPEG), **subprocess_options()
    )
    if out.returncode != 0:
        raise AudioError(out.stderr.decode(errors="replace").strip()[:400])
    # frombuffer views immutable bytes, so take a writable copy.
    x = np.frombuffer(out.stdout, dtype="<f4").astype(np.float32)
    return np.nan_to_num(x, copy=False)


# --------------------------------------------------------------------------
# analysis
# --------------------------------------------------------------------------

HOP = 512
WIN = 2048


def _spectral_batches(x: np.ndarray, win: int = WIN, hop: int = HOP):
    """Analyze at most 256 overlapping windows at once, including batch seams."""
    if x.size < win:
        x = np.pad(x, (0, win - x.size))
    windows = np.lib.stride_tricks.sliding_window_view(x, win)[::hop]
    taper = np.hanning(win).astype(np.float32)
    for start in range(0, len(windows), 256):
        frames = windows[start : start + 256] * taper
        yield np.abs(np.fft.rfft(frames, axis=1)).astype(np.float32)


def onset_envelope(x: np.ndarray, sr: int = SR) -> np.ndarray:
    """Log-magnitude spectral flux — the standard transient detector."""
    env = np.empty(1 + max(0, x.size - WIN) // HOP, dtype=np.float32)
    previous = None
    start = 0
    for mag in _spectral_batches(x):
        log = np.log1p(mag * 8.0)
        flux = np.diff(log, axis=0, prepend=log[:1] if previous is None else previous)
        env[start : start + len(log)] = np.maximum(flux, 0.0).sum(axis=1)
        previous = log[-1:].copy()
        start += len(log)
    if env.max() > 0:
        env /= env.max()
    return env


def detect_bpm(x: np.ndarray, sr: int = SR, lo: float = 60.0, hi: float = 190.0) -> float:
    """Autocorrelate the onset envelope, pick the strongest plausible tempo."""
    env = onset_envelope(x, sr)
    if env.size < 32:
        return 120.0
    env = env - env.mean()
    ac = np.correlate(env, env, mode="full")[env.size - 1 :]
    fps = sr / HOP
    lags = np.arange(ac.size)
    with np.errstate(divide="ignore", invalid="ignore"):
        bpms = 60.0 * fps / lags
    band = (bpms >= lo) & (bpms <= hi) & (lags > 0)
    if not band.any():
        return 120.0
    scores = ac.copy()
    # Reward tempos whose multiples also line up, so we lock onto the pulse
    # rather than an arbitrary sub-multiple of it.
    for mult in (2, 4):
        shifted = np.zeros_like(scores)
        src = lags * mult
        ok = src < ac.size
        shifted[ok] = ac[src[ok]]
        scores += 0.5 * shifted
    scores[~band] = -np.inf
    best = int(np.argmax(scores))
    bpm = float(60.0 * fps / best)
    while bpm < lo * 1.35 and bpm * 2 <= hi:
        bpm *= 2
    return round(bpm, 2)


def pick_peaks(
    env: np.ndarray, fps: float, sensitivity: float = 1.0, min_gap_ms: float = 45.0
) -> list[int]:
    """Adaptive-threshold peak picking on any onset envelope.

    The threshold is a local median plus a scaled local spread, so a quiet
    passage is judged against its own neighbourhood rather than the loudest
    bar in the file. Returns frame indices.
    """
    if env.size < 4:
        return []
    w = max(1, round(0.175 * fps))
    pad = np.pad(env, (w, w), mode="edge")
    strides = np.lib.stride_tricks.sliding_window_view(pad, 2 * w + 1)
    local_med = np.median(strides, axis=1)
    local_dev = strides.std(axis=1)
    delta = 0.55 / max(sensitivity, 0.05)
    # A fixed floor after whole-file normalization loses quiet phrases next
    # to loud hits. Scale the floor to this neighbourhood too.
    floor = np.maximum(1e-7, 0.008 * strides.max(axis=1))
    thresh = local_med + delta * local_dev + floor

    min_gap = int(min_gap_ms / 1000.0 * fps)
    peaks: list[int] = []
    for i in range(1, env.size - 1):
        if env[i] < thresh[i]:
            continue
        if env[i] < env[i - 1] or env[i] < env[i + 1]:
            continue
        if peaks and i - peaks[-1] < min_gap:
            if env[i] > env[peaks[-1]]:
                peaks[-1] = i
            continue
        peaks.append(i)
    return peaks


def detect_onsets(
    x: np.ndarray, sr: int = SR, sensitivity: float = 1.0, min_gap_ms: float = 45.0
) -> list[float]:
    """Return transient positions in seconds. sensitivity 0.2 (few) .. 2.5 (many)."""
    env = onset_envelope(x, sr)
    if env.size < 4:
        return [0.0]
    peaks = pick_peaks(env, sr / HOP, sensitivity, min_gap_ms)
    times = [max(0.0, (p * HOP) / sr - 0.006) for p in peaks]
    if not times or times[0] > 0.05:
        times.insert(0, 0.0)
    return [round(t, 5) for t in times]


def peaks_for_display(path: Path, buckets: int = 4000) -> list[float]:
    """Min/max envelope pairs so the UI can draw before it finishes decoding."""
    x = read_mono(path)
    if x.size == 0:
        return []
    n = min(buckets, max(1, x.size // 16))
    edges = np.linspace(0, x.size, n + 1).astype(int)
    out: list[float] = []
    for i in range(n):
        seg = x[edges[i] : edges[i + 1]]
        if seg.size == 0:
            out.extend((0.0, 0.0))
        else:
            out.extend((float(seg.min()), float(seg.max())))
    return [round(v, 4) for v in out]


def analyze(path: Path, sensitivity: float = 1.0) -> dict:
    x = read_mono(path)
    dur = x.size / SR
    return {
        "duration": round(dur, 4),
        "bpm": detect_bpm(x),
        "onsets": detect_onsets(x, sensitivity=sensitivity),
        "peak": round(float(np.abs(x).max()) if x.size else 0.0, 4),
        "rms": round(float(np.sqrt((x**2).mean())) if x.size else 0.0, 4),
    }
