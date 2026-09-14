"""Bounded optional project state for deeper professional DAW systems.

Project format 5 remains the core interchange contract.  Older builds already
ignore unknown top-level keys, so advanced systems can evolve inside this
validated sidecar without destabilizing the realtime dataclasses or forcing a
format bump for every workflow tranche.
"""

from __future__ import annotations

import base64
import math
from pathlib import Path

from .model import MAX_TRACKS, Project
from .workflow_routing import MAX_ROUTING_BUSES

MAX_CHAIN_PLUGINS = 8
MAX_PLUGIN_CHAINS = MAX_TRACKS + MAX_ROUTING_BUSES + 1
MAX_CHAIN_PLUGIN_STATE = 2 * 1024 * 1024
MAX_TOTAL_CHAIN_PLUGINS = 128
_INSTALLED = False


def _text(value, limit: int, label: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{label} must be text")
    text = value.strip()
    if len(text) > limit:
        raise ValueError(f"{label} is too long")
    return text


def _target(value) -> str:
    target = _text(value, 160, "plugin chain target")
    if target == "master":
        return target
    if target.startswith("track:") and target[6:]:
        return target
    if target.startswith("bus:") and target[4:]:
        return target
    raise ValueError("plugin chain target must be master, track:<id>, or bus:<id>")


def _plugin_spec(value) -> dict:
    allowed = {"path", "plugin_name", "parameters", "state", "bypass"}
    if not isinstance(value, dict) or set(value) - allowed:
        raise ValueError("plugin chain entry contains unsupported fields")
    path = _text(value.get("path", ""), 4096, "plugin path")
    if not path or Path(path).suffix.casefold() not in {".vst3", ".component"}:
        raise ValueError("plugin chain entry must identify a VST3 or Audio Unit")
    name = value.get("plugin_name", "")
    if not isinstance(name, str) or len(name) > 512:
        raise ValueError("plugin chain name is invalid")
    parameters = value.get("parameters", {})
    if not isinstance(parameters, dict) or len(parameters) > 512:
        raise ValueError("plugin chain parameters must be a bounded object")
    checked_parameters = {}
    for key, number in parameters.items():
        if not isinstance(key, str) or len(key) > 256:
            raise ValueError("plugin chain parameter name is invalid")
        if type(number) not in (int, float) or not math.isfinite(number) or not 0 <= number <= 1:
            raise ValueError("plugin chain parameter values must be normalized finite numbers")
        checked_parameters[key] = float(number)
    state = value.get("state", "")
    if not isinstance(state, str) or len(state) > 2_800_000:
        raise ValueError("plugin chain state is too large")
    try:
        decoded = base64.b64decode(state, validate=True)
    except (ValueError, TypeError) as exc:
        raise ValueError("plugin chain state is invalid") from exc
    if len(decoded) > MAX_CHAIN_PLUGIN_STATE:
        raise ValueError("plugin chain state is too large")
    if type(value.get("bypass", False)) is not bool:
        raise ValueError("plugin chain bypass must be boolean")
    return {
        "path": path,
        "plugin_name": name,
        "parameters": checked_parameters,
        "state": state,
        "bypass": value.get("bypass", False),
    }


def validate_pro_daw(value) -> dict:
    if value is None:
        return {}
    if not isinstance(value, dict) or set(value) - {"plugin_chains"}:
        raise ValueError("pro_daw contains unsupported keys")
    raw = value.get("plugin_chains", {})
    if not isinstance(raw, dict) or len(raw) > MAX_PLUGIN_CHAINS:
        raise ValueError("plugin chains must be a bounded object")
    result = {}
    total = 0
    for raw_target, items in raw.items():
        target = _target(raw_target)
        if target in result:
            raise ValueError("plugin chain targets must be unique")
        if not isinstance(items, list) or len(items) > MAX_CHAIN_PLUGINS:
            raise ValueError(f"each insert chain supports at most {MAX_CHAIN_PLUGINS} plugins")
        checked = [_plugin_spec(item) for item in items]
        total += len(checked)
        if total > MAX_TOTAL_CHAIN_PLUGINS:
            raise ValueError("project contains too many insert plugins")
        if checked:
            result[target] = checked
    return {"plugin_chains": result} if result else {}


def ensure_pro_daw(project: Project) -> dict:
    current = getattr(project, "pro_daw", None)
    if current is None:
        current = {}
        project.pro_daw = current
    return current


def plugin_chains(project: Project) -> dict[str, list[dict]]:
    state = validate_pro_daw(ensure_pro_daw(project))
    chains = state.get("plugin_chains", {})
    return chains if isinstance(chains, dict) else {}


def install_pro_daw_state() -> None:
    """Wrap whatever Project persistence is currently installed exactly once."""
    global _INSTALLED
    if _INSTALLED:
        return
    original_to_dict = Project.to_dict
    original_from_dict = Project.from_dict.__func__

    def to_dict(self: Project) -> dict:
        payload = original_to_dict(self)
        state = validate_pro_daw(getattr(self, "pro_daw", {}))
        if state:
            payload["pro_daw"] = state
        return payload

    @classmethod
    def from_dict(cls, payload: dict) -> Project:
        project = original_from_dict(cls, payload)
        project.pro_daw = validate_pro_daw(payload.get("pro_daw", {}))
        return project

    Project.to_dict = to_dict
    Project.from_dict = from_dict
    _INSTALLED = True
