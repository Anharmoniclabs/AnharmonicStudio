"""Record a performance directly into a Song lane, without live audio devices."""

import numpy as np
import pytest
import soundfile as sf
from PySide6.QtCore import QPoint, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from mpclab.engine import Engine
from mpclab.export import render_export
from mpclab.model import Project
from mpclab.ui import main_window, theme
from mpclab.ui.playlist import HEAD_W, ROW_H, RULER_H
from scripts.render_studio_preview import PreviewSettings


@pytest.fixture
def window(tmp_path, monkeypatch):
    monkeypatch.setattr(main_window, "QSettings", PreviewSettings)
    monkeypatch.setattr(Engine, "start", lambda self: None)
    app = main_window.MainWindow(tmp_path, restore_session=False)
    app._ui_timer.stop()
    app._autosave_timer.stop()
    app.project.vocal_record.count_in_bars = 0
    yield app
    app.track_capture.active = False
    app.track_capture.pending = False
    app.track_capture.unsaved = None
    app._dirty = False
    app.close()


def arm(window, index=2, source="audio"):
    row = window.project.rows[index]
    row.record_source = source
    window.track_inspector.select_row(row)
    window.track_inspector.arm.click()
    return row


def mock_audio(window, monkeypatch):
    calls = []
    recorder = window.track_capture.recorder
    data = np.column_stack([np.sin(np.arange(48000) * 0.04) * 0.1] * 2).astype(np.float32)
    monkeypatch.setattr(recorder, "start", lambda *args: calls.append(args))
    monkeypatch.setattr(recorder, "stop", lambda: data)
    return data, calls


def test_audio_stays_on_armed_lane_when_selection_and_order_change(window, monkeypatch, tmp_path):
    data, calls = mock_audio(window, monkeypatch)
    row = arm(window)
    row.record_track = 6
    window.engine.beat = 8.25
    window.project.vocal_record.input_latency_ms = 100
    window.btn_rec.click()
    window.engine._process_commands()
    assert window.track_capture.active
    assert calls == [(None, 0.0, None)]
    assert window.engine.mode == "song"
    window.track_inspector.select_row(window.project.rows[0])
    window.project.rows.remove(row)
    window.project.rows.insert(0, row)
    window.track_capture.arm(window.project.rows[1])
    assert window.track_capture.armed_id == row.id
    window.stop_all()
    assert len(row.clips) == 1
    clip = row.clips[0]
    assert clip.start_beat == pytest.approx(8.10)
    assert clip.track == 6
    assert clip.source_length == 1.0
    assert clip.length_beats == 1.5
    assert np.allclose(window.library.audio(clip.ref), data, atol=1e-6)
    assert len(window._undo) == 1
    path = tmp_path / "recorded.json"
    window.project.save(path)
    assert Project.load(path).rows[0].clips[0].ref == clip.ref
    window.undo()
    assert not window.project.rows[0].clips
    window.redo()
    assert window.project.rows[0].clips[0].ref == clip.ref
    assert window.library.audio(clip.ref) is not None


def test_synth_and_sample_notes_keep_off_grid_timing_in_song(window):
    row = arm(window, source="notes")
    window.engine.beat = 12.5
    window.btn_rec.click()
    window.engine._process_commands()
    assert window.track_capture.active
    window.engine.beat = 12.625
    window.play_synth_note(60, 0.63)
    window.engine.beat = 13.0
    window.release_synth_note(60)
    source = window.library.add_audio(np.ones((4800, 2), np.float32) * 0.05, "Sample")
    pad = window.project.pads[0]
    pad.sample_id, pad.end = source.id, source.duration
    window.piano_roll.target_pad = 0
    window.engine.beat = 13.125
    window.play_selected_note(64, 0.71)
    window.engine.beat = 13.75
    window.release_selected_note(64)
    window.stop_all()
    clip = row.clips[0]
    assert clip.start_beat == 12.5
    pattern = next(p for p in window.project.patterns if p.id == clip.ref)
    assert [(n.start, n.duration, n.pitch, n.pad) for n in pattern.notes] == [
        (0.125, 0.375, 60, None),
        (0.625, 0.625, 64, 0),
    ]
    assert len(window._undo) == 1
    window.undo()
    assert not window.project.rows[2].clips


