"""Filesystem discovery, compatibility hints and saved external-plugin settings.

Discovery never loads third-party code. The isolated host validates a selected
plugin before installing it in the live audio graph.
"""

from __future__ import annotations

from dataclasses import dataclass, asdict
import json
import base64
import math
import os
import platform
from pathlib import Path
from typing import Iterable


PLUGIN_SUFFIXES = {
    "lv2": ".lv2",
    "clap": ".clap",
    "vst3": ".vst3",
    "vst2": ".dll",
    "au": ".component",
}


def default_plugin_paths() -> list[Path]:
    home = Path.home()
    if platform.system() == "Windows":
        return [
            Path(os.environ.get("COMMONPROGRAMFILES", "C:/Program Files/Common Files")) / "VST3"
        ]
    if platform.system() == "Darwin":
        return [
            base / kind
            for base in (home / "Library/Audio/Plug-Ins", Path("/Library/Audio/Plug-Ins"))
            for kind in ("VST3", "Components")
        ]
    return [
        home / ".vst3",
        home / ".lv2",
        home / ".clap",
        Path("/usr/lib/vst3"),
        Path("/usr/local/lib/vst3"),
        Path("/usr/lib/lv2"),
        Path("/usr/lib/clap"),
    ]


def compatibility(candidate: "PluginCandidate") -> str:
    if candidate.format == "au":
        return "Ready to load" if platform.system() == "Darwin" else "Requires macOS"
    if candidate.format != "vst3":
        return "Discovered; this plugin format is not hosted yet"
    path = Path(candidate.path)
    contents = path / "Contents"
    if contents.is_dir():
        if platform.system() == "Linux":
            architecture = platform.machine().lower()
            folder = {"amd64": "x86_64", "arm64": "aarch64"}.get(
                architecture, architecture
            ) + "-linux"
            if not (contents / folder).is_dir():
                return "Needs a Linux build for this computer"
        elif platform.system() == "Darwin" and not (contents / "MacOS").is_dir():
            return "Needs a macOS build"
    return "Ready to load"


def validate_project_plugins(value) -> dict:
    if not isinstance(value, dict) or set(value) - {"instrument", "effect"}:
        raise ValueError("project plugins must contain instrument/effect slots")
    result = {}
    for slot, spec in value.items():
        if not isinstance(spec, dict):
            raise ValueError("project plugin must be an object")
        path = spec.get("path")
        if (
            not isinstance(path, str)
            or len(path) > 4096
            or Path(path).suffix.casefold() not in {".vst3", ".component"}
        ):
            raise ValueError("project plugin path must identify a VST3 or Audio Unit")
        parameters = spec.get("parameters", {})
        if not isinstance(parameters, dict) or len(parameters) > 512:
            raise ValueError("project plugin parameters must be a bounded object")
        for key, number in parameters.items():
            if (
                not isinstance(key, str)
                or len(key) > 256
                or type(number) not in (int, float)
                or not math.isfinite(number)
                or not 0 <= number <= 1
            ):
                raise ValueError("project plugin parameters must be normalized finite numbers")
        state = spec.get("state", "")
        if not isinstance(state, str) or len(state) > 2_800_000:
            raise ValueError("project plugin state is too large")
        try:
            if len(base64.b64decode(state, validate=True)) > 2 * 1024 * 1024:
                raise ValueError("project plugin state is too large")
        except ValueError as exc:
            raise ValueError("project plugin state is invalid") from exc
        name = spec.get("plugin_name", "")
        if (
            not isinstance(name, str)
            or len(name) > 512
            or type(spec.get("bypass", False)) is not bool
        ):
            raise ValueError("project plugin name or bypass is invalid")
        result[slot] = {
            "path": path,
            "plugin_name": name,
            "parameters": dict(parameters),
            "state": state,
            "bypass": spec.get("bypass", False),
        }
    return result


