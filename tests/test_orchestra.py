"""Recorded sources, playable articulations and safe asynchronous instrument selection."""

from copy import deepcopy
from dataclasses import replace
import threading
import time

import numpy as np
import pytest
import soundfile as sf
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest

from mpclab import orchestra
from mpclab.engine import Engine
from mpclab.export import render_export
from mpclab.model import Project
from mpclab.music import Note
from mpclab.synth import SynthVoice, patch_copy, render_patch
from mpclab.ui import main_window
from scripts.render_studio_preview import PreviewSettings


@pytest.fixture
def window(tmp_path, monkeypatch):
    monkeypatch.setattr(main_window, "QSettings", PreviewSettings)
    monkeypatch.setattr(Engine, "start", lambda self: None)
    instance = main_window.MainWindow(tmp_path, restore_session=False)
    yield instance
    instance._dirty = False
    instance.close()


def wait_until(predicate, timeout=8):
    deadline = time.monotonic() + timeout
    while not predicate() and time.monotonic() < deadline:
        QTest.qWait(10)
    assert predicate()


@pytest.mark.parametrize("source", list(orchestra.SOURCE_NAMES))
def test_bundled_sources_are_multisampled_and_prepared_readonly(source):
    orchestra.prepare_source(source)
    instrument = orchestra._PREPARED[source]
    assert len({region.root for region in instrument.regions}) >= 5
    assert all(not region.audio.flags.writeable for region in instrument.regions)
    assert all(np.isfinite(region.audio).all() for region in instrument.regions)
    for velocity in (0.15, 0.5, 0.95):
        region = instrument.choose(69, velocity, 0)
        assert region.low_velocity <= round(velocity * 127) <= region.high_velocity


def test_short_string_notes_use_recorded_dynamics_and_alternate_takes():
    orchestra.prepare_source("violin_spiccato")
    instrument = orchestra._PREPARED["violin_spiccato"]
    soft = instrument.choose(69, 0.25, 0)
    strong = instrument.choose(69, 0.9, 0)
    alternate = instrument.choose(69, 0.9, 1)
    assert soft is not strong
    assert alternate is not strong
    assert strong.root == alternate.root == 69
    assert instrument.choose(69, 0.9, 2) is strong


@pytest.mark.parametrize("reverse", [False, True])
def test_sample_layer_loop_is_continuous_across_arbitrary_callback_blocks(reverse):
    sr = 8000
    t = np.arange(sr * 2) / sr
    tone = np.sin(2 * np.pi * 217 * t).astype(np.float32)
    data = np.column_stack((tone, tone))
    region = orchestra.Region(data, sr, 60, 0, 127, 1, 1, 0, 0, len(data))
    instrument = orchestra.Instrument((region,), True)
    layer = orchestra.SampleLayer(instrument, 60, 1, 0, sr, reverse)
    signal = np.concatenate([layer.render(257) for _ in range(120)])
    whole = orchestra.SampleLayer(instrument, 60, 1, 0, sr, reverse).render(len(signal))
    assert np.allclose(signal, whole, atol=1e-7)
    # A crossfade must not introduce jumps beyond the tone's normal slope.
    assert np.max(np.abs(np.diff(signal[:, 0]))) < 0.18
    assert np.sqrt(np.mean(signal[-sr:] ** 2)) > 0.25


def test_motion_and_layer_balance_change_the_rendered_sound():
    patch = patch_copy("Cathedral Glass")
    a = render_patch(replace(patch, layer_mix=0), 69, 1, 16000)
    b = render_patch(replace(patch, layer_mix=1), 69, 1, 16000)
    moving = render_patch(replace(patch, motion=1, lfo_rate=3), 69, 1, 16000)
    plain = render_patch(patch, 69, 1, 16000)
    assert np.sqrt(np.mean((a - b) ** 2)) > 0.002
    assert np.sqrt(np.mean((moving - plain) ** 2)) > 0.002
    assert not np.array_equal(plain[:, 0], plain[:, 1])


def test_sustained_voice_releases_and_sample_render_never_loads_files(monkeypatch):
    patch = patch_copy("Chamber Violins")
    orchestra.prepare_patch(patch)
    voice = SynthVoice(69, 0.8, 48000)
    monkeypatch.setattr(sf, "read", lambda *a, **k: pytest.fail("audio read in voice render"))
    block = np.zeros((512, 2), dtype=np.float32)
    for _ in range(700):
        block.fill(0)
        voice.render(block, patch)
    assert np.sqrt(np.mean(block**2)) > 0.001
    voice.note_off(0.01)
    voice.render(np.zeros_like(block), patch)
    assert voice.dead


