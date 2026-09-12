"""Higher-level arrangement audio clip actions.

These commands are intentionally non-destructive: operations either change only
clip properties or create a new library derivative and repoint the arrangement
clip. The original library source stays available for undo/relink/audit.
"""

from __future__ import annotations

import math
import re

import numpy as np
from PySide6.QtWidgets import QInputDialog, QMenu


_BPM_RE = re.compile(r"(?<!\d)(\d{2,3}(?:\.\d+)?)\s*BPM\b", re.IGNORECASE)
_MAX_NORMALIZE_GAIN = 10.0 ** (24.0 / 20.0)


def source_bpm(meta) -> float | None:
    """Return trustworthy clip metadata BPM, then fall back to the filename."""
    value = getattr(meta, "bpm", None)
    try:
        value = float(value)
    except (TypeError, ValueError):
        value = 0.0
    if 20.0 <= value <= 400.0:
        return value
    match = _BPM_RE.search(str(getattr(meta, "name", "")))
    if not match:
        return None
    value = float(match.group(1))
    return value if 20.0 <= value <= 400.0 else None


def _source_region(owner, clip):
    meta = owner.app.library.clips.get(clip.ref)
    data = owner.app.library.audio(clip.ref)
    if meta is None or data is None:
        return None
    sr = int(getattr(owner.app.library, "sr", getattr(meta, "sample_rate", 48000)))
    start = max(0, min(len(data), int(round(float(clip.offset) * sr))))
    wanted = (
        int(round(float(clip.source_length) * sr))
        if clip.source_length > 0
        else len(data) - start
    )
    end = max(start, min(len(data), start + max(0, wanted)))
    return meta, np.asarray(data[start:end], dtype=np.float32), sr


def _resample_exact(data: np.ndarray, target_frames: int) -> np.ndarray:
    audio = np.asarray(data, dtype=np.float32)
    if audio.ndim == 1:
        audio = np.column_stack((audio, audio))
    if audio.ndim != 2 or audio.shape[1] not in (1, 2):
        raise ValueError("audio clip must be mono or stereo")
    if audio.shape[1] == 1:
        audio = np.repeat(audio, 2, axis=1)
    target_frames = max(1, int(target_frames))
    if len(audio) == 0:
        return np.zeros((target_frames, 2), dtype=np.float32)
    if len(audio) == target_frames:
        return np.ascontiguousarray(audio[:, :2], dtype=np.float32)
    if len(audio) == 1:
        return np.repeat(audio[:, :2], target_frames, axis=0).astype(np.float32, copy=False)
    source_x = np.arange(len(audio), dtype=np.float64)
    target_x = np.linspace(0.0, float(len(audio) - 1), target_frames, dtype=np.float64)
    out = np.empty((target_frames, 2), dtype=np.float32)
    out[:, 0] = np.interp(target_x, source_x, audio[:, 0]).astype(np.float32)
    out[:, 1] = np.interp(target_x, source_x, audio[:, 1]).astype(np.float32)
    return np.ascontiguousarray(out)


def _commit_render(owner, clip, rendered, sr: int, beats: float, name: str):
    parent = clip.ref
    owner.app.snapshot()
    fitted = owner.app.library.add_audio(rendered, name, kind="render", parent=parent)
    clip.ref = fitted.id
    clip.offset = 0.0
    clip.source_length = float(len(rendered)) / sr
    clip.length_beats = float(beats)
    clip.loop = False
    clip.reverse = False
    preload = getattr(owner.app.engine, "preload_project_audio", None)
    if preload is not None:
        preload(owner.app.project)
    owner.changed.emit()
    owner.refresh()
    return clip


def fit_audio_to_beats(owner, clip, beats: float, label: str | None = None):
    """Resample a clip source region to an exact number of project beats."""
    if clip.kind != "audio" or not math.isfinite(beats) or beats <= 0:
        return None
    region = _source_region(owner, clip)
    if region is None:
        owner.app.status.showMessage("The clip source is unavailable", 3000)
        return None
    meta, source, sr = region
    if len(source) < 2:
        owner.app.status.showMessage("The selected source range is too short to fit", 3000)
        return None
    bpm = max(1e-6, float(owner.app.project.bpm))
    target_frames = max(1, int(round(beats * 60.0 / bpm * sr)))
    rendered = _resample_exact(source, target_frames)
    suffix = label or f"{beats:g} beats"
    result = _commit_render(owner, clip, rendered, sr, beats, f"{meta.name} [fit {suffix}]")
    owner.app.status.showMessage(f"Fit audio to {suffix} at {bpm:g} BPM (Resample)", 3500)
    return result


