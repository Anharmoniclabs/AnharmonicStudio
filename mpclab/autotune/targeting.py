"""One correction plan shared by the pitch editor and renderer."""

from __future__ import annotations
import numpy as np


def targets_for(analysis, settings):
    from ..vocal import _quantize_pitch_curve, allowed_notes

    voiced = np.isfinite(analysis.detected_midi) & (analysis.confidence >= 0.35)
    return _quantize_pitch_curve(
        analysis.detected_midi, voiced, allowed_notes(settings), settings.transpose
    )


def notes_from_analysis(analysis, duration, settings=None):
    notes = []
    for _i, (time, target, confidence) in enumerate(
        zip(
            analysis.times,
            targets_for(analysis, settings) if settings is not None else analysis.target_midi,
            analysis.confidence,
            strict=True,
        )
    ):
        if not np.isfinite(target) or confidence < 0.35:
            continue
        dt = float(analysis.times[1] - analysis.times[0]) if len(analysis.times) > 1 else 0.01
        start, end = max(0.0, float(time) - dt / 2), min(duration, float(time) + dt / 2)
        if end <= start:
            continue
        if notes and abs(notes[-1]["end"] - start) < 0.001 and notes[-1]["target"] == float(target):
            notes[-1]["end"] = end
        else:
            notes.append(
                dict(start=start, end=end, target=float(target), bypass=False, strength=1.0)
            )
    return notes


def correction(analysis, settings, notes=(), cancelled=None):
    from ..vocal import _check_cancel

    detected = analysis.detected_midi
    target = targets_for(analysis, settings)
    depth = np.full(len(detected), float(settings.strength))
    voiced = np.isfinite(detected) & (analysis.confidence >= 0.35)
    for note in notes:
        _check_cancel(cancelled)
        lo, hi = np.searchsorted(analysis.times, [note["start"], note["end"]], side="left")
        target[lo:hi] = note["target"]
        depth[lo:hi] *= 0 if note.get("bypass", False) else note.get("strength", 1.0)
    # Preserve fast within-note motion while correcting its central pitch.
    trend = detected.copy()
    indices = np.flatnonzero(voiced)
    for start in range(0, len(indices), 4096):
        _check_cancel(cancelled)
        rows = indices[start : start + 4096]
        neighbors = rows[:, None] + np.arange(-5, 6)[None, :]
        inside = (neighbors >= 0) & (neighbors < len(detected))
        neighbors = np.clip(neighbors, 0, len(detected) - 1)
        valid = inside & voiced[neighbors] & (target[neighbors] == target[rows, None])
        values = np.where(valid, detected[neighbors], np.nan)
        trend[rows] = np.nanmedian(values, axis=1)
    error = np.zeros(len(detected))
    error[voiced] = (
        target[voiced] - detected[voiced] + settings.humanize * (detected[voiced] - trend[voiced])
    ) * depth[voiced]
    state = 0.0
    for i in range(len(error)):
        if i % 128 == 0:
            _check_cancel(cancelled)
        dt = float(analysis.times[i] - analysis.times[i - 1]) if i else 0.01
        alpha = 1.0 if settings.retune_ms <= 0 else -np.expm1(-dt * 1000 / settings.retune_ms)
        state += alpha * (error[i] - state)
        error[i] = state if voiced[i] else 0.0
    if not settings.enabled:
        error[:] = 0
    return np.exp2(np.clip(error, -24, 24) / 12)
