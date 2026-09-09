"""Recording and visual tuning remain separate, with reversible Song edits."""

import time
from dataclasses import replace

import numpy as np
import pytest
from PySide6.QtCore import QPoint, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from mpclab.model import Clip, VocalSettings
from mpclab.ui.vocal_pitch import VocalPitchView, correction_guide
from mpclab.vocal import PitchAnalysis
from test_track_recording import window, mock_audio  # noqa: F401


def wait_for(predicate, timeout=8):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        QApplication.processEvents()
        if predicate():
            return
        time.sleep(0.005)
    raise AssertionError("The vocal worker did not finish")


def test_vocal_track_records_dry_in_song_at_the_playhead(window, monkeypatch):  # noqa: F811
    data, calls = mock_audio(window, monkeypatch)
    window.engine.mode = "song"
    window.engine.beat = 9.5
    window.project.vocal.transpose = 12
    window.show_tab(5)
    window.add_vocal_button.click()
    row = window.track_inspector.row()
    assert row.name == "Vocal 1"
    assert window.studio.selected == 2
    assert window.track_capture.armed_id == row.id
    assert not calls
    assert window.engine.beat == 9.5
    window.show_tab(5)
    window.track_inspector.record_song.click()
    assert window.studio.selected == 2
    assert window.track_capture.active
    assert calls
    window.track_inspector.record_song.click()
    assert not window.track_capture.busy
    assert len(row.clips) == 1
    clip = row.clips[0]
    assert clip.start_beat == pytest.approx(9.5)
    assert np.allclose(window.library.audio(clip.ref), data, atol=1e-6)
    assert window.vocal_panel.legacy_recording.isHidden()
    assert window.studio.buttons[5].text() == "Autotune"
    window.studio.buttons[5].click()
    assert window.vocal_panel.take_box.currentData() == clip.ref
    assert window.vocal_panel._song_clip_id == clip.id


def test_visual_tuning_renders_separate_take_and_applies_with_undo(window):  # noqa: F811
    t = np.arange(48000) / 48000
    mono = (0.15 * np.sin(2 * np.pi * 448 * t)).astype(np.float32)
    audio = np.column_stack([mono, mono])
    source = window.library.add_audio(audio, "Dry lead", kind="recording")
    row = window.project.rows[0]
    clip = Clip(kind="audio", ref=source.id, start_beat=7.25, length_beats=1.5, source_length=1)
    row.clips.append(clip)
    window.open_vocal_clip(clip)
    panel = window.vocal_panel
    assert panel.take_box.currentData() == source.id
    assert panel.pitch_view.duration == 1
    assert panel.pitch_view.peaks.max() > 0.1
    panel.key_box.setCurrentText("A")
    panel.scale_box.setCurrentText("major")
    panel.preset.setCurrentText("Hard tune")
    panel.render_button.click()
    wait_for(lambda: panel._tune_cancel is None)
    assert panel.progress.format() == "TUNED TAKE READY"
    tuned_id = panel.take_box.currentData()
    assert tuned_id != source.id
    assert window.library.clips[tuned_id].parent == source.id
    assert panel.compare_tuned_button.isEnabled()
    assert np.array_equal(window.library.audio(source.id), audio)
    tuned_audio = window.library.audio(tuned_id)
    assert tuned_audio.shape == audio.shape and np.isfinite(tuned_audio).all()
    assert not np.allclose(tuned_audio, audio)
    assert clip.ref == source.id
    panel.place_button.click()
    assert clip.ref == tuned_id
    assert window.studio.selected == 2
    assert clip.start_beat == 7.25 and clip.length_beats == 1.5
    window.undo()
    assert window.project.rows[0].clips[0].ref == source.id
    assert window.library.audio(source.id) is not None


def test_correction_guide_follows_settings_and_bypass():
    detected = np.full(100, 69.35, dtype=np.float32)
    analysis = PitchAnalysis(
        np.arange(100) * 0.01, np.full(100, 449.0), detected, np.full(100, 69.0), np.full(100, 0.9)
    )
    settings = VocalSettings(key="A", scale="major", retune_ms=0, humanize=0, strength=1)
    assert np.allclose(correction_guide(analysis, settings), 69)
    assert np.allclose(correction_guide(analysis, replace(settings, strength=0)), detected)
    assert np.allclose(correction_guide(analysis, replace(settings, enabled=False)), detected)
    assert np.allclose(correction_guide(analysis, replace(settings, mix=0)), detected)
    assert np.allclose(correction_guide(analysis, replace(settings, transpose=2)), 71)
    assert np.array_equal(analysis.detected_midi, detected)


