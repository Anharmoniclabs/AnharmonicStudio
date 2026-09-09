"""Backward-compatible project metadata for advanced editor workflows.

The core project format intentionally stays at version 5.  Older builds already
ignore unknown top-level keys, so a bounded ``workflow`` object can travel with
a song without forcing a schema migration or changing realtime dataclasses.
"""

from __future__ import annotations

from copy import deepcopy
from .model import Project


_ALLOWED = {"clip_edits", "freezes", "groups", "sidechains", "scenes", "track_presets"}
_INSTALLED = False
_ORIGINAL_TO_DICT = Project.to_dict
_ORIGINAL_FROM_DICT = Project.from_dict.__func__


def _bounded_text(value, limit=128) -> str:
    text = str(value or "")
    if len(text) > limit:
        raise ValueError("workflow text field is too long")
    return text


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
                if not -1_000_000 <= number <= 1_000_000:
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
        # Freeze records are generated internally from primitive project state.
        # Bound their serialized breadth and recursively copy only JSON-shaped values.
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
                "gain": max(0.0, min(2.0, float(item.get("gain", 1.0)))),
                "mute": bool(item.get("mute", False)),
            }
        )
    if checked_groups:
        result["groups"] = checked_groups

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
                "amount": max(0.0, min(32.0, float(item.get("amount", 4.0)))),
                "threshold": max(0.0, min(1.0, float(item.get("threshold", 0.05)))),
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
                "bpm": max(20.0, min(400.0, float(item.get("bpm", 120.0)))),
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
