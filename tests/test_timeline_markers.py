from __future__ import annotations

import csv
import io
import json

import pytest

from mpclab.model import Project
from mpclab.timeline_markers import (
    MAX_BEAT,
    MAX_MARKERS,
    TimelineMarker,
    adjacent_marker,
    create_marker,
    delete_marker,
    export_document,
    export_markers,
    export_text,
    install_timeline_marker_state,
    marker_extent,
    marker_items,
    update_marker,
    validate_timeline_markers,
)


@pytest.fixture
def project():
    install_timeline_marker_state()
    return Project(name="Marker test", bpm=120)


def test_project_save_load_preserves_named_colored_points_and_regions(project, tmp_path):
    create_marker(project, "region", "Chorus", 16, 32, "#AA22FF")
    create_marker(project, "cue", "Singer in", 7.5, color="#aabbcc")
    create_marker(project, "marker", "Intro", 0)
    path = tmp_path / "song.json"
    project.save(path)
    reopened = Project.load(path)
    assert reopened.to_dict() == project.to_dict()
    assert [item.name for item in marker_items(reopened)] == ["Intro", "Singer in", "Chorus"]
    assert marker_items(reopened)[2].color == "#aa22ff"
    assert marker_extent(reopened) == 32


def test_legacy_project_and_idempotent_installer(project):
    before = Project.to_dict
    install_timeline_marker_state()
    assert Project.to_dict is before
    assert "timeline_markers" not in project.to_dict()
    restored = Project.from_dict(project.to_dict())
    assert marker_items(restored) == []
    assert marker_extent(restored) == 0


@pytest.mark.parametrize(
    "changes",
    [
        {"start_beat": float("nan")},
        {"start_beat": float("inf")},
        {"start_beat": -1},
        {"start_beat": True},
        {"start_beat": "3"},
        {"start_beat": MAX_BEAT + 1},
        {"start_beat": 10**1000},
        {"name": ""},
        {"name": "\nInjected"},
        {"name": "x" * 257},
        {"kind": "tempo"},
        {"color": "red"},
        {"color": "#ffffff00"},
        {"kind": "region", "end_beat": 0},
        {"end_beat": 5},
        {"id": "replacement"},
        {"unknown": 1},
    ],
)
def test_rejected_edit_is_atomic(project, changes):
    marker = create_marker(project, "marker", "Original", 1)
    before = project.to_dict()
    with pytest.raises(ValueError):
        update_marker(project, marker.id, **changes)
    assert project.to_dict() == before


@pytest.mark.parametrize(
    "state", [[], {"version": 2}, {"version": True}, {"items": {}}, {"other": 1}]
)
def test_rejects_invalid_sidecar_instead_of_silently_discarding(project, state):
    payload = project.to_dict()
    payload["timeline_markers"] = state
    with pytest.raises(ValueError):
        Project.from_dict(payload)


def test_duplicate_ids_and_resource_limits(project):
    marker = create_marker(project, "marker", "One", 0).to_dict()
    with pytest.raises(ValueError, match="unique"):
        validate_timeline_markers({"items": [marker, marker]})
    with pytest.raises(ValueError, match="at most"):
        validate_timeline_markers({"items": [marker] * (MAX_MARKERS + 1)})


def test_update_delete_and_navigation(project):
    intro = create_marker(project, "marker", "Intro", 0)
    verse = create_marker(project, "cue", "Verse", 8)
    chorus = create_marker(project, "region", "Chorus", 16, 24)
    assert adjacent_marker(project, 0, -1) is None
    assert adjacent_marker(project, 0, 1) == verse
    assert adjacent_marker(project, 16, -1) == verse
    assert adjacent_marker(project, 16, 1) is None
    result = update_marker(project, chorus.id, start_beat=12, end_beat=20, name="Hook")
    assert result.name == "Hook"
    delete_marker(project, verse.id)
    assert adjacent_marker(project, 1, -1) == intro
    assert adjacent_marker(project, 1, 1) == result
    with pytest.raises(ValueError, match="no longer exists"):
        delete_marker(project, verse.id)


def test_json_and_spreadsheet_safe_csv_export(project, tmp_path):
    create_marker(project, "cue", '=HYPERLINK("bad")', 4, color="#123456")
    create_marker(project, "region", "Vérse, one", 8, 12)
    document = export_document(project)
    assert document["items"][0]["start_seconds"] == 2
    assert document["items"][1]["end_seconds"] == 6
    assert document["items"][0]["end_seconds"] is None
    csv_rows = list(csv.DictReader(io.StringIO(export_text(project, "csv"))))
    assert csv_rows[0]["name"].startswith("'=HYPERLINK")
    assert csv_rows[1]["name"] == "Vérse, one"
    assert csv_rows[0]["color"] == "#123456"
    path = export_markers(project, tmp_path / "markers.json")
    assert json.loads(path.read_text()) == document


def test_export_uses_project_tempo_conversion_when_available(project):
    create_marker(project, "region", "Tempo map", 8, 16)
    project.beat_to_seconds = lambda beat: beat * 0.4
    assert export_document(project)["items"][0]["end_seconds"] == 6.4


def test_atomic_export_failure_keeps_existing_file(project, tmp_path, monkeypatch):
    from mpclab import timeline_markers

    path = tmp_path / "markers.json"
    export_markers(project, path)
    before = path.read_bytes()
    create_marker(project, "marker", "New", 4)

    def fail(*_):
        raise OSError("simulated disk failure")

    monkeypatch.setattr(timeline_markers.os, "replace", fail)
    with pytest.raises(OSError, match="disk failure"):
        export_markers(project, path)
    assert path.read_bytes() == before
    assert list(tmp_path.iterdir()) == [path]


def test_invalid_format_does_not_touch_destination(project, tmp_path):
    path = tmp_path / "markers.wav"
    with pytest.raises(ValueError, match="JSON or CSV"):
        export_markers(project, path)
    assert not path.exists()


def test_marker_is_immutable():
    from dataclasses import FrozenInstanceError

    marker = TimelineMarker("one", "marker", "Start", 0)
    with pytest.raises(FrozenInstanceError):
        marker.name = "Changed"
