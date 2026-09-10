"""Backward-compatible project metadata for advanced editor workflows.

The core project format intentionally stays at version 5.  Older builds already
ignore unknown top-level keys, so a bounded ``workflow`` object can travel with
a song without forcing a schema migration or changing realtime dataclasses.
"""

from __future__ import annotations

from copy import deepcopy
import math

from .model import Project
from .workflow_routing import MAX_ROUTING_BUSES


_ALLOWED = {
    "clip_edits",
    "freezes",
    "groups",
    "recording",
    "routing",
    "sidechains",
    "scenes",
    "track_presets",
}
_INSTALLED = False
_ORIGINAL_TO_DICT = Project.to_dict
_ORIGINAL_FROM_DICT = Project.from_dict.__func__


def _bounded_text(value, limit=128) -> str:
    text = str(value or "")
    if len(text) > limit:
        raise ValueError("workflow text field is too long")
    return text


def _bounded_number(value, low: float, high: float, label: str) -> float:
    number = float(value)
    if not math.isfinite(number):
        raise ValueError(f"{label} must be finite")
    return max(low, min(high, number))


def _check_routing_acyclic(buses: list[dict], sends: list[dict]) -> None:
    ids = [item["id"] for item in buses]
    bus_ids = set(ids)
    edges = {bus_id: set() for bus_id in ids}
    indegree = dict.fromkeys(ids, 0)

    def connect(source: str, target: str) -> None:
        if source not in bus_ids or target not in bus_ids or target in edges[source]:
            return
        edges[source].add(target)
        indegree[target] += 1

    for item in buses:
        connect(item["id"], item["output"])
    for item in sends:
        connect(item["source"], item["target"])

    ready = [bus_id for bus_id in ids if indegree[bus_id] == 0]
    visited = 0
    while ready:
        source = ready.pop()
        visited += 1
        for target in edges[source]:
            indegree[target] -= 1
            if indegree[target] == 0:
                ready.append(target)
    if visited != len(ids):
        raise ValueError("routing buses must form an acyclic graph")


def _validate_routing(value) -> dict:
    if value is None:
        return {}
    if not isinstance(value, dict) or set(value) - {"buses", "track_outputs", "sends"}:
        raise ValueError("routing metadata contains unsupported keys")

    raw_buses = value.get("buses", [])
    if not isinstance(raw_buses, list) or len(raw_buses) > MAX_ROUTING_BUSES:
        raise ValueError(f"routing supports at most {MAX_ROUTING_BUSES} buses")
    buses = []
    bus_ids = set()
    for item in raw_buses:
        if not isinstance(item, dict):
            raise ValueError("routing bus must be an object")
        bus_id = _bounded_text(item.get("id"))
        if not bus_id or bus_id == "master" or bus_id in bus_ids:
            raise ValueError("routing bus ids must be unique and non-empty")
        bus_ids.add(bus_id)
        buses.append(
            {
                "id": bus_id,
                "name": _bounded_text(item.get("name") or "BUS", 96),
                "gain": _bounded_number(item.get("gain", 1.0), 0.0, 2.0, "bus gain"),
                "pan": _bounded_number(item.get("pan", 0.0), -1.0, 1.0, "bus pan"),
                "mute": bool(item.get("mute", False)),
                "output": _bounded_text(item.get("output") or "master"),
            }
        )
    for item in buses:
        if item["output"] != "master" and item["output"] not in bus_ids:
            raise ValueError("routing bus output does not exist")

    raw_outputs = value.get("track_outputs", {})
    if not isinstance(raw_outputs, dict) or len(raw_outputs) > 64:
        raise ValueError("track outputs must be a bounded object")
    track_outputs = {}
    for track_id, target in raw_outputs.items():
        track_id = _bounded_text(track_id)
        target = _bounded_text(target)
        if not track_id or (target != "master" and target not in bus_ids):
            raise ValueError("track output target does not exist")
        track_outputs[track_id] = target

    raw_sends = value.get("sends", [])
    if not isinstance(raw_sends, list) or len(raw_sends) > 128:
        raise ValueError("routing sends must be a bounded array")
    sends = []
    send_ids = set()
    for item in raw_sends:
        if not isinstance(item, dict):
            raise ValueError("routing send must be an object")
        send_id = _bounded_text(item.get("id"))
        source = _bounded_text(item.get("source"))
        target = _bounded_text(item.get("target") or "master")
        if not send_id or send_id in send_ids or not source:
            raise ValueError("routing send ids and sources must be unique/non-empty")
        if target != "master" and target not in bus_ids:
            raise ValueError("routing send target does not exist")
        send_ids.add(send_id)
        sends.append(
            {
                "id": send_id,
                "source": source,
                "target": target,
                "gain": _bounded_number(item.get("gain", 1.0), 0.0, 2.0, "send gain"),
                "pre_fader": bool(item.get("pre_fader", False)),
                "enabled": bool(item.get("enabled", True)),
            }
        )

    _check_routing_acyclic(buses, sends)
    result = {}
    if buses:
        result["buses"] = buses
    if track_outputs:
        result["track_outputs"] = track_outputs
    if sends:
        result["sends"] = sends
    return result


