"""Persistent named timeline markers, cue points and regions (deliverable 03-04).

Positions use quarter-note beats, just like arrangement clips. The optional
format-5 sidecar participates in normal project persistence and undo snapshots;
it has no audio-thread or device dependencies.
"""

from __future__ import annotations

import csv
from dataclasses import asdict, dataclass
import io
import json
import math
import os
from pathlib import Path
import re
import tempfile
from uuid import uuid4

MAX_MARKERS = 4096
# Fits Qt's maximum widget width even at the arrangement's 90px/beat zoom.
MAX_BEAT = 100_000.0
KINDS = ("marker", "cue", "region")
COLORS = {"marker": "#d5a354", "cue": "#79b8d4", "region": "#88b878"}
_INSTALLED = False


def _number(value: object, label: str) -> float:
    if type(value) not in (int, float):
        raise ValueError(f"{label} must be a finite number")
    if not 0 <= value <= MAX_BEAT:
        raise ValueError(f"{label} must be between 0 and {MAX_BEAT:g} beats")
    if not math.isfinite(value):
        raise ValueError(f"{label} must be a finite number")
    return float(value)


def _text(value: object, label: str, limit: int) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        raise ValueError(f"{label} must contain 1–{limit} characters")
    if any(ord(character) < 32 for character in value):
        raise ValueError(f"{label} cannot contain control characters")
    return value.strip()


@dataclass(frozen=True)
class TimelineMarker:
    id: str
    kind: str
    name: str
    start_beat: float
    end_beat: float | None = None
    color: str = "#d5a354"

    @classmethod
    def from_dict(cls, value: object) -> "TimelineMarker":
        fields = {"id", "kind", "name", "start_beat", "end_beat", "color"}
        if not isinstance(value, dict) or set(value) - fields:
            raise ValueError("timeline marker must be an object with supported fields")
        kind = value.get("kind", "marker")
        if kind not in KINDS:
            raise ValueError("timeline entry kind must be marker, cue or region")
        start = _number(value.get("start_beat", 0.0), "marker start")
        end = value.get("end_beat")
        if kind == "region":
            end = _number(end, "region end")
            if end <= start:
                raise ValueError("region end must be after its start")
        elif end is not None:
            raise ValueError("only regions have an end position")
        color = value.get("color", COLORS[kind])
        if not isinstance(color, str) or not re.fullmatch(r"#[0-9a-fA-F]{6}", color):
            raise ValueError("marker color must be #RRGGBB")
        return cls(
            _text(value.get("id"), "marker ID", 128),
            kind,
            _text(value.get("name"), "marker name", 256),
            start,
            end,
            color.lower(),
        )

    def to_dict(self) -> dict:
        return asdict(self)


def validate_timeline_markers(value: object) -> dict:
    """Reject malformed state without discarding a user's previous project."""
    if value is None or value == {}:
        return {"version": 1, "items": []}
    if not isinstance(value, dict) or set(value) - {"version", "items"}:
        raise ValueError("timeline_markers must contain only version and items")
    if type(value.get("version", 1)) is not int or value.get("version", 1) != 1:
        raise ValueError("unsupported timeline marker version")
    items = value.get("items", [])
    if not isinstance(items, list) or len(items) > MAX_MARKERS:
        raise ValueError(f"a project supports at most {MAX_MARKERS} timeline entries")
    markers = [TimelineMarker.from_dict(item) for item in items]
    if len({marker.id for marker in markers}) != len(markers):
        raise ValueError("timeline marker IDs must be unique")
    markers.sort(key=lambda marker: (marker.start_beat, marker.kind, marker.id))
    return {"version": 1, "items": [marker.to_dict() for marker in markers]}


def marker_items(project) -> list[TimelineMarker]:
    state = validate_timeline_markers(getattr(project, "timeline_markers", None))
    return [TimelineMarker(**item) for item in state["items"]]


def marker_extent(project) -> float:
    return max((item.end_beat or item.start_beat for item in marker_items(project)), default=0.0)


def create_marker(
    project,
    kind: str,
    name: str,
    start_beat: float,
    end_beat: float | None = None,
    color: str | None = None,
) -> TimelineMarker:
    marker = TimelineMarker.from_dict(
        {
            "id": uuid4().hex,
            "kind": kind,
            "name": name,
            "start_beat": start_beat,
            "end_beat": end_beat,
            "color": color or COLORS.get(kind, COLORS["marker"]),
        }
    )
    items = [item.to_dict() for item in marker_items(project)]
    project.timeline_markers = validate_timeline_markers(
        {"version": 1, "items": items + [marker.to_dict()]}
    )
    return marker


