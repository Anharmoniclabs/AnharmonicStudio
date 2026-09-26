"""Focused regression coverage for per-Song-track monitoring ownership."""

from copy import deepcopy

import numpy as np
import pytest

from mpclab.model import Project
from test_track_recording import arm, mock_audio, window  # noqa: F401


def test_song_monitor_is_track_local_persistent_and_routes_dry_cue(window, monkeypatch):
    _data, calls = mock_audio(window, monkeypatch)
    window.project.vocal_record.monitor = False
    window.project.vocal_record.corrected_monitor = True
    row = arm(window)
    other = window.project.rows[0]
    queued = []
    monkeypatch.setattr(
        window.engine,
        "queue_monitor",
        lambda block, gain=1.0: queued.append((np.asarray(block).copy(), gain)),
    )

    window.track_inspector.gain.setValue(6.0)
    window.track_inspector.monitor.setChecked(True)
    assert window.project.vocal_record.monitor is False
    assert window.project.vocal_record.input_gain_db == 0.0
    assert window.project.vocal_record.corrected_monitor is True

    window.track_inspector.select_row(other)
    assert not window.track_inspector.monitor.isChecked()
    window.track_inspector.select_row(row)
    assert window.track_inspector.monitor.isChecked()
    assert window.track_inspector.gain.value() == pytest.approx(6.0)
    assert not window.track_inspector.corrected_monitor.isEnabled()

    restored = Project.from_dict(window.project.to_dict())
    saved = restored.workflow["recording"]["track_inputs"][row.id]
    assert saved == {"count_in_bars": 0, "input_gain_db": 6.0, "monitor": True}

    window.btn_rec.click()
    window.engine._process_commands()
    assert window.track_capture.settings.monitor is True
    assert window.track_capture.settings.corrected_monitor is False
    callback = calls[0][2]
    assert callable(callback)
    block = np.full((32, 2), 0.125, dtype=np.float32)
    callback(block)
    assert len(queued) == 1
    np.testing.assert_array_equal(queued[0][0], block)
    assert queued[0][1] == pytest.approx(window.project.vocal_record.monitor_gain)
    window.stop_all()


def test_song_monitor_defaults_off_even_when_vocal_monitor_is_on(window, monkeypatch):
    _data, calls = mock_audio(window, monkeypatch)
    window.project.vocal_record.monitor = True
    window.project.vocal_record.corrected_monitor = True
    arm(window)

    window.btn_rec.click()
    window.engine._process_commands()

    assert window.track_capture.settings.monitor is False
    assert window.track_capture.settings.corrected_monitor is False
    assert calls == [(None, 0.0, None)]
    window.stop_all()


def test_track_input_workflow_rejects_non_boolean_monitor():
    project = Project()
    row_id = project.rows[0].id
    project.workflow = {
        "recording": {
            "track_inputs": {
                row_id: {
                    "count_in_bars": 1,
                    "input_gain_db": 0.0,
                    "monitor": False,
                }
            }
        }
    }
    payload = project.to_dict()
    broken = deepcopy(payload)
    broken["workflow"]["recording"]["track_inputs"][row_id]["monitor"] = "yes"
    with pytest.raises(ValueError, match="monitor must be a boolean"):
        Project.from_dict(broken)