def match_audio_to_project_tempo(owner, clip):
    """Infer the source's musical length and conform it to the project tempo."""
    region = _source_region(owner, clip)
    if region is None:
        owner.app.status.showMessage("The clip source is unavailable", 3000)
        return None
    meta, source, sr = region
    bpm = source_bpm(meta)
    if bpm is None:
        owner.app.status.showMessage("No source BPM metadata or filename BPM was found", 3500)
        return None
    source_seconds = len(source) / sr
    raw_beats = source_seconds * bpm / 60.0
    beats = max(0.25, round(raw_beats * 4.0) / 4.0)
    project_bpm = max(1e-6, float(owner.app.project.bpm))
    result = fit_audio_to_beats(owner, clip, beats, f"tempo {bpm:g}→{project_bpm:g}")
    if result is not None:
        owner.app.status.showMessage(
            f"Matched {bpm:g} BPM source to {project_bpm:g} BPM · {beats:g} beats (Resample)",
            4000,
        )
    return result


def custom_fit_dialog(owner, clip):
    bars, ok = QInputDialog.getDouble(
        owner,
        "Fit audio to bars",
        "Bars (4/4):",
        max(0.25, float(clip.length_beats) / 4.0),
        0.0625,
        256.0,
        4,
    )
    if ok:
        return fit_audio_to_beats(owner, clip, float(bars) * 4.0, f"{bars:g} bars")
    return None


def normalize_clip_gain(owner, clip):
    """Peak-normalize nondestructively through clip gain, capped at +24 dB."""
    region = _source_region(owner, clip)
    if region is None:
        owner.app.status.showMessage("The clip source is unavailable", 3000)
        return None
    _meta, source, _sr = region
    if not len(source):
        return None
    peak = float(np.max(np.abs(source)))
    if not math.isfinite(peak) or peak <= 1e-9:
        owner.app.status.showMessage("The selected source is silent", 3000)
        return None
    owner.app.snapshot()
    clip.gain = min(_MAX_NORMALIZE_GAIN, 1.0 / peak)
    owner.changed.emit()
    owner.update()
    db = 20.0 * math.log10(max(1e-12, clip.gain))
    owner.app.status.showMessage(f"Normalized clip gain to {db:+.1f} dB", 3000)
    return clip


def reset_clip_gain(owner, clip):
    owner.app.snapshot()
    clip.gain = 1.0
    owner.changed.emit()
    owner.update()


def use_full_source(owner, clip):
    meta = owner.app.library.clips.get(clip.ref)
    if meta is None:
        return None
    duration = max(0.001, float(getattr(meta, "duration", 0.0)))
    owner.app.snapshot()
    clip.offset = 0.0
    clip.source_length = duration
    clip.length_beats = duration * max(1e-6, float(owner.app.project.bpm)) / 60.0
    clip.loop = False
    owner.changed.emit()
    owner.refresh()
    owner.app.status.showMessage("Using the full audio source", 2500)
    return clip


def make_audio_unique(owner, clip):
    """Copy the current source region into a private library derivative."""
    region = _source_region(owner, clip)
    if region is None:
        owner.app.status.showMessage("The clip source is unavailable", 3000)
        return None
    meta, source, sr = region
    if not len(source):
        return None
    parent = clip.ref
    owner.app.snapshot()
    unique = owner.app.library.add_audio(
        np.ascontiguousarray(source, dtype=np.float32),
        f"{meta.name} [unique]",
        kind="render",
        parent=parent,
    )
    clip.ref = unique.id
    clip.offset = 0.0
    clip.source_length = float(len(source)) / sr
    preload = getattr(owner.app.engine, "preload_project_audio", None)
    if preload is not None:
        preload(owner.app.project)
    owner.changed.emit()
    owner.refresh()
    owner.app.status.showMessage("Made audio clip unique", 2500)
    return clip


def add_audio_clip_actions(menu: QMenu, owner, clip) -> None:
    """Append the organized Audio Clip V2 commands to an existing context menu."""
    meta = owner.app.library.clips.get(clip.ref)
    detected = source_bpm(meta) if meta is not None else None

    tempo = menu.addMenu("Tempo & stretch")
    if detected is not None:
        match = tempo.addAction(
            f"Match project tempo · {detected:g} → {float(owner.app.project.bpm):g} BPM"
        )
        match.triggered.connect(lambda _checked=False: match_audio_to_project_tempo(owner, clip))
        info = tempo.addAction(f"Detected source tempo · {detected:g} BPM")
        info.setEnabled(False)
    else:
        info = tempo.addAction("Source tempo · not detected")
        info.setEnabled(False)

    fit = tempo.addMenu("Fit to bars · Resample")
    for bars in (1, 2, 4, 8, 16):
        fit.addAction(
            f"{bars} bar{'s' if bars != 1 else ''}",
            lambda _checked=False, n=bars: fit_audio_to_beats(
                owner, clip, n * 4.0, f"{n} bar{'s' if n != 1 else ''}"
            ),
        )
    fit.addSeparator()
    fit.addAction("Custom…", lambda: custom_fit_dialog(owner, clip))

    source = menu.addMenu("Source")
    source.addAction("Make audio unique", lambda: make_audio_unique(owner, clip))
    source.addAction("Use full source", lambda: use_full_source(owner, clip))

    gain = menu.addMenu("Gain")
    gain.addAction("Normalize peak", lambda: normalize_clip_gain(owner, clip))
    gain.addAction("Reset to 0 dB", lambda: reset_clip_gain(owner, clip))
