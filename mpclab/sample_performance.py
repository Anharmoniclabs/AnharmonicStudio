"""Deterministic mono sample performance helpers shared by live/export paths."""

from __future__ import annotations

from dataclasses import dataclass
import math

import numpy as np


@dataclass(frozen=True, slots=True)
class MonoPerformance:
    glide_ms: float = 0.0
    legato: bool = False
    retrigger: bool = True

    def validate(self) -> None:
        if not math.isfinite(self.glide_ms) or not 0.0 <= self.glide_ms <= 5000.0:
            raise ValueError("glide_ms must be between 0 and 5000")
        if type(self.legato) is not bool or type(self.retrigger) is not bool:
            raise ValueError("legato and retrigger must be booleans")


def glide_curve(
    start_semitones: float,
    end_semitones: float,
    frames: int,
    sample_rate: int,
    glide_ms: float,
) -> np.ndarray:
    """Return per-frame playback-rate multipliers for a constant-time glide.

    Interpolation occurs in semitone space, giving an exponential frequency
    transition. The same function can be used by live and offline renderers.
    """
    if frames < 0 or sample_rate <= 0:
        raise ValueError("invalid frame count or sample rate")
    if not math.isfinite(glide_ms) or glide_ms < 0:
        raise ValueError("glide_ms must be finite and non-negative")
    if frames == 0:
        return np.empty(0, dtype=np.float64)

    transition = min(frames, max(0, int(round(sample_rate * glide_ms / 1000.0))))
    semitones = np.empty(frames, dtype=np.float64)
    if transition <= 1:
        semitones.fill(end_semitones)
    else:
        semitones[:transition] = np.linspace(
            start_semitones,
            end_semitones,
            transition,
            endpoint=True,
            dtype=np.float64,
        )
        if transition < frames:
            semitones[transition:] = end_semitones
    return np.exp2(semitones / 12.0)


def should_restart_envelope(
    *,
    previous_note_held: bool,
    policy: MonoPerformance,
) -> bool:
    """Decide whether a mono note transition restarts its amplitude envelope."""
    policy.validate()
    if not previous_note_held:
        return True
    if policy.legato:
        return False
    return policy.retrigger