def update_marker(project, marker_id: str, **changes) -> TimelineMarker:
    if "id" in changes:
        raise ValueError("marker IDs cannot be changed")
    items = marker_items(project)
    current = next((item for item in items if item.id == marker_id), None)
    if current is None:
        raise ValueError("the selected timeline entry no longer exists")
    updated = TimelineMarker.from_dict({**current.to_dict(), **changes})
    project.timeline_markers = validate_timeline_markers(
        {
            "version": 1,
            "items": [
                updated.to_dict() if item.id == marker_id else item.to_dict() for item in items
            ],
        }
    )
    return updated


def delete_marker(project, marker_id: str) -> None:
    items = marker_items(project)
    if not any(item.id == marker_id for item in items):
        raise ValueError("the selected timeline entry no longer exists")
    project.timeline_markers = {
        "version": 1,
        "items": [item.to_dict() for item in items if item.id != marker_id],
    }


def adjacent_marker(project, beat: float, direction: int) -> TimelineMarker | None:
    """Find the next/previous entry without wrapping or moving the transport."""
    position = _number(beat, "navigation position")
    items = marker_items(project)
    if direction > 0:
        return next((item for item in items if item.start_beat > position + 1e-8), None)
    return next((item for item in reversed(items) if item.start_beat < position - 1e-8), None)


def _seconds(project, beat: float) -> float:
    # Future tempo maps can supply the same conversion used by rendering.
    converter = getattr(project, "beat_to_seconds", None)
    if callable(converter):
        result = float(converter(beat))
    else:
        bpm = float(project.bpm)
        if not math.isfinite(bpm) or bpm <= 0:
            raise ValueError("project tempo must be positive for marker export")
        result = beat * 60.0 / bpm
    if not math.isfinite(result) or result < 0:
        raise ValueError("marker time conversion returned an invalid time")
    return result


def export_document(project) -> dict:
    items = []
    for marker in marker_items(project):
        item = marker.to_dict()
        item["start_seconds"] = _seconds(project, marker.start_beat)
        item["end_seconds"] = (
            _seconds(project, marker.end_beat) if marker.end_beat is not None else None
        )
        items.append(item)
    return {
        "format": "anharmonic-timeline-markers",
        "version": 1,
        "project": str(project.name),
        "beat_unit": "quarter note",
        "items": items,
    }


def export_text(project, format: str = "json") -> str:
    document = export_document(project)
    if format == "json":
        return json.dumps(document, ensure_ascii=False, indent=2) + "\n"
    if format != "csv":
        raise ValueError("marker export format must be JSON or CSV")
    stream = io.StringIO(newline="")
    columns = (
        "id",
        "kind",
        "name",
        "start_beat",
        "end_beat",
        "start_seconds",
        "end_seconds",
        "color",
    )
    writer = csv.DictWriter(stream, fieldnames=columns, lineterminator="\n")
    writer.writeheader()
    for item in document["items"]:
        row = dict(item)
        # Spreadsheet applications otherwise interpret user names as formulas.
        for field in ("id", "name"):
            if row[field].lstrip().startswith(("=", "+", "-", "@")):
                row[field] = "'" + row[field]
        writer.writerow(row)
    return stream.getvalue()


def export_markers(project, destination: Path, format: str | None = None) -> Path:
    """Write atomically so an export failure cannot truncate a previous export."""
    destination = Path(destination)
    chosen = format or destination.suffix.lstrip(".").lower()
    contents = export_text(project, chosen)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            newline="",
            dir=destination.parent,
            prefix=".markers-",
            suffix=".tmp",
            delete=False,
        ) as output:
            temporary = Path(output.name)
            output.write(contents)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, destination)
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()
    return destination


def install_timeline_marker_state() -> None:
    """Install after the other optional Project persistence extensions."""
    global _INSTALLED
    if _INSTALLED:
        return
    from .model import Project
    from .workflow_state import install_project_workflow_state

    # That legacy installer captures its baseline at module import, unlike the
    # later sidecars. Install it first even in standalone tools/tests so calling
    # the full application installer later cannot replace this wrapper.
    install_project_workflow_state()

    original_to_dict = Project.to_dict
    original_from_dict = Project.from_dict.__func__

    def to_dict(self):
        payload = original_to_dict(self)
        state = validate_timeline_markers(getattr(self, "timeline_markers", None))
        if state["items"]:
            payload["timeline_markers"] = state
        return payload

    @classmethod
    def from_dict(cls, payload):
        if not isinstance(payload, dict):
            raise ValueError("project must be an object")
        state = validate_timeline_markers(payload.get("timeline_markers"))
        project = original_from_dict(cls, payload)
        project.timeline_markers = state
        return project

    Project.to_dict = to_dict
    Project.from_dict = from_dict
    _INSTALLED = True
