from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication

from mpclab.engine import Engine
from mpclab.model import Clip, Pattern, Project, Row
from mpclab.music import Note
from mpclab.premium_workflows import attach_premium_workflows, install_premium_runtime
from mpclab.recording_workflows import (
    attach_recording_workflows,
    install_recording_capture_extensions,
)
from mpclab.take_comping import (
    clear_comp,
    comp_coverage,
    comp_row,
    erase_comp_range,
    swipe_comp_range,
    take_group,
)
from mpclab.ui import main_window
from mpclab.workflow_state import install_project_workflow_state, validate_workflow
from scripts.render_studio_preview import PreviewSettings


def _audio_project() -> tuple[Project, Row, Row, Row, str]:
    project = Project(name="audio comp", bpm=120.0)
    source = Row(id="source-audio", name="Vocal", record_source="audio", record_track=3)
    lane_one = Row(
        id="lane-audio-1",
        name="Vocal · Take 1",
        record_source="audio",
        record_track=3,
        mute=True,
    )
    lane_two = Row(
        id="lane-audio-2",
        name="Vocal · Take 2",
        record_source="audio",
        record_track=3,
        mute=False,
    )
    lane_one.clips.append(
        Clip(
            kind="audio",
            ref="shared-audio",
            start_beat=4.0,
            length_beats=4.0,
            offset=0.0,
            source_length=2.0,
            track=3,
        )
    )
    lane_two.clips.append(
        Clip(
            kind="audio",
            ref="shared-audio",
            start_beat=4.0,
            length_beats=4.0,
            offset=2.0,
            source_length=2.0,
            track=3,
        )
    )
    project.rows = [source, lane_one, lane_two]
    group_id = "takes:audio"
    project.workflow = validate_workflow(
        {
            "recording": {
                "take_groups": [
                    {
                        "id": group_id,
                        "name": "Vocal Takes 1",
                        "source_row": source.id,
                        "lanes": [lane_one.id, lane_two.id],
                        "start": 4.0,
                        "end": 8.0,
                        "active_lane": lane_two.id,
                    }
                ]
            }
        }
    )
    return project, source, lane_one, lane_two, group_id


def _note_project() -> tuple[Project, Row, Row, str, str, str]:
    project = Project(name="note comp", bpm=120.0)
    source = Row(id="source-notes", name="Keys", record_source="notes", record_track=2)
    first_pattern = Pattern(id="pattern-take-1", name="Take 1")
    first_pattern.notes = [Note(60, 0.0, 4.0, 0.8)]
    second_pattern = Pattern(id="pattern-take-2", name="Take 2")
    second_pattern.notes = [Note(64, 0.0, 4.0, 0.7)]
    project.patterns = [first_pattern, second_pattern]
    lane_one = Row(
        id="lane-notes-1",
        name="Keys · Take 1",
        record_source="notes",
        record_track=2,
        mute=True,
    )
    lane_two = Row(
        id="lane-notes-2",
        name="Keys · Take 2",
        record_source="notes",
        record_track=2,
        mute=False,
    )
    lane_one.clips.append(
        Clip(kind="pattern", ref=first_pattern.id, start_beat=0.0, length_beats=4.0)
    )
    lane_two.clips.append(
        Clip(kind="pattern", ref=second_pattern.id, start_beat=0.0, length_beats=4.0)
    )
    project.rows = [source, lane_one, lane_two]
    group_id = "takes:notes"
    project.workflow = validate_workflow(
        {
            "recording": {
                "take_groups": [
                    {
                        "id": group_id,
                        "name": "Keys Takes 1",
                        "source_row": source.id,
                        "lanes": [lane_one.id, lane_two.id],
                        "start": 0.0,
                        "end": 4.0,
                        "active_lane": lane_two.id,
                    }
                ]
            }
        }
    )
    return project, lane_one, lane_two, group_id, first_pattern.id, second_pattern.id


def test_audio_swipe_starts_from_active_take_without_touching_source_clips():
    project, _source, lane_one, lane_two, group_id = _audio_project()
    source_state = [
        (clip.id, clip.ref, clip.start_beat, clip.length_beats, clip.offset, clip.source_length)
        for lane in (lane_one, lane_two)
        for clip in lane.clips
    ]

    row = swipe_comp_range(project, group_id, lane_one.id, 5.0, 7.0)

    assert row.id == f"{group_id}:comp"
    assert not row.mute
    assert [lane_one.mute, lane_two.mute] == [True, True]
    assert comp_coverage(row) == [
        (4.0, 5.0, "audio"),
        (5.0, 7.0, "audio"),
        (7.0, 8.0, "audio"),
    ]
    clips = sorted(row.clips, key=lambda item: item.start_beat)
    assert [clip.ref for clip in clips] == ["shared-audio"] * 3
    assert [clip.offset for clip in clips] == pytest.approx([2.0, 0.5, 3.5])
    assert [clip.source_length for clip in clips] == pytest.approx([0.5, 1.0, 0.5])
    assert take_group(project, group_id)["active_lane"] == lane_one.id
    assert source_state == [
        (clip.id, clip.ref, clip.start_beat, clip.length_beats, clip.offset, clip.source_length)
        for lane in (lane_one, lane_two)
        for clip in lane.clips
    ]


