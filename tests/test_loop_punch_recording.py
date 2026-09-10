from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np
import pytest
from PySide6.QtWidgets import QApplication

from mpclab.engine import Engine
from mpclab.premium_workflows import attach_premium_workflows, install_premium_runtime
from mpclab.recording_workflows import (
    attach_recording_workflows,
    install_recording_capture_extensions,
    recording_settings,
)
from mpclab.routing_ui import attach_routing_ui
from mpclab.ui import main_window
from mpclab.workflow_state import ensure_workflow, validate_workflow
from scripts.render_studio_preview import PreviewSettings


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
    attach_routing_ui(window, controller)
    attach_recording_workflows(window, controller)
    window.project.vocal_record.count_in_bars = 0
    yield window
    window.track_capture.active = False
    window.track_capture.pending = False
    window.track_capture.unsaved = None
    window._dirty = False
    window.close()
    app.processEvents()


def _arm(window, index=1, source="audio"):
    row = window.project.rows[index]
    row.record_source = source
    window.track_capture.arm(row)
    return row


def _mock_audio(window, monkeypatch, seconds):
    recorder = window.track_capture.recorder
    frames = int(window.engine.sr * seconds)
    data = np.full((frames, 2), 0.05, dtype=np.float32)
    monkeypatch.setattr(recorder, "start", lambda *_args: None)
    monkeypatch.setattr(recorder, "stop", lambda: data)
    return data


def _set_recording(window, **values):
    settings = recording_settings(window.project)
    settings.update(values)
    workflow = ensure_workflow(window.project)
    workflow["recording"] = settings
    window.project.workflow = validate_workflow(workflow)


def test_default_track_recording_behavior_stays_single_lane(window, monkeypatch):
    _mock_audio(window, monkeypatch, 1.0)
    row = _arm(window)
    initial_rows = len(window.project.rows)
    window.engine.beat = 3.0
    assert window.track_capture.prepare()
    window.track_capture.start()
    window.track_capture.finish()
    assert len(row.clips) == 1
    assert row.clips[0].start_beat == pytest.approx(3.0)
    assert not ensure_workflow(window.project).get("recording")
    assert len(window.project.rows) == initial_rows


def test_two_audio_loop_passes_create_two_non_destructive_take_lanes(window, monkeypatch):
    data = _mock_audio(window, monkeypatch, 2.0)
    row = _arm(window)
    window.project.bpm = 120.0
    window.project.loop_start = 4.0
    window.project.loop_end = 6.0
    _set_recording(
        window,
        loop_takes=True,
        auto_take_lanes=True,
        loop_passes=2,
        punch_enabled=False,
    )

    assert window.track_capture.prepare()
    assert window.track_capture.start_beat == 4.0
    window.track_capture.start()
    assert window.engine.loop_song
    window.track_capture.finish()

    assert not row.clips
    row_index = window.project.rows.index(row)
    lanes = window.project.rows[row_index + 1 : row_index + 3]
    assert [lane.name for lane in lanes] == [f"{row.name} · Take 1", f"{row.name} · Take 2"]
    assert [lane.mute for lane in lanes] == [True, False]
    clips = [lane.clips[0] for lane in lanes]
    assert clips[0].ref == clips[1].ref
    assert clips[0].offset == pytest.approx(0.0)
    assert clips[1].offset == pytest.approx(1.0)
    assert [clip.source_length for clip in clips] == pytest.approx([1.0, 1.0])
    assert [clip.start_beat for clip in clips] == pytest.approx([4.0, 4.0])
    assert np.array_equal(window.library.audio(clips[0].ref), data)

    groups = ensure_workflow(window.project)["recording"]["take_groups"]
    assert len(groups) == 1
    assert groups[0]["source_row"] == row.id
    assert groups[0]["lanes"] == [lane.id for lane in lanes]
    assert groups[0]["active_lane"] == lanes[-1].id
    assert (groups[0]["start"], groups[0]["end"]) == (4.0, 6.0)


def test_punch_recording_trims_preroll_from_saved_audio(window, monkeypatch):
    _mock_audio(window, monkeypatch, 3.0)
    row = _arm(window)
    window.project.bpm = 120.0
    _set_recording(
        window,
        loop_takes=False,
        punch_enabled=True,
        punch_start=8.0,
        punch_end=10.0,
        pre_roll_bars=1,
    )

    assert window.track_capture.prepare()
    assert window.track_capture.start_beat == 4.0
    window.track_capture.start()
    window.track_capture.finish()

    assert len(row.clips) == 1
    clip = row.clips[0]
    assert clip.start_beat == pytest.approx(8.0)
    assert clip.offset == pytest.approx(2.0)
    assert clip.source_length == pytest.approx(1.0)
    assert clip.length_beats == pytest.approx(2.0)


def test_loop_note_passes_split_into_patterns_with_local_timing(window):
    row = _arm(window, source="notes")
    window.project.bpm = 120.0
    window.project.loop_start = 0.0
    window.project.loop_end = 4.0
    _set_recording(
        window,
        loop_takes=True,
        auto_take_lanes=True,
        loop_passes=0,
        punch_enabled=False,
    )

    assert window.track_capture.prepare()
    window.track_capture.start()

    window.engine.beat = 1.0
    window.track_capture.note_on(60, 0.8)
    window.engine.beat = 1.5
    window.track_capture.note_off(60)
    # Transport wrap into pass two. The capture extension turns this into an
    # unwrapped 4.5-beat timeline before the take is split back into lanes.
    window.engine.beat = 0.5
    window.track_capture.note_on(64, 0.7)
    window.engine.beat = 1.0
    window.track_capture.note_off(64)
    window.track_capture.finish()

    row_index = window.project.rows.index(row)
    lanes = window.project.rows[row_index + 1 : row_index + 3]
    assert len(lanes) == 2
    assert [lane.mute for lane in lanes] == [True, False]
    patterns = [
        next(pattern for pattern in window.project.patterns if pattern.id == lane.clips[0].ref)
        for lane in lanes
    ]
    assert [(note.pitch, note.start, note.duration) for note in patterns[0].notes] == [
        (60, 1.0, 0.5)
    ]
    assert [(note.pitch, note.start, note.duration) for note in patterns[1].notes] == [
        (64, 0.5, 0.5)
    ]
    assert [lane.clips[0].start_beat for lane in lanes] == [0.0, 0.0]


def test_recording_metadata_rejects_conflicting_modes_and_round_trips(window):
    with pytest.raises(ValueError, match="mutually exclusive"):
        validate_workflow(
            {
                "recording": {
                    "loop_takes": True,
                    "punch_enabled": True,
                }
            }
        )

    _set_recording(
        window,
        loop_takes=False,
        punch_enabled=True,
        punch_start=12.0,
        punch_end=16.0,
        pre_roll_bars=2,
    )
    payload = window.project.to_dict()
    reopened = type(window.project).from_dict(payload)
    assert recording_settings(reopened)["punch_start"] == 12.0
    assert recording_settings(reopened)["punch_end"] == 16.0
    assert recording_settings(reopened)["pre_roll_bars"] == 2


def test_recording_menu_and_commands_attach_offscreen(window):
    assert window.recording_workflow_controller is not None
    assert window.workflow_controller.registry.get("recording.settings").category == "Recording"
    assert "Recording" in {action.text() for action in window.menuBar().actions()}