@pytest.mark.parametrize("song", [False, True])
def test_recording_arp_stores_generated_notes_instead_of_held_chord(window, song):
    engine = window.engine
    window.project.bpm = 120
    window.project.arp.enabled = True
    window.project.arp.rate_beats = 0.25
    window.project.arp.gate = 0.5
    if song:
        row = arm(window, source="notes")
        engine.beat = 1.125
        window.btn_rec.click()
    else:
        window.set_mode("pattern")
        window.btn_rec.click()
        window._record_count_deadline = 0
        window._advance_record_count()
    engine._process_commands()
    for pitch in (60, 64, 67):
        window.play_synth_note(pitch, 0.7)
    assert not window._recorded_notes
    assert not window.track_capture.held
    output = np.zeros((engine.blocksize, 2), np.float32)
    for _ in range(int(engine.sr * 0.6 / engine.blocksize)):
        engine._callback(output, len(output), None, False)
    for pitch in (60, 64, 67):
        window.release_synth_note(pitch)
    window.stop_all()
    if song:
        assert row.clips[0].start_beat == 1.125
        pattern = next(p for p in window.project.patterns if p.id == row.clips[0].ref)
    else:
        pattern = window.project.pattern()
    assert [n.pitch for n in pattern.notes] == [60, 64, 67, 60, 64]
    assert [n.start for n in pattern.notes] == pytest.approx([0, 0.25, 0.5, 0.75, 1])
    assert [n.duration for n in pattern.notes] == pytest.approx([0.125] * 5)
    assert engine.arp_note_capture is None
    saved = window.project.to_dict()
    window.undo()
    window.redo()
    assert window.project.to_dict() == saved


def test_cancelled_count_in_does_not_open_input_or_add_history(window, monkeypatch):
    _, calls = mock_audio(window, monkeypatch)
    arm(window)
    window.project.vocal_record.count_in_bars = 1
    window.btn_rec.click()
    assert window.track_capture.pending
    window.stop_all()
    window._advance_record_count()
    assert not window.track_capture.busy
    assert not window.btn_rec.isChecked()
    assert not calls
    assert not window._undo


def test_input_failure_leaves_session_editable(window, monkeypatch):
    arm(window)
    monkeypatch.setattr(
        window.track_capture.recorder,
        "start",
        lambda *_: (_ for _ in ()).throw(RuntimeError("Input unplugged")),
    )
    window.btn_rec.click()
    window._tick()
    assert not window.track_capture.busy
    assert not window.btn_rec.isChecked()
    assert "Input unplugged" in window.track_inspector.state.text()
    assert not window._undo


def test_failed_save_retains_audio_for_retry(window, monkeypatch):
    data, _ = mock_audio(window, monkeypatch)
    window.snapshot()
    window.project.name = "Redo this name"
    window.undo()
    previous_redo = list(window._redo)
    row = arm(window)
    add_audio = window.library.add_audio
    window.btn_rec.click()
    monkeypatch.setattr(
        window.library,
        "add_audio",
        lambda *_args, **_kw: (_ for _ in ()).throw(OSError("Disk full")),
    )
    window.stop_all()
    assert window.track_capture.unsaved is not None
    assert np.array_equal(window.track_capture.unsaved[0], data)
    assert not row.clips
    assert not window._undo
    assert window._redo == previous_redo
    monkeypatch.setattr(window.library, "add_audio", add_audio)
    window.track_inspector.retry.click()
    assert not window.track_capture.busy
    assert len(row.clips) == 1
    assert len(window._undo) == 1


