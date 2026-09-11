"""Validated musical events and non-destructive arrangement automation."""

from dataclasses import dataclass, field
import math

import numpy as np


@dataclass
class Note:
    pitch: int = 60
    start: float = 0.0
    duration: float = 1.0
    velocity: float = 0.8
    pad: int | None = None  # None = existing synth; otherwise a stable sample slot (0..63)

    def __post_init__(self):
        if self.pad is not None and (type(self.pad) is not int or not 0 <= self.pad < 64):
            raise ValueError("note pad must be an integer from 0 to 63, or null for synth")
        if type(self.pitch) is not int or not 0 <= self.pitch <= 127:
            raise ValueError("note pitch must be an integer from 0 to 127")
        for name in ("start", "duration", "velocity"):
            value = getattr(self, name)
            if not isinstance(value, (int, float)) or not math.isfinite(value):
                raise ValueError(f"note {name} must be finite")
        if not 0 <= self.start <= 1_000_000 or not 0 < self.duration <= 4096:
            raise ValueError("note position or duration is out of range")
        if not 0 < self.velocity <= 1:
            raise ValueError("note velocity must be between 0 and 1")


@dataclass
class AutomationPoint:
    beat: float = 0.0
    value: float = 1.0

    def __post_init__(self):
        if (
            not all(
                isinstance(v, (int, float)) and math.isfinite(v) for v in (self.beat, self.value)
            )
            or not 0 <= self.beat <= 1_000_000
        ):
            raise ValueError("automation points require finite values and nonnegative beats")


@dataclass
class AutomationLane:
    target: str = "master"
    points: list[AutomationPoint] = field(default_factory=list)
    enabled: bool = True
    interpolation: str = "linear"

    def __post_init__(self):
        if self.target not in automation_targets():
            raise ValueError(f"unsupported automation target: {self.target}")
        if self.interpolation not in ("linear", "step", "smooth"):
            raise ValueError("automation interpolation must be linear, step or smooth")
        lo, hi = target_range(self.target)
        if any(not lo <= p.value <= hi for p in self.points):
            raise ValueError("automation value outside target range")
        self.points.sort(key=lambda p: p.beat)
        if len({p.beat for p in self.points}) != len(self.points):
            raise ValueError("automation point positions must be unique")

    def values(self, beats, *, points=None):
        points = self.points if points is None else points
        if not points:
            raise ValueError("Cannot evaluate an empty automation lane")
        positions = np.asarray([p.beat for p in points], dtype=np.float64)
        values = np.asarray([p.value for p in points], dtype=np.float64)
        samples = np.asarray(beats, dtype=np.float64)
        if self.interpolation == "step":
            indices = np.clip(
                np.searchsorted(positions, samples, side="right") - 1, 0, len(values) - 1
            )
            return values[indices]
        if self.interpolation == "smooth" and len(points) > 1:
            # Per-segment cubic smoothstep keeps point values exact while easing
            # both ends of each transition. It is deterministic in live/offline
            # renderers because both call this shared evaluator.
            left = np.clip(
                np.searchsorted(positions, samples, side="right") - 1,
                0,
                len(values) - 2,
            )
            start = positions[left]
            span = positions[left + 1] - start
            t = np.clip((samples - start) / span, 0.0, 1.0)
            eased = t * t * (3.0 - 2.0 * t)
            result = values[left] + (values[left + 1] - values[left]) * eased
            return np.where(samples <= positions[0], values[0], np.where(samples >= positions[-1], values[-1], result))
        return np.interp(samples, positions, values)

    def put(self, beat, value):
        lo, hi = target_range(self.target)
        AutomationPoint(beat, value)
        point = AutomationPoint(round(max(0, beat), 6), min(hi, max(lo, value)))
        self.points = sorted(
            [p for p in self.points if p.beat != point.beat] + [point], key=lambda p: p.beat
        )


def automation_targets():
    return ["master"] + [f"track:{i}:{param}" for i in range(8) for param in ("gain", "pan")]


def target_range(target):
    return (-1.0, 1.0) if target.endswith(":pan") else (0.0, 1.3)


def automation_values(project, target, beats, default):
    for lane in project.automation:
        points = lane.points
        if lane.target == target and lane.enabled and points:
            return lane.values(beats, points=points)
    return default


def read_notes(data):
    if not isinstance(data, list) or len(data) > 100_000:
        raise ValueError("pattern notes must be an array of at most 100000 notes")
    if any(not isinstance(item, dict) for item in data):
        raise ValueError("pattern notes must be objects")
    return [Note(**{k: v for k, v in item.items() if k in Note.__annotations__}) for item in data]


def read_automation(data):
    if not isinstance(data, list) or len(data) > 17:
        raise ValueError("automation must be an array of at most 17 lanes")
    lanes = []
    for item in data:
        if not isinstance(item, dict):
            raise ValueError("automation lanes must be objects")
        points = item.get("points", [])
        if not isinstance(points, list) or len(points) > 100_000:
            raise ValueError("automation points must be an array of at most 100000 points")
        if any(not isinstance(p, dict) for p in points):
            raise ValueError("automation points must be objects")
        lanes.append(
            AutomationLane(
                target=item.get("target", "master"),
                enabled=bool(item.get("enabled", True)),
                interpolation=item.get("interpolation", "linear"),
                points=[AutomationPoint(**p) for p in points],
            )
        )
    if len({lane.target for lane in lanes}) != len(lanes):
        raise ValueError("automation targets must be unique")
    return lanes
