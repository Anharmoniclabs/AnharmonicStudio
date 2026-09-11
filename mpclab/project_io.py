"""Bounded JSON readers for user-controlled project and recovery files."""

from __future__ import annotations

import json
from pathlib import Path

from .model import Project

MAX_PROJECT_FILE_BYTES = 64 * 1024 * 1024
MAX_HISTORY_FILE_BYTES = 64 * 1024 * 1024


def read_json_file(path: Path, *, max_bytes: int, kind: str) -> object:
    """Read UTF-8 JSON without allowing an unbounded pre-parse allocation."""
    target = Path(path)
    if max_bytes <= 0:
        raise ValueError("JSON safety limit must be positive")
    try:
        with target.open("rb") as handle:
            raw = handle.read(max_bytes + 1)
    except OSError:
        raise
    if len(raw) > max_bytes:
        mib = max_bytes / (1024 * 1024)
        label = f"{mib:g} MiB" if mib >= 1 else f"{max_bytes} bytes"
        raise ValueError(f"{kind} exceeds the {label} safety limit")
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError(f"{kind} must be UTF-8 JSON") from exc
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(
            f"{kind} is not valid JSON (line {exc.lineno}, column {exc.colno})"
        ) from exc


def load_project_file(path: Path) -> Project:
    """Load an external desktop project through a bounded reader."""
    payload = read_json_file(
        path,
        max_bytes=MAX_PROJECT_FILE_BYTES,
        kind="project file",
    )
    return Project.from_dict(payload)


def load_history_file(path: Path) -> object:
    """Load an undo/recovery history through the same bounded contract."""
    return read_json_file(
        path,
        max_bytes=MAX_HISTORY_FILE_BYTES,
        kind="undo history",
    )