def test_row_arm_button_does_not_toggle_mute_solo_or_add_history(window):
    window.show()
    QApplication.processEvents()
    row = window.project.rows[0]
    QTest.mouseClick(window.playlist, Qt.LeftButton, pos=QPoint(HEAD_W - 55, RULER_H + ROW_H // 2))
    assert window.track_capture.armed_id == row.id
    assert window.track_inspector.row_id == row.id
    assert not row.mute and not row.solo
    assert not window._undo


def test_track_selection_and_recording_preserve_visible_pads(window):
    window.show()
    window.show_tab(2)
    window.select_pad(5)
    QApplication.processEvents()
    assert window.pads.isVisible()
    pad_parent = window.pads.parentWidget()
    row = arm(window, source="notes")
    QApplication.processEvents()
    assert window.song_track_mount.isVisible()
    assert window.pads.isVisible()
    assert window.pads.selected == 5
    assert window.pads.parentWidget() is pad_parent
    window.track_controls_button.click()
    assert window.song_track_mount.isHidden()
    window.track_inspector.select_row(row)
    assert not window.song_track_mount.isHidden()
    assert window.pads.isVisible()
    window.show_tab(1)
    assert window.pads.isVisible()
    assert not window.track_inspector.isVisible()


def test_input_settings_expand_and_collapse_without_clipping(window):
    window.show()
    window.show_tab(2)
    arm(window)
    QApplication.processEvents()
    compact = window.track_controls_scroll.height()
    window.track_inspector.details_button.click()
    for _ in range(3):
        QApplication.processEvents()
    assert window.track_controls_scroll.height() > compact
    assert window.track_inspector.gain.isVisible()
    window.track_inspector.details_button.click()
    for _ in range(3):
        QApplication.processEvents()
    assert window.track_controls_scroll.height() == compact


def test_top_meters_follow_engine_and_ignore_stale_recording_input(window, monkeypatch):
    window.engine.master_meter[:] = (0.25, 0.5)
    window.engine.master_peak = 1.05
    window.engine.cpu = 0.42
    recorder = window.track_capture.recorder
    recorder.input_peak = 0.75
    window._tick()
    meters = window.transport_meters
    assert meters.levels == (0.25, 0.5)
    assert meters.held_peak == 1.05
    assert meters.cpu == 0.42
    assert meters.input_peak == 0 and not meters.input_active
    with monkeypatch.context() as patch:
        patch.setattr(type(recorder), "recording", property(lambda self: self is recorder))
        window._tick()
        assert meters.input_peak == 0.75 and meters.input_active
    window.engine.master_peak = 0.2
    window._tick()
    assert meters.held_peak == 1.05
    QTest.keyClick(meters, Qt.Key_Return)
    window._tick()
    assert meters.held_peak == 0.2
    assert meters.position(0.001) == pytest.approx(0.0)
    assert meters.position(0.1) == pytest.approx(2 / 3)


def test_theme_and_navigation_keep_color_and_editor_owner(window):
    parents = {i: page.parent() for i, page in window.studio.pages.items()}
    window.project.accent_color = "#b88aff"
    for mode in ("dark", "light", "dark"):
        window.apply_theme(mode)
        assert theme.C["accent"] in window.studio.styleSheet()
        for index in (0, 2, 4, 6, 5, 3, 1):
            window.show_tab(index)
            assert window.studio.selected == index
            assert window.tabs.currentIndex() == 8
            assert window.tabs.tabBar().isHidden()
            for i, page in window.studio.pages.items():
                assert page.parent() is parents[i]
    assert window.project.accent_color == "#b88aff"


def test_track_input_settings_round_trip_and_legacy_defaults():
    project = Project()
    project.rows[0].record_source = "notes"
    project.rows[1].record_track = 7
    saved = project.to_dict()
    restored = Project.from_dict(saved)
    assert restored.rows[0].record_source == "notes"
    assert restored.rows[1].record_track == 7
    del saved["rows"][0]["record_source"]
    del saved["rows"][0]["record_track"]
    assert Project.from_dict(saved).rows[0].record_source == "audio"
    assert Project.from_dict(saved).rows[0].record_track == 3
    saved["rows"][0]["record_track"] = 999
    with pytest.raises(ValueError, match="outside the mixer"):
        Project.from_dict(saved)


def test_recorded_audio_plays_through_chosen_mixer_channel(window, monkeypatch, tmp_path):
    mock_audio(window, monkeypatch)
    row = arm(window)
    row.record_track = 6
    window.project.song_length_beats = 4
    window.project.loop_end = 4
    window.engine.beat = 0
    window.btn_rec.click()
    window.stop_all()
    destination = tmp_path / "take.wav"
    render_export(window.project, window.library, destination, tail=0, subtype="FLOAT")
    audible, rate = sf.read(destination)
    assert rate == 48000 and np.max(np.abs(audible)) > 0.01
    window.project.tracks[6].mute = True
    render_export(window.project, window.library, destination, tail=0, subtype="FLOAT")
    muted, _ = sf.read(destination)
    assert not np.any(muted)


def test_song_record_without_an_arm_explains_destination(window):
    window.set_mode("song")
    window.btn_rec.click()
    assert not window.engine.recording
    assert not window.btn_rec.isChecked()
    assert "Arm a Song track" in window.status.currentMessage()
    assert not window._undo


def test_capture_prevents_session_replacement_tempo_changes_and_seek(window, monkeypatch, tmp_path):
    mock_audio(window, monkeypatch)
    arm(window)
    window.engine.beat = 8
    window.btn_rec.click()
    window.engine._process_commands()
    project = window.project
    path = tmp_path / "other.json"
    Project().save(path)
    assert window.new_project() is False
    assert window.load_project_path(path) is False
    window.bpm_box.setValue(150)
    assert window.project is project and project.bpm == 90
    window.playlist.seek.emit(0)
    window.engine._process_commands()
    assert window.engine.beat == 8
    window.studio.buttons[2].click()
    assert window.track_capture.active


def test_note_take_grows_past_pattern_length_without_wrapping(window):
    row = arm(window, source="notes")
    window.engine.beat = 8
    window.btn_rec.click()
    window.engine._process_commands()
    window.engine.beat = 18.125
    window.play_synth_note(64, 0.71)
    window.engine.beat = 19.25
    window.stop_all()
    pattern = next(p for p in window.project.patterns if p.id == row.clips[0].ref)
    assert pattern.bars == 3
    assert pattern.notes[0].start == 10.125
    assert pattern.notes[0].duration == 1.125


def test_color_control_remains_visible_in_narrow_focus_mode(window):
    window.show()
    window.resize(760, 700)
    window.set_playlist_focus(True)
    QApplication.processEvents()
    color = window.btn_color
    assert color.isVisible()
    top_left = color.mapTo(window, QPoint(0, 0))
    assert 0 <= top_left.x() < window.width() - color.width()
    assert top_left.y() < 80
    assert window.song_scroll.height() > window.height() * 0.6


def test_failed_capture_decode_retains_wav_and_destination_for_retry(window, monkeypatch):
    from mpclab import vocal
    from test_vocal import _install_fake_input

    fake = _install_fake_input(monkeypatch)
    row = arm(window)
    window.btn_rec.click()
    fake.streams[0].push(np.full(4800, 0.2))
    path = window.track_capture.recorder.temporary_path
    original = vocal.read_stereo
    monkeypatch.setattr(vocal, "read_stereo", lambda *_: (_ for _ in ()).throw(MemoryError()))
    window.stop_all()
    assert window.track_capture.busy
    assert path.is_file()
    assert not window.close()
    assert not row.clips
    monkeypatch.setattr(vocal, "read_stereo", original)
    window.track_capture.save_take()
    assert not window.track_capture.busy
    assert len(row.clips) == 1
    assert not path.exists()


def test_discarding_unsaved_take_requires_explicit_choice(window, monkeypatch):
    from PySide6.QtWidgets import QMessageBox

    data, _ = mock_audio(window, monkeypatch)
    arm(window)
    window.btn_rec.click()
    monkeypatch.setattr(
        window.library, "add_audio", lambda *_a, **_k: (_ for _ in ()).throw(OSError("full"))
    )
    window.stop_all()
    monkeypatch.setattr(QMessageBox, "question", lambda *_: QMessageBox.No)
    window.track_capture.discard_take()
    assert window.track_capture.unsaved is not None
    monkeypatch.setattr(QMessageBox, "question", lambda *_: QMessageBox.Yes)
    window.track_capture.discard_take()
    assert not window.track_capture.busy