def test_repainting_and_erasing_only_rebuild_the_requested_comp_range():
    project, _source, lane_one, lane_two, group_id = _audio_project()
    row = swipe_comp_range(project, group_id, lane_one.id, 5.0, 7.0)
    row = swipe_comp_range(project, group_id, lane_two.id, 6.0, 8.0)

    assert comp_coverage(row) == [
        (4.0, 5.0, "audio"),
        (5.0, 6.0, "audio"),
        (6.0, 8.0, "audio"),
    ]
    assert [
        clip.offset for clip in sorted(row.clips, key=lambda item: item.start_beat)
    ] == pytest.approx([2.0, 0.5, 3.0])

    row = erase_comp_range(project, group_id, 6.5, 7.0)
    assert comp_coverage(row) == [
        (4.0, 5.0, "audio"),
        (5.0, 6.0, "audio"),
        (6.0, 6.5, "audio"),
        (7.0, 8.0, "audio"),
    ]
    assert [lane_one.mute, lane_two.mute] == [True, True]


def test_clear_comp_restores_the_selected_take_as_the_only_audible_lane():
    project, _source, lane_one, lane_two, group_id = _audio_project()
    row = swipe_comp_range(project, group_id, lane_one.id, 5.0, 7.0)

    clear_comp(project, group_id)

    assert row.clips == []
    assert row.mute
    assert [lane_one.mute, lane_two.mute] == [False, True]
    assert take_group(project, group_id)["active_lane"] == lane_one.id


def test_note_comp_uses_derived_patterns_and_clear_removes_only_comp_patterns():
    project, lane_one, lane_two, group_id, first_id, second_id = _note_project()
    source_notes = {
        pattern.id: [
            (note.pitch, note.start, note.duration, note.velocity) for note in pattern.notes
        ]
        for pattern in project.patterns
    }

    row = swipe_comp_range(project, group_id, lane_one.id, 1.0, 3.0)

    assert comp_coverage(row) == [
        (0.0, 1.0, "pattern"),
        (1.0, 3.0, "pattern"),
        (3.0, 4.0, "pattern"),
    ]
    patterns = {pattern.id: pattern for pattern in project.patterns}
    comp_patterns = [
        patterns[clip.ref] for clip in sorted(row.clips, key=lambda item: item.start_beat)
    ]
    assert [[note.pitch for note in pattern.notes] for pattern in comp_patterns] == [
        [64],
        [60],
        [64],
    ]
    assert source_notes == {
        pattern.id: [
            (note.pitch, note.start, note.duration, note.velocity) for note in pattern.notes
        ]
        for pattern in project.patterns
        if pattern.id in {first_id, second_id}
    }
    assert [lane_one.mute, lane_two.mute] == [True, True]

    clear_comp(project, group_id)
    assert {pattern.id for pattern in project.patterns} == {first_id, second_id}
    assert [lane_one.mute, lane_two.mute] == [False, True]


def test_comp_row_and_playback_state_survive_project_round_trip():
    install_project_workflow_state()
    project, _source, lane_one, _lane_two, group_id = _audio_project()
    swipe_comp_range(project, group_id, lane_one.id, 5.0, 7.0)

    reopened = Project.from_dict(project.to_dict())
    reopened_group = take_group(reopened, group_id)
    reopened_comp = comp_row(reopened, reopened_group)
    assert reopened_comp is not None
    assert not reopened_comp.mute
    assert comp_coverage(reopened_comp) == [
        (4.0, 5.0, "audio"),
        (5.0, 7.0, "audio"),
        (7.0, 8.0, "audio"),
    ]
    reopened_lanes = [
        next(row for row in reopened.rows if row.id == lane_id)
        for lane_id in reopened_group["lanes"]
    ]
    assert [lane.mute for lane in reopened_lanes] == [True, True]


def test_invalid_lane_and_outside_range_do_not_create_a_comp_row():
    project, _source, _lane_one, _lane_two, group_id = _audio_project()
    group = take_group(project, group_id)

    with pytest.raises(ValueError, match="not part"):
        swipe_comp_range(project, group_id, "other-lane", 5.0, 6.0)
    with pytest.raises(ValueError, match="positive length"):
        erase_comp_range(project, group_id, 20.0, 21.0)
    assert comp_row(project, group) is None


@pytest.fixture
def window(tmp_path, monkeypatch):
    monkeypatch.setattr(main_window, "QSettings", PreviewSettings)
    monkeypatch.setattr(Engine, "start", lambda self: None)
    install_premium_runtime()
    install_recording_capture_extensions()
    app = QApplication.instance() or QApplication([])
    window = main_window.MainWindow(tmp_path, restore_session=False)
    window._ui_timer.stop()
    window._autosave_timer.stop()
    controller = attach_premium_workflows(window)
    attach_recording_workflows(window, controller)
    yield window, controller
    window.track_capture.active = False
    window.track_capture.pending = False
    window.track_capture.unsaved = None
    window._dirty = False
    window.close()
    app.processEvents()


def test_take_comp_command_attaches_to_existing_recording_menu(window):
    from mpclab.take_comping import attach_take_comping

    app_window, controller = window
    before = sum(action.text() == "Recording" for action in app_window.menuBar().actions())
    attached = attach_take_comping(app_window, controller)
    again = attach_take_comping(app_window, controller)

    assert attached is again
    assert controller.registry.get("recording.take_comp").category == "Recording"
    assert sum(action.text() == "Recording" for action in app_window.menuBar().actions()) == before
    recording_menu = next(
        action.menu() for action in app_window.menuBar().actions() if action.text() == "Recording"
    )
    assert "Take comp editor" in {action.text() for action in recording_menu.actions()}
