"""Camera-independent gesture mapping and Prism automation curves."""

from __future__ import annotations

from dataclasses import dataclass
import math

# Normalized VST parameter indices, shared with prism_parameters.json.
CONTROLS = {
    "54": "Tone",
    "56": "Space",
    "57": "Texture",
    "55": "Motion",
    "53": "Layer A / B",
    "12": "Filter cutoff",
    "13": "Resonance",
    "26": "Delay mix",
    "29": "Reverb mix",
}
TARGETS = tuple("prism:" + key for key in CONTROLS)
DEFAULT_MAPPING = ("54", "56", "57")


def clamp(value):
    return max(0.0, min(1.0, value))


def automation_parameters(project, beat, excluded=()):
    """Evaluate enabled curves at block start; camera gestures temporarily win."""
    result = {}
    for lane in project.automation:
        if lane.enabled and lane.points and lane.target in TARGETS:
            key = lane.target.split(":")[1]
            if key not in excluded:
                result[key] = float(lane.values([beat])[0])
    return result


@dataclass(frozen=True)
class GestureFrame:
    timestamp: float
    points: tuple[tuple[float, float, float], ...]

    def signals(self):
        if len(self.points) != 21 or not math.isfinite(self.timestamp):
            return None
        if any(len(p) != 3 or not all(math.isfinite(v) for v in p) for p in self.points):
            return None

        def distance(a, b):
            return math.hypot(
                self.points[a][0] - self.points[b][0], self.points[a][1] - self.points[b][1]
            )

        palm = distance(5, 17)
        if palm < 0.025:
            return None
        # Thumb/index pinch is the clutch; the remaining fingertips stay free.
        pinch = distance(4, 8) / palm
        x = (self.points[4][0] + self.points[8][0]) / 2
        y = 1 - (self.points[4][1] + self.points[8][1]) / 2
        spread = clamp(distance(12, 20) / (palm * 2))
        return pinch, (clamp(x), clamp(y), spread)


class PinchMapper:
    """Relative pickup, pinch hysteresis and time-based smoothing."""

    def __init__(self, mapping=DEFAULT_MAPPING, sensitivity=1.5, smoothing=0.12):
        if len(mapping) != 3 or len(set(mapping)) != 3 or any(k not in CONTROLS for k in mapping):
            raise ValueError("Choose three different Prism controls")
        if not 0.1 <= sensitivity <= 4 or not 0.01 <= smoothing <= 1:
            raise ValueError("Invalid gesture response")
        self.mapping = tuple(mapping)
        self.sensitivity = sensitivity
        self.smoothing = smoothing
        self.release()

    def release(self):
        self.active = False
        self._candidate = None
        self._last = None
        self._anchor = None
        self._base = {}
        self._values = {}

    def update(self, frame, current):
        signals = frame.signals()
        if signals is None or (self._last is not None and frame.timestamp <= self._last):
            self.release()
            return {}
        pinch, position = signals
        if self._last is not None and frame.timestamp - self._last > 0.3:
            self.release()
        dt = 1 / 30 if self._last is None else frame.timestamp - self._last
        self._last = frame.timestamp
        if pinch > (0.45 if self.active else 0.28):
            self.release()
            return {}
        if not self.active:
            if self._candidate is None:
                self._candidate = frame.timestamp
            if frame.timestamp - self._candidate < 0.07:
                return {}
            self.active = True
            self._anchor = position
            self._base = {key: clamp(float(current.get(key, 0))) for key in self.mapping}
            self._values = dict(self._base)
        alpha = 1 - math.exp(-max(0, dt) / self.smoothing)
        for index, key in enumerate(self.mapping):
            wanted = clamp(
                self._base[key] + (position[index] - self._anchor[index]) * self.sensitivity
            )
            self._values[key] += alpha * (wanted - self._values[key])
        return dict(self._values)


# Finger, destination, display name, description, color.
FINGER_EFFECTS = (
    ("Index", "12", "Filter", "Dark to bright", "#78e5c5"),
    ("Middle", "26", "Echo", "Repeating trails", "#a99aff"),
    ("Ring", "29", "Reverb", "Room to atmosphere", "#79c9ff"),
    ("Pinky", "57", "Texture", "Clean to character", "#ffbc7b"),
)
FINGER_MAPPING = tuple(effect[1] for effect in FINGER_EFFECTS)