def _bounded_int(value, low: int, high: int, label: str) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError(f"{label} must be an integer") from exc
    if not low <= number <= high:
        raise ValueError(f"{label} is out of range")
    return number


def _validate_recording(value) -> dict:
    if value is None:
        return {}
    allowed = {
        "loop_takes",
        "auto_take_lanes",
        "loop_passes",
        "punch_enabled",
        "punch_start",
        "punch_end",
        "pre_roll_bars",
        "take_groups",
    }
    if not isinstance(value, dict) or set(value) - allowed:
        raise ValueError("recording workflow metadata contains unsupported keys")
    if not value:
        return {}

    loop_takes = bool(value.get("loop_takes", False))
    punch_enabled = bool(value.get("punch_enabled", False))
    if loop_takes and punch_enabled:
        raise ValueError("loop-take and punch recording modes are mutually exclusive")
    punch_start = _bounded_number(value.get("punch_start", 0.0), 0.0, 1_000_000.0, "punch start")
    punch_end = _bounded_number(value.get("punch_end", 4.0), 0.0, 1_000_000.0, "punch end")
    if punch_enabled and punch_end <= punch_start:
        raise ValueError("punch end must be after punch start")

    raw_groups = value.get("take_groups", [])
    if not isinstance(raw_groups, list) or len(raw_groups) > 256:
        raise ValueError("take groups must be a bounded array")
    groups = []
    group_ids = set()
    for item in raw_groups:
        if not isinstance(item, dict):
            raise ValueError("take group must be an object")
        group_id = _bounded_text(item.get("id"))
        if not group_id or group_id in group_ids:
            raise ValueError("take group ids must be unique and non-empty")
        group_ids.add(group_id)
        lanes = item.get("lanes", [])
        if not isinstance(lanes, list) or len(lanes) > 64:
            raise ValueError("take group lanes must be a bounded array")
        start = _bounded_number(item.get("start", 0.0), 0.0, 1_000_000.0, "take start")
        end = _bounded_number(item.get("end", start), 0.0, 1_000_000.0, "take end")
        if end < start:
            raise ValueError("take group end cannot precede its start")
        groups.append(
            {
                "id": group_id,
                "name": _bounded_text(item.get("name") or "Takes", 96),
                "source_row": _bounded_text(item.get("source_row")),
                "lanes": [_bounded_text(lane) for lane in lanes],
                "start": start,
                "end": end,
                "active_lane": _bounded_text(item.get("active_lane")),
            }
        )

    result = {
        "loop_takes": loop_takes,
        "auto_take_lanes": bool(value.get("auto_take_lanes", True)),
        "loop_passes": _bounded_int(value.get("loop_passes", 0), 0, 64, "loop passes"),
        "punch_enabled": punch_enabled,
        "punch_start": punch_start,
        "punch_end": punch_end,
        "pre_roll_bars": _bounded_int(value.get("pre_roll_bars", 1), 0, 8, "pre-roll bars"),
    }
    if groups:
        result["take_groups"] = groups
    return result