@dataclass(frozen=True, slots=True)
class PluginCandidate:
    id: str
    format: str
    path: str
    name: str

    def validate(self) -> None:
        if self.format not in PLUGIN_SUFFIXES:
            raise ValueError("unsupported plugin format")
        if not self.id or not self.path or not self.name:
            raise ValueError("plugin candidate fields are required")


def discover_plugins(paths: Iterable[Path]) -> list[PluginCandidate]:
    """Discover plugin bundles/files without importing or executing them."""
    candidates: dict[str, PluginCandidate] = {}
    pending = [(Path(root).expanduser(), 0) for root in paths]
    seen = set()
    inspected = 0
    while pending and inspected < 20_000:
        root, depth = pending.pop()
        root = Path(root).expanduser()
        if not root.exists() or not root.is_dir():
            continue
        try:
            resolved_root = root.resolve()
            if resolved_root in seen:
                continue
            seen.add(resolved_root)
            entries = sorted(root.iterdir(), key=lambda p: p.name.casefold())
        except OSError:
            continue
        for entry in entries:
            inspected += 1
            if inspected > 20_000:
                break
            suffix = entry.suffix.casefold()
            fmt = next((name for name, ext in PLUGIN_SUFFIXES.items() if ext == suffix), None)
            if fmt is None:
                if depth < 4 and entry.is_dir() and not entry.is_symlink():
                    pending.append((entry, depth + 1))
                continue
            resolved = str(entry.resolve())
            candidate = PluginCandidate(
                id=f"{fmt}:{resolved}",
                format=fmt,
                path=resolved,
                name=entry.stem,
            )
            candidate.validate()
            candidates[candidate.id] = candidate
    return sorted(
        candidates.values(), key=lambda item: (item.format, item.name.casefold(), item.path)
    )


class PluginRegistry:
    VERSION = 1

    def __init__(self, path: Path):
        self.path = Path(path)
        self.candidates: dict[str, PluginCandidate] = {}
        self.quarantined: dict[str, str] = {}

    def replace_candidates(self, candidates: Iterable[PluginCandidate]) -> None:
        validated: dict[str, PluginCandidate] = {}
        for candidate in candidates:
            candidate.validate()
            validated[candidate.id] = candidate
        self.candidates = validated
        self.quarantined = {
            plugin_id: reason
            for plugin_id, reason in self.quarantined.items()
            if plugin_id in self.candidates
        }

    def quarantine(self, plugin_id: str, reason: str) -> None:
        if plugin_id not in self.candidates:
            raise KeyError(plugin_id)
        text = reason.strip()
        if not text:
            raise ValueError("quarantine reason is required")
        self.quarantined[plugin_id] = text[:500]

    def restore(self, plugin_id: str) -> None:
        self.quarantined.pop(plugin_id, None)

    def usable(self) -> list[PluginCandidate]:
        return [
            candidate
            for plugin_id, candidate in sorted(self.candidates.items())
            if plugin_id not in self.quarantined
        ]

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "version": self.VERSION,
            "candidates": [asdict(item) for item in self.candidates.values()],
            "quarantined": self.quarantined,
        }
        temporary = self.path.with_suffix(self.path.suffix + ".tmp")
        temporary.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        temporary.replace(self.path)

    def load(self) -> None:
        if not self.path.exists():
            return
        payload = json.loads(self.path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict) or payload.get("version") != self.VERSION:
            raise ValueError("unsupported plugin registry")
        raw_candidates = payload.get("candidates", [])
        if not isinstance(raw_candidates, list) or len(raw_candidates) > 100_000:
            raise ValueError("invalid plugin candidate list")
        candidates = [PluginCandidate(**item) for item in raw_candidates if isinstance(item, dict)]
        self.replace_candidates(candidates)
        raw_quarantine = payload.get("quarantined", {})
        if not isinstance(raw_quarantine, dict):
            raise ValueError("invalid plugin quarantine map")
        for plugin_id, reason in raw_quarantine.items():
            if plugin_id in self.candidates and isinstance(reason, str) and reason.strip():
                self.quarantined[plugin_id] = reason[:500]
