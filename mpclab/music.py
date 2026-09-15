"""Validated musical events and non-destructive arrangement automation."""

from dataclasses import dataclass, field
import math

import numpy as np


@dataclass
class MidiControl:
    beat: float
    message: list[int]
    instrument: str | None = None
    pad: int | None = None

    def __post_init__(self):
        if not isinstance(self.beat, (int, float)) or not math.isfinite(self.beat) or not 0 <= self.beat <= 1_000_000:
            raise ValueError("MIDI control position must be finite and nonnegative")
        if not isinstance(self.message, list) or not self.message:
            raise ValueError("MIDI control message is required")
        status = self.message[0]
        if type(status) is not int or status & 0xF0 not in (0xA0, 0xB0, 0xC0, 0xD0, 0xE0):
            raise ValueError("Unsupported MIDI control status")
        count = 2 if status & 0xF0 in (0xC0, 0xD0) else 3
        if len(self.message) != count or any(type(v) is not int or not 0 <= v < 128 for v in self.message[1:]):
            raise ValueError("Invalid MIDI control data")
        if self.instrument is not None and (not isinstance(self.instrument, str) or not 0 < len(self.instrument) <= 128):
            raise ValueError("Invalid MIDI control instrument")
        if self.pad is not None and (type(self.pad) is not int or not 0 <= self.pad < 64 or self.instrument is not None):
            raise ValueError("Invalid MIDI control sample slot")


def read_midi_controls(data):
    if not isinstance(data, list) or len(data) > 100000 or any(not isinstance(item, dict) for item in data):
        raise ValueError("MIDI controls must be an array of at most 100000 events")
    return [MidiControl(**item) for item in data]


@dataclass
class Note:
    pitch: int = 60
    start: float = 0.0
    duration: float = 1.0
    velocity: float = 0.8
    pad: int | None = None  # None = existing synth; otherwise a stable sample slot (0..63)
    instrument: str | None = None  # Stable additional instrument ID; None preserves legacy synth.
    channel: int = 0  # Original zero-based MIDI channel, retained for interchange.
    release_velocity: int = 0

    def __post_init__(self):
        if self.instrument is not None and (
            not isinstance(self.instrument, str)
            or not self.instrument
            or len(self.instrument) > 128
            or self.pad is not None
        ):
            raise ValueError("note instrument must be a stable ID, mutually exclusive with pad")
        if type(self.channel) is not int or not 0 <= self.channel <= 15:
            raise ValueError("note channel must be an integer from 0 to 15")
        if type(self.release_velocity) is not int or not 0 <= self.release_velocity <= 127:
            raise ValueError("note release velocity must be an integer from 0 to 127")
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
            return np.where(
                samples <= positions[0],
                values[0],
                np.where(samples >= positions[-1], values[-1], result),
            )
        return np.interp(samples, positions, values)

    def put(self, beat, value):
        lo, hi = target_range(self.target)
        AutomationPoint(beat, value)
        point = AutomationPoint(round(max(0, beat), 6), min(hi, max(lo, value)))
        self.points = sorted(
            [p for p in self.points if p.beat != point.beat] + [point], key=lambda p: p.beat
        )


def automation_targets(track_count=None):
    # Local import avoids the model/music dataclass import cycle.
    from .model import MAX_TRACKS

    count = MAX_TRACKS if track_count is None else track_count
    if type(count) is not int or not 1 <= count <= MAX_TRACKS:
        raise ValueError("automation track count is outside the mixer limit")
    from .prism_motion import TARGETS

    return ["master"] + [f"track:{i}:{param}" for i in range(count) for param in ("gain", "pan")] + list(TARGETS)


def target_range(target):
    if target.startswith("prism:"):
        return (0.0, 1.0)
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


def read_automation(data, *, track_count=None):
    targets = set(automation_targets(track_count))
    if not isinstance(data, list) or len(data) > len(targets):
        raise ValueError(f"automation must be an array of at most {len(targets)} lanes")
    lanes = []
    allowed_lane_keys = {"target", "points", "enabled", "interpolation"}
    allowed_point_keys = {"beat", "value"}
    for item in data:
        if not isinstance(item, dict):
            raise ValueError("automation lanes must be objects")
        if set(item) - allowed_lane_keys:
            raise ValueError("automation lane contains unsupported fields")
        target = item.get("target", "master")
        if not isinstance(target, str) or target not in targets:
            raise ValueError(f"unsupported automation target: {target}")
        enabled = item.get("enabled", True)
        if type(enabled) is not bool:
            raise ValueError("automation enabled must be a boolean")
        points = item.get("points", [])
        if not isinstance(points, list) or len(points) > 100_000:
            raise ValueError("automation points must be an array of at most 100000 points")
        if any(not isinstance(p, dict) for p in points):
            raise ValueError("automation points must be objects")
        if any(set(point) - allowed_point_keys for point in points):
            raise ValueError("automation point contains unsupported fields")
        try:
            parsed_points = [AutomationPoint(**point) for point in points]
        except (TypeError, ValueError) as exc:
            raise ValueError("automation point is invalid") from exc
        lanes.append(
            AutomationLane(
                target=target,
                enabled=enabled,
                interpolation=item.get("interpolation", "linear"),
                points=parsed_points,
            )
        )
    if len({lane.target for lane in lanes}) != len(lanes):
        raise ValueError("automation targets must be unique")
    return lanes


def automation_label(target):
    if target == "master":
        return "Master • level"
    if target.startswith("prism:"):
        from .prism_motion import CONTROLS
        return "Prism • " + CONTROLS[target.split(":")[1]]
    return f"Track {int(target.split(':')[1]) + 1} • {target.split(':')[2]}"
