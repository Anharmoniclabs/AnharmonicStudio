"""Conservative plugin discovery metadata for LV2/CLAP/VST3 hosts.

Discovery is filesystem-only and never loads third-party code. A future host can
consume the registry after validating plugins in an isolated scanner process.
"""

from __future__ import annotations

from dataclasses import dataclass, asdict
import json
from pathlib import Path
from typing import Iterable


PLUGIN_SUFFIXES = {
    "lv2": ".lv2",
    "clap": ".clap",
    "vst3": ".vst3",
}


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
    for root in paths:
        root = Path(root).expanduser()
        if not root.exists() or not root.is_dir():
            continue
        try:
            entries = sorted(root.iterdir(), key=lambda p: p.name.casefold())
        except OSError:
            continue
        for entry in entries:
            suffix = entry.suffix.casefold()
            fmt = next((name for name, ext in PLUGIN_SUFFIXES.items() if ext == suffix), None)
            if fmt is None:
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