class FingerFXMapper:
    """One thumb/finger pinch selects one effect; movement adjusts its amount.

    Relative pickup preserves the playing sound. Release leaves the chosen
    value in place and returns control to any playing automation.
    """

    def __init__(self, mapping=FINGER_MAPPING, sensitivity=1.5, smoothing=0.12):
        if len(mapping) != 4 or len(set(mapping)) != 4 or any(k not in CONTROLS for k in mapping):
            raise ValueError("Give each finger a different effect")
        self.mapping = tuple(mapping)
        self.sensitivity = sensitivity
        self.smoothing = smoothing
        self.release()

    def release(self):
        self.active = False
        self.finger = None
        self._candidate = None
        self._since = None
        self._last = None
        self._value = 0.0
        self._base = 0.0
        self._anchor = 0.0

    def update(self, frame, current):
        if frame.signals() is None:
            self.release()
            return {}
        if self._last is not None and not 0 < frame.timestamp - self._last <= 0.3:
            self.release()
            return {}
        points = frame.points
        palm = math.dist(points[5][:2], points[17][:2])
        gaps = [math.dist(points[4][:2], points[i][:2]) / palm for i in (8, 12, 16, 20)]
        dt = 1 / 30 if self._last is None else frame.timestamp - self._last
        self._last = frame.timestamp
        if self.active:
            finger = self.finger
            if gaps[finger] > 0.45:
                self.release()
                return {}
        else:
            finger = min(range(4), key=gaps.__getitem__)
            if gaps[finger] > 0.28:
                self._candidate = None
                return {}
            if self._candidate != finger:
                self._candidate = finger
                self._since = frame.timestamp
            if frame.timestamp - self._since < 0.07:
                return {}
            self.active = True
            self.finger = finger
            self._base = self._value = clamp(float(current.get(self.mapping[finger], 0)))
            self._anchor = points[4][0] - points[4][1]
        # Right or up adds more; left or down adds less.
        position = points[4][0] - points[4][1]
        wanted = clamp(self._base + (position - self._anchor) * self.sensitivity)
        self._value += (1 - math.exp(-dt / self.smoothing)) * (wanted - self._value)
        return {self.mapping[finger]: self._value}


class GestureTake:
    """Touch-overdub writer: preserve points outside each touched interval."""

    def __init__(self):
        self.project = None
        self.last = {}
        self.original = {}

    def end(self):
        # Return to the pre-existing curve just after the touched region.
        if self.project is not None:
            for key, points in self.original.items():
                if points and key in self.last:
                    target = "prism:" + key
                    lane = next((x for x in self.project.automation if x.target == target), None)
                    beat = min(1_000_000, self.last[key] + 0.001)
                    if lane is not None and len(lane.points) < 100_000:
                        lane.put(beat, float(lane.values([beat], points=points)[0]))
        self.project = None
        self.last.clear()
        self.original.clear()

    def write(self, project, beat, values):
        from .music import AutomationLane

        if not math.isfinite(beat) or not 0 <= beat <= 1_000_000:
            return False
        if any(beat < last for last in self.last.values()):
            self.end()
        if self.project is not project:
            self.end()
            self.project = project
        changed = False
        for key, value in values.items():
            if key not in CONTROLS or not math.isfinite(value):
                continue
            last = self.last.get(key)
            # Quantized sampling bounds curve size without quantizing the gesture.
            if last is not None and 0 <= beat - last < 1 / 32:
                continue
            target = "prism:" + key
            lane = next((x for x in project.automation if x.target == target), None)
            if lane is None:
                lane = AutomationLane(target)
                project.automation = [*project.automation, lane]
            if len(lane.points) >= 99_998:
                raise ValueError("Gesture lane is full; shorten the take or remove points")
            if key not in self.original:
                self.original[key] = list(lane.points)
                if lane.points and beat > 0:
                    before = max(0, beat - 0.001)
                    lane.put(before, float(lane.values([before])[0]))
                elif beat > 0:
                    lane.put(0, clamp(value))
            if len(lane.points) >= 100_000:
                raise ValueError("Gesture lane is full; shorten the take or remove points")
            if last is not None and beat >= last:
                lane.points = [p for p in lane.points if not last < p.beat <= beat]
            lane.put(beat, clamp(value))
            lane.enabled = True
            self.last[key] = beat
            changed = True
        return changed
