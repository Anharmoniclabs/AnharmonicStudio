"""Arp recording stores the generated performance for ordinary note playback."""

import numpy as np
import pytest

from mpclab.engine import Engine
from mpclab.model import Project


def render(engine, blocks=64):
    output = np.zeros((blocks, engine.blocksize, 2), dtype=np.float32)
    for block in output:
        engine._callback(block, len(block), None, False)
    return output


@pytest.mark.parametrize("mode", ["up", "down", "up/down", "random"])
def test_recorded_arp_replays_the_live_audio_after_save_and_arp_disabled(mode, tmp_path):
    engine = Engine(object(), sample_rate=8000, blocksize=128)
    project = engine.project
    project.bpm = 120
    project.synth.noise = 0
    project.arp.enabled = True
    project.arp.mode = mode
    project.arp.octaves = 2
    project.arp.rate_beats = 0.25
    project.arp.gate = 0.5
    engine.recording = True
    engine.play(0)
    for pitch in (60, 64, 67):
        engine.synth_note_on(pitch)
    live = render(engine)
    notes = project.pattern().notes
    assert len(notes) == 9
    assert [n.start for n in notes] == pytest.approx(np.arange(9) * 0.25)
    assert [n.duration for n in notes] == pytest.approx([0.125] * 9)
    assert len({n.pitch for n in notes}) > 1
    assert engine.pattern_dirty

    path = tmp_path / "arp.json"
    project.save(path)
    playback = Engine(object(), sample_rate=8000, blocksize=128)
    playback.project = Project.load(path)
    playback.project.arp.enabled = False
    playback.play(0)
    actual = render(playback)
    np.testing.assert_allclose(actual, live, atol=1e-6)
    assert not playback.arp_state.held


def test_arp_does_not_record_during_count_in_or_after_recording_stops():
    engine = Engine(object(), sample_rate=8000, blocksize=128)
    engine.project.arp.enabled = True
    engine.recording = True
    engine.synth_note_on(60)
    render(engine, 8)
    assert not engine.project.pattern().notes
    engine.play(0)
    render(engine, 32)
    notes = list(engine.project.pattern().notes)
    assert notes
    engine.recording = False
    render(engine, 32)
    assert engine.project.pattern().notes == notes


def test_arp_recording_wraps_pattern_positions_without_losing_gate():
    engine = Engine(object(), sample_rate=8000, blocksize=128)
    engine.project.bpm = 120
    engine.project.pattern().bars = 1
    engine.project.arp.enabled = True
    engine.project.arp.rate_beats = 0.25
    engine.project.arp.gate = 0.5
    engine.recording = True
    engine.play(3.9375)
    engine.synth_note_on(60)
    render(engine, 12)
    notes = engine.project.pattern().notes
    assert [n.start for n in notes] == pytest.approx([3.9375, 0.1875])
    assert [n.duration for n in notes] == pytest.approx([0.125, 0.125])