def test_pitch_selection_auditions_range_and_stale_analysis_is_ignored(window, monkeypatch):  # noqa: F811
    source = window.library.add_audio(np.full((48000, 2), 0.1, np.float32), "Dry", kind="recording")
    panel = window.vocal_panel
    panel.open_source(source.id)
    view = panel.pitch_view
    view.resize(700, 300)
    view.shutdown()
    old_generation = view._generation
    view.set_source("other", np.zeros((96000, 2), np.float32), 48000)
    view._finished(old_generation, None, "obsolete failure")
    assert "obsolete" not in view.message
    panel.open_source(source.id)
    QTest.mousePress(view, Qt.LeftButton, pos=QPoint(round(view.x_at(0.2)), 62))
    QTest.mouseMove(view, QPoint(round(view.x_at(0.7)), 62))
    QTest.mouseRelease(view, Qt.LeftButton, pos=QPoint(round(view.x_at(0.7)), 62))
    assert view.selection == pytest.approx((0.2, 0.7), abs=0.004)
    calls = []
    monkeypatch.setattr(window.engine, "audition", lambda *args: calls.append(args))
    panel.preview_original.click()
    assert calls[-1][0] == source.id
    assert calls[-1][1:] == pytest.approx((0.2, 0.7), abs=0.004)
    view.clear_selection()
    panel.preview_original.click()
    assert calls[-1] == (source.id, 0, 0)
    view.shutdown()


def test_pitch_empty_state_does_not_open_an_input():
    view = VocalPitchView()
    view.set_source(None, None, 48000)
    assert view.analysis is None and view.audio is None
    assert "Choose a recorded take" in view.message
    view.shutdown()


@pytest.mark.parametrize("height", (380, 560))
def test_wide_pitch_range_keeps_readable_lanes_and_centers_vocal(height):
    view = VocalPitchView()
    view.resize(720, height)
    # A low phrase plus octave errors like the supplied screenshot.
    notes = np.concatenate((np.linspace(38, 44, 90), np.linspace(74, 86, 10)))
    view.analysis = PitchAnalysis(
        np.linspace(0, 3.4, 100), np.zeros(100), notes, notes, np.ones(100)
    )
    view.guide = notes.copy()
    view.fit_pitch()
    assert abs(view.pitch_y_at(41) - view.pitch_y_at(42)) >= 18
    low, high = view.pitch_bounds()
    assert low < np.median(notes) < high
    assert high < 74  # Outliers remain reachable by scrolling, not by crushing every lane.
    view.pitch_scroll.setValue((127 - 80) * 10)
    assert view.pitch_bounds()[0] < 80 < view.pitch_bounds()[1]
    spacing = view.note_height
    view.zoom_pitch(1.25)
    assert view.note_height > spacing
    for _ in range(20):
        view.zoom_pitch(0.8)
    assert view.note_height >= 18
    view.fit_pitch()
    assert view.pitch_bounds()[0] < np.median(notes) < view.pitch_bounds()[1]
    # View changes must not modify detected pitch or the correction guide.
    np.testing.assert_array_equal(view.analysis.detected_midi, notes)
    np.testing.assert_array_equal(view.guide, notes)
    view.shutdown()


def test_tune_result_cannot_modify_a_replacement_project(window):  # noqa: F811
    import threading
    from mpclab.model import Project

    source = window.library.add_audio(np.zeros((4800, 2), np.float32), "Dry", kind="recording")
    panel = window.vocal_panel
    panel.open_source(source.id)
    panel._render_project = window.project
    panel._render_source_id = source.id
    panel._tune_job_id = 11
    panel._tune_cancel = threading.Event()
    window.project = Project()
    before = set(window.library.clips)
    panel._tune_finished(11, np.zeros((4800, 2), np.float32), None, "Late result")
    assert set(window.library.clips) == before
    assert panel._tune_cancel is None
    assert "Project changed" in panel.analysis_label.text()


def test_comp_panel_does_not_force_pitch_editor_horizontal_overflow(window):  # noqa: F811
    window.show()
    window.resize(1280, 800)
    window.pad_side.show()
    window.show_tab(5)
    QApplication.processEvents()
    scroller = window.vocal_panel.workspace_scroller
    assert scroller.horizontalScrollBar().maximum() == 0, {
        "viewport": scroller.viewport().width(),
        "body_minimum": scroller.widget().minimumSizeHint().width(),
        "keys": [
            (key.text(), key.minimumWidth(), key.minimumSizeHint().width())
            for key in window.vocal_panel.root_keys.values()
        ],
    }