def validate_workflow(value) -> dict:
    if value is None:
        return {}
    if not isinstance(value, dict) or set(value) - _ALLOWED:
        raise ValueError("project workflow metadata contains unsupported keys")
    result: dict = {}

    clip_edits = value.get("clip_edits", {})
    if not isinstance(clip_edits, dict) or len(clip_edits) > 100_000:
        raise ValueError("clip workflow metadata must be a bounded object")
    checked_clips = {}
    for clip_id, item in clip_edits.items():
        clip_id = _bounded_text(clip_id)
        if not isinstance(item, dict) or len(item) > 24:
            raise ValueError("clip workflow metadata is invalid")
        checked = {}
        for key in ("stretch_mode", "source_name"):
            if key in item:
                checked[key] = _bounded_text(item[key])
        for key in ("fade_in_ms", "fade_out_ms", "source_bpm"):
            if key in item:
                number = float(item[key])
                if not math.isfinite(number) or not -1_000_000 <= number <= 1_000_000:
                    raise ValueError("clip workflow numeric value is out of range")
                checked[key] = number
        checked_clips[clip_id] = checked
    if checked_clips:
        result["clip_edits"] = checked_clips

    freezes = value.get("freezes", {})
    if not isinstance(freezes, dict) or len(freezes) > 64:
        raise ValueError("freeze metadata must be a bounded object")
    checked_freezes = {}
    for track_id, item in freezes.items():
        track_id = _bounded_text(track_id)
        if not isinstance(item, dict):
            raise ValueError("freeze metadata is invalid")
        if len(item) > 24:
            raise ValueError("freeze metadata contains too many fields")
        checked_freezes[track_id] = deepcopy(item)
    if checked_freezes:
        result["freezes"] = checked_freezes

    groups = value.get("groups", [])
    if not isinstance(groups, list) or len(groups) > 64:
        raise ValueError("track groups must be a bounded array")
    checked_groups = []
    for item in groups:
        if not isinstance(item, dict):
            raise ValueError("track group must be an object")
        members = item.get("members", [])
        if not isinstance(members, list) or len(members) > 64:
            raise ValueError("track group members are invalid")
        checked_groups.append(
            {
                "id": _bounded_text(item.get("id")),
                "name": _bounded_text(item.get("name"), 96),
                "members": [_bounded_text(member) for member in members],
                "gain": _bounded_number(item.get("gain", 1.0), 0.0, 2.0, "group gain"),
                "mute": bool(item.get("mute", False)),
            }
        )
    if checked_groups:
        result["groups"] = checked_groups

    recording = _validate_recording(value.get("recording", {}))
    if recording:
        result["recording"] = recording

    routing = _validate_routing(value.get("routing", {}))
    if routing:
        result["routing"] = routing

    sidechains = value.get("sidechains", [])
    if not isinstance(sidechains, list) or len(sidechains) > 64:
        raise ValueError("sidechains must be a bounded array")
    checked_sidechains = []
    for item in sidechains:
        if not isinstance(item, dict):
            raise ValueError("sidechain must be an object")
        checked_sidechains.append(
            {
                "source": _bounded_text(item.get("source")),
                "target": _bounded_text(item.get("target")),
                "amount": _bounded_number(item.get("amount", 4.0), 0.0, 32.0, "sidechain amount"),
                "threshold": _bounded_number(
                    item.get("threshold", 0.05), 0.0, 1.0, "sidechain threshold"
                ),
                "enabled": bool(item.get("enabled", True)),
            }
        )
    if checked_sidechains:
        result["sidechains"] = checked_sidechains

    scenes = value.get("scenes", [])
    if not isinstance(scenes, list) or len(scenes) > 256:
        raise ValueError("scenes must be a bounded array")
    checked_scenes = []
    for item in scenes:
        if not isinstance(item, dict):
            raise ValueError("scene must be an object")
        checked_scenes.append(
            {
                "id": _bounded_text(item.get("id")),
                "name": _bounded_text(item.get("name"), 96),
                "pattern": _bounded_text(item.get("pattern")),
                "bpm": _bounded_number(item.get("bpm", 120.0), 20.0, 400.0, "scene tempo"),
            }
        )
    if checked_scenes:
        result["scenes"] = checked_scenes

    presets = value.get("track_presets", {})
    if not isinstance(presets, dict) or len(presets) > 256:
        raise ValueError("track presets must be a bounded object")
    checked_presets = {}
    for name, item in presets.items():
        name = _bounded_text(name, 96)
        if not isinstance(item, dict) or len(item) > 32:
            raise ValueError("track preset is invalid")
        checked_presets[name] = deepcopy(item)
    if checked_presets:
        result["track_presets"] = checked_presets

    return result


def ensure_workflow(project: Project) -> dict:
    current = getattr(project, "workflow", None)
    if current is None:
        current = {}
        project.workflow = current
    return current


def install_project_workflow_state() -> None:
    """Teach Project persistence about the optional workflow sidecar once."""
    global _INSTALLED
    if _INSTALLED:
        return

    def to_dict(self: Project) -> dict:
        payload = _ORIGINAL_TO_DICT(self)
        workflow = validate_workflow(getattr(self, "workflow", {}))
        if workflow:
            payload["workflow"] = workflow
        return payload

    @classmethod
    def from_dict(cls, payload: dict) -> Project:
        project = _ORIGINAL_FROM_DICT(cls, payload)
        project.workflow = validate_workflow(payload.get("workflow", {}))
        return project

    Project.to_dict = to_dict
    Project.from_dict = from_dict
    _INSTALLED = True