def test_recorded_note_timing_and_timbre_match_callback_and_export(window, tmp_path):
    patch = patch_copy("Spiccato Strings")
    window.project.synth = patch
    window.project.bpm = 120
    pattern = window.project.pattern()
    pattern.bars = 1
    pattern.notes = [Note(69, 0.25, 0.5, 0.9), Note(72, 1.25, 0.5, 0.55), Note(69, 2.25, 0.5, 0.9)]
    project = Project.from_dict(window.project.to_dict())
    engine = Engine(window.library, blocksize=512)
    engine.project = project
    engine.preload_project_audio()
    engine.mode = "pattern"
    engine.playing = True
    chunks = []
    for _ in range(188):
        block = np.zeros((512, 2), dtype=np.float32)
        engine._callback(block, 512, None, False)
        chunks.append(block)
    callback = np.concatenate(chunks)[:96000]
    target = tmp_path / "orchestra.wav"
    render_export(project, window.library, target, mode="pattern", tail=0, subtype="FLOAT")
    exported, sr = sf.read(target, dtype="float32", always_2d=True)
    assert sr == 48000 and len(exported) == 96000
    assert np.max(np.abs(callback[:5900])) == 0
    assert np.max(np.abs(callback[6200:20000])) > 0.01
    assert np.allclose(callback, exported, atol=3e-5)
    # Each pitch owns its take counter, so intervening notes do not defeat alternation.
    assert engine._synth_variants[69] == 2


def test_async_selection_is_searchable_undoable_and_persisted(window):
    panel = window.synth_panel
    panel.category.setCurrentText("Orchestral strings")
    panel.sound_search.setText("short bow")
    assert panel.sound_cards.count() == 1
    assert panel.sound_cards.item(0).data(Qt.UserRole) == "Spiccato Strings"
    before = deepcopy(window.project.to_dict())
    panel.load_preset("Spiccato Strings")
    wait_until(lambda: window.project.synth.name == "Spiccato Strings")
    assert len(window._undo) == 1
    assert window.project.synth.sample_source == "violin_spiccato"
    path = window.projects_dir / "orchestral-session.json"
    assert window._save_project_to(path)
    assert window.load_project_path(path)
    assert window.project.synth.sample_source == "violin_spiccato"
    window.undo()
    assert window.project.to_dict() == before
    window.redo()
    assert window.project.synth.sample_source == "violin_spiccato"


def test_slow_load_does_not_block_or_replace_a_later_choice(window, monkeypatch):
    entered, finish = threading.Event(), threading.Event()
    original = orchestra.prepare_patch

    def slow(patch):
        entered.set()
        assert finish.wait(5)
        original(patch)

    monkeypatch.setattr(orchestra, "is_prepared", lambda patch: False)
    monkeypatch.setattr(orchestra, "prepare_patch", slow)
    panel = window.synth_panel
    try:
        panel.load_preset("Chamber Violins")
        wait_until(entered.is_set)
        assert window.project.synth.name == "Midnight Brass"
        panel.load_preset("Clean Sub")
        finish.set()
        wait_until(lambda: window.project.synth.name == "Clean Sub")
        QTest.qWait(150)
        assert window.project.synth.name == "Clean Sub"
        assert len(window._undo) == 1
    finally:
        finish.set()


def test_load_error_preserves_current_sound_and_history(window, monkeypatch):
    monkeypatch.setattr(orchestra, "is_prepared", lambda patch: False)

    def fail(patch):
        raise FileNotFoundError("missing recording")

    monkeypatch.setattr(orchestra, "prepare_patch", fail)
    before = window.project.to_dict()
    window.synth_panel.load_preset("Concert Harp")
    wait_until(lambda: "unavailable" in window.status.currentMessage())
    assert window.project.to_dict() == before
    assert not window._undo


def test_hybrid_macro_drag_has_one_undo(window):
    panel = window.synth_panel
    panel.load_preset("Silk Pulse")
    wait_until(lambda: window.project.synth.name == "Silk Pulse")
    before = deepcopy(window.project.synth)
    count = len(window._undo)
    slider = next(item[0] for item in panel._controls if item[1] == "motion")
    slider.setSliderDown(True)
    slider.setValue(400)
    slider.setValue(200)
    slider.setSliderDown(False)
    assert window.project.synth.motion == 0.2
    assert len(window._undo) == count + 1
    window.undo()
    assert window.project.synth == before


def test_project_with_unavailable_instrument_keeps_current_music(window, monkeypatch):
    project = Project()
    project.synth = patch_copy("Concert Harp")
    path = window.projects_dir / "missing-orchestra.json"
    project.save(path)
    before = deepcopy(window.project.to_dict())

    def fail(patch):
        raise FileNotFoundError("Orchestra recording missing")

    monkeypatch.setattr(main_window, "prepare_instrument_patch", fail)
    warnings = []
    monkeypatch.setattr(main_window.QMessageBox, "warning", lambda *args: warnings.append(args))
    assert not window.load_project_path(path)
    assert window.project.to_dict() == before
    assert warnings


@pytest.mark.parametrize(
    "field,value",
    [
        ("layer_mix", float("nan")),
        ("motion", 2),
        ("layer_octave", 5),
        ("sample_reverse", "true"),
        ("sample_source", []),
    ],
)
def test_invalid_hybrid_parameters_are_rejected(field, value):
    project = Project().to_dict()
    project["synth"][field] = value
    with pytest.raises(ValueError, match=field):
        Project.from_dict(project)
