"""Selected phrases retain their source, musical duration, and editing history."""

from copy import deepcopy

import numpy as np
import pytest
import soundfile as sf
from PySide6.QtWidgets import QComboBox

from mpclab import detect
from mpclab.engine import Engine
from mpclab.export import render_export
from mpclab.model import Clip, Pad, Project, map_sample_range
from mpclab.ui import main_window
from mpclab.workflow import four_bar_phrase
from scripts.render_studio_preview import PreviewSettings


@pytest.fixture
def window(tmp_path, monkeypatch):
    monkeypatch.setattr(main_window, "QSettings", PreviewSettings)
    monkeypatch.setattr(Engine, "start", lambda self: None)
    instance = main_window.MainWindow(tmp_path, restore_session=False)
    t = np.arange(48_000 * 12) / 48_000
    wave = (0.2 * np.sin(2 * np.pi * 220 * t)).astype(np.float32)
    source = instance.library.add_audio(np.column_stack((wave, wave)), "Synthetic soul")
    instance.load_clip_into_editor(source.id)
    instance.wave.set_selection(1, 9)
    yield instance
    instance._dirty = False
    instance.close()


@pytest.mark.parametrize("pieces", [4, 8, 16])
def test_plan_is_frame_contiguous_and_protects_existing_music(pieces):
    project = Project()
    project.pads[0] = Pad(sample_id="keep", pitch=7)
    project.pattern().steps = {1: {3: 0.63}}  # empty but musically reserved
    before = deepcopy(project.to_dict())
    plan = four_bar_phrase(project, "source", "Phrase", 1.001, 9.137, 480_000, 48_000, pieces)
    assert project.to_dict() == before
    assert len(plan.pads) == pieces
    assert not {0, 1} & {index for index, pad in plan.pads}
    assert plan.pattern.length_beats == 16
    assert plan.pattern.bars == 4
    assert round(plan.pads[0][1].start * 48_000) == round(1.001 * 48_000)
    assert round(plan.pads[-1][1].end * 48_000) == round(9.137 * 48_000)
    for (_, pad), (_, following) in zip(plan.pads, plan.pads[1:], strict=False):
        assert pad.end == following.start
    for i, (index, pad) in enumerate(plan.pads):
        assert plan.pattern.steps[index] == {i * 64 // pieces: 1.0}
        assert pad.sync_beats == 16 / pieces


@pytest.mark.parametrize("start,end", [(-1, 2), (2, 2), (8, 12), (0, float("nan"))])
def test_invalid_range_is_rejected(start, end):
    with pytest.raises(ValueError):
        four_bar_phrase(Project(), "sample", "Phrase", start, end, 480_000, 48_000)


def test_full_banks_leave_project_and_history_untouched(window):
    for pad in window.project.pads:
        pad.sample_id = window.current_clip
    before = window.project.to_dict()
    history = len(window._undo)
    assert window.arrange_four_bar_phrase() is None
    assert window.project.to_dict() == before
    assert len(window._undo) == history
    assert "unused pads" in window.status.currentMessage()


def test_four_bar_selection_uses_chosen_downbeat_and_rejects_short_tail(window):
    window.wave.set_selection(1.123456, 2)
    window.phrase_bpm.setValue(120)
    window.btn_select_phrase.click()
    assert window.wave.selection() == pytest.approx((1.123456, 9.123456), abs=1e-6)
    window.phrase_bpm.setValue(60)
    before = window.wave.selection()
    window.btn_select_phrase.click()
    assert window.wave.selection() == before
    assert "Not enough" in window.status.currentMessage()


def test_phrase_appends_to_selected_lane_with_one_undo_and_saved_history(window):
    previous = Clip(ref=window.project.pattern().id, start_beat=4, length_beats=4.3)
    window.project.rows[2].clips.append(previous)
    window.project.pads[0] = Pad(sample_id=window.current_clip, name="Protected kick")
    window.playlist.select_clip(previous)
    before = deepcopy(window.project.to_dict())
    history = len(window._undo)
    window.btn_arrange_phrase.click()
    placed = window.playlist.selected_clip
    assert placed.start_beat == 12
    assert placed.length_beats == 16
    assert window.project.rows[2].clips[0] == previous
    assert len(window._undo) == history + 1
    assert window.pads.bank == 1
    assert window.project.pads[0].name == "Protected kick"
    after = deepcopy(window.project.to_dict())
    path = window.projects_dir / "four-bars.json"
    assert window._save_project_to(path)
    assert window.load_project_path(path)
    assert window.project.to_dict() == after
    window.undo()
    assert window.project.to_dict() == before
    window.redo()
    assert window.project.to_dict() == after


@pytest.mark.parametrize("bpm", [90, 120, 150])
def test_phrase_playback_and_export_follow_song_tempo(window, tmp_path, bpm):
    window.arrange_four_bar_phrase()
    window.project.bpm = bpm  # tempo change after mapping
    project = Project.from_dict(window.project.to_dict())
    engine = Engine(window.library, blocksize=512)
    engine.project = project
    engine.preload_project_audio()
    pattern = project.pattern()
    for index in pattern.steps:
        voice = engine._voice_for_pad(project.pads[index], 1)
        assert voice.rate == pytest.approx(bpm / 120)
        assert abs(voice.length - 48_000 * 60 / bpm) <= 1
    events, _ = engine._collect(0, 16)
    assert [event[0] for event in events] == list(range(16))
    engine.trigger_pad(next(iter(pattern.steps)))
    chunks = []
    for _ in range(24):
        block = np.zeros((512, 2), dtype=np.float32)
        engine._callback(block, 512, None, False)
        chunks.append(block)
    callback = np.concatenate(chunks)
    destination = tmp_path / "phrase.wav"
    render_export(project, window.library, destination, mode="pattern", tail=0, subtype="FLOAT")
    exported, sr = sf.read(destination, dtype="float32", always_2d=True)
    assert abs(len(exported) - 16 * 60 * sr / bpm) <= 1
    for signal in (callback, exported):
        assert np.isfinite(signal).all()
        segment = signal[2400:11000, 0].astype(np.float64)
        points = np.flatnonzero((segment[:-1] <= 0) & (segment[1:] > 0))
        crossings = points - segment[points] / (segment[points + 1] - segment[points])
        assert sr / np.diff(crossings).mean() == pytest.approx(220 * bpm / 120, rel=0.0001)
    # Every source beat is audible; no truncation to the old two-bar pattern.
    beat_frames = sr * 60 / bpm
    for beat in range(16):
        middle = exported[round((beat + 0.2) * beat_frames) : round((beat + 0.8) * beat_frames)]
        assert np.sqrt(np.mean(middle**2)) > 0.01


def test_tempo_sync_can_be_disabled_and_undone_and_retires_old_control(window):
    window.arrange_four_bar_phrase()
    index = window.pads.selected
    control = window.pad_inspector.findChild(QComboBox, "padTempoSync")
    control.setCurrentIndex(control.findData(0.0))
    assert window.project.pads[index].sync_beats == 0
    window.undo()
    assert window.project.pads[index].sync_beats == 1
    before = deepcopy(window.project.to_dict())
    history = len(window._undo)
    control.setCurrentIndex(control.findData(4.0))
    assert window.project.to_dict() == before
    assert len(window._undo) == history


def test_replacing_or_remapping_pad_removes_phrase_sync(window):
    window.arrange_four_bar_phrase()
    pad = window.project.pads[0]
    map_sample_range(pad, window.current_clip, 2, 3)
    assert pad.sync_beats == 0
    pad.sync_beats = 1
    window.assign_sample_to_pad(0, window.current_clip)
    assert pad.sync_beats == 0


def test_rebuilding_same_pad_retires_old_tempo_control(window):
    window.arrange_four_bar_phrase()
    control = window.pad_inspector.findChild(QComboBox, "padTempoSync")
    window.pad_inspector.rebuild()
    before = deepcopy(window.project.to_dict())
    history = len(window._undo)
    control.setCurrentIndex(control.findData(4.0))
    assert window.project.to_dict() == before
    assert len(window._undo) == history


@pytest.mark.parametrize("value", [float("nan"), float("inf"), -1, "invalid"])
def test_invalid_saved_sync_is_rejected(value):
    data = Project().to_dict()
    data["pads"][0]["sync_beats"] = value
    with pytest.raises(ValueError, match="sync_beats"):
        Project.from_dict(data)


def test_default_scan_offers_complete_four_bar_phrases():
    sr = 8000
    t = np.arange(sr * 16) / sr
    wave = (np.sin(2 * np.pi * 110 * t) * (0.5 + 0.4 * np.cos(4 * np.pi * t))).astype(np.float32)
    loops = detect.find_loops(wave, sr=sr, bpm=120, phase=0)
    assert loops
    assert loops[0].detail["bars"] == 4
    assert loops[0].length == 8
    assert all(candidate.end <= 16 for candidate in loops)


def test_scan_does_not_auto_map_after_switching_sample_or_project(window, monkeypatch):
    original = window.current_clip
    other = window.library.add_audio(np.zeros((48_000, 2), dtype=np.float32), "Other sample")
    result = {"bpm": 120, "hits": [], "by_kind": {}, "loops": [], "drops": []}
    calls = []
    monkeypatch.setattr(window, "auto_map", lambda: calls.append(window.current_clip))
    window._scan_then_map = True
    window._scan_project = window.project
    window.load_clip_into_editor(other.id)
    window._scan_finished(original, result)
    assert not calls
    assert not window._scan_then_map
    window._scans.clear()
    window._scan_then_map = True
    window.project = Project()
    window._scan_finished(original, result)
    assert not window._scans
    assert not calls


def test_scan_shows_every_cut_without_trimming_phrases_to_hit_tails(window):
    hits = [detect.Candidate(i * 0.25 + 1, i * 0.25 + 1.08, "tonal", 0.8) for i in range(24)]
    result = {
        "bpm": 120,
        "hits": hits,
        "onsets": [hit.start for hit in hits],
        "by_kind": {"tonal": hits[:4]},
        "loops": [],
        "drops": [],
    }
    window._scan_project = window.project
    window._scan_finished(window.current_clip, result)
    assert window.wave.markers == result["onsets"]
    assert len(window.hits_for(window.current_clip)) == 24
    assert window.wave.slice_bounds(0) == pytest.approx((1, 1.25))
    assert len(window._scans[window.current_clip]["by_kind"]["tonal"]) == 4
