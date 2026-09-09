"""Advanced mixer control layered over the existing eight-track audio engine.

Groups and sidechains deliberately operate on the already-rendered track control
values.  This keeps the realtime callback allocation-free and preserves the
current insert/send graph while adding project-persisted summing control and
meter-driven ducking.
"""

from __future__ import annotations

import numpy as np

from . import engine_mixing
from .workflow_state import ensure_workflow

_INSTALLED = False
_ORIGINAL_TRACK_CONTROLS = engine_mixing.track_controls


def _multiply(value, amount: float):
    if isinstance(value, np.ndarray):
        return value * np.float32(amount)
    return value * amount


def advanced_track_controls(engine, index: int, beats):
    left, right = _ORIGINAL_TRACK_CONTROLS(engine, index, beats)
    project = engine.project
    if not 0 <= index < len(project.tracks):
        return left, right
    workflow = ensure_workflow(project)
    track_id = project.tracks[index].id

    # A group acts like a VCA/summing-control layer over member channels.  It
    # intentionally does not create another audio bus, so no callback buffers
    # or plugin ordering change is required.
    for group in workflow.get("groups", []):
        if track_id not in group.get("members", []):
            continue
        if group.get("mute", False):
            return _multiply(left, 0.0), _multiply(right, 0.0)
        gain = max(0.0, min(2.0, float(group.get("gain", 1.0))))
        left, right = _multiply(left, gain), _multiply(right, gain)

    # Sidechain control uses the source track's previous callback RMS.  That
    # one-block lookback is stable, bounded and avoids scanning source audio in
    # the target processing path.  The curve behaves like a simple compressor
    # control signal; it can later drive a dedicated dynamics bus unchanged.
    for route in workflow.get("sidechains", []):
        if not route.get("enabled", True) or route.get("target") != track_id:
            continue
        try:
            source_index = project.track_index(route.get("source", ""))
        except KeyError:
            continue
        level = float(engine.meters[source_index])
        threshold = max(0.0, min(1.0, float(route.get("threshold", 0.05))))
        amount = max(0.0, min(32.0, float(route.get("amount", 4.0))))
        excess = max(0.0, level - threshold)
        duck = 1.0 / (1.0 + amount * excess)
        left, right = _multiply(left, duck), _multiply(right, duck)
    return left, right


def install_advanced_track_controls() -> None:
    global _INSTALLED
    if _INSTALLED:
        return
    engine_mixing.track_controls = advanced_track_controls
    _INSTALLED = True
