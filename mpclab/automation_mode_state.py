"""Backward-compatible persistence for automation write modes.

The envelope data remains in Project.automation. This tiny sidecar stores only
per-target control modes, so existing format-5 projects and renderers keep their
current automation representation unchanged.
"""

from __future__ import annotations

from .model import Project
from .music import automation_targets

AUTOMATION_MODES = ("read", "write", "touch", "latch")
_INSTALLED = False


def validate_automation_control(value) -> dict:
    if value is None:
        return {}
    if not isinstance(value, dict) or set(value) - {"modes"}:
        raise ValueError("automation control contains unsupported keys")
    raw = value.get("modes", {})
    if not isinstance(raw, dict) or len(raw) > len(automation_targets()):
        raise ValueError("automation modes must be a bounded object")
    allowed_targets = set(automation_targets())
    modes = {}
    for target, mode in raw.items():
        if target not in allowed_targets:
            raise ValueError(f"unsupported automation mode target: {target}")
        if mode not in AUTOMATION_MODES:
            raise ValueError(f"unsupported automation mode: {mode}")
        if mode != "read":
            modes[target] = mode
    return {"modes": modes} if modes else {}


def automation_mode(project: Project, target: str) -> str:
    if target not in automation_targets():
        raise ValueError(f"unsupported automation target: {target}")
    state = validate_automation_control(getattr(project, "automation_control", {}))
    return state.get("modes", {}).get(target, "read")


def set_automation_mode(project: Project, target: str, mode: str) -> None:
    state = validate_automation_control(getattr(project, "automation_control", {}))
    modes = dict(state.get("modes", {}))
    if mode == "read":
        modes.pop(target, None)
    else:
        modes[target] = mode
    project.automation_control = validate_automation_control({"modes": modes})


def install_automation_mode_state() -> None:
    global _INSTALLED
    if _INSTALLED:
        return
    original_to_dict = Project.to_dict
    original_from_dict = Project.from_dict.__func__

    def to_dict(self: Project) -> dict:
        payload = original_to_dict(self)
        state = validate_automation_control(getattr(self, "automation_control", {}))
        if state:
            payload["automation_control"] = state
        return payload

    @classmethod
    def from_dict(cls, payload: dict) -> Project:
        project = original_from_dict(cls, payload)
        project.automation_control = validate_automation_control(
            payload.get("automation_control", {})
        )
        return project

    Project.to_dict = to_dict
    Project.from_dict = from_dict
    _INSTALLED = True
