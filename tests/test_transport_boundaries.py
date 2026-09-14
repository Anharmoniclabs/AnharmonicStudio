"""A host buffer crossing a loop seam must retain all its musical time."""

import numpy as np
import pytest

from mpclab.engine import Engine
from mpclab.fx import BlockConvolver
from mpclab.model import Clip


def test_multiple_loop_boundaries_preserve_time_and_retrigger_notes(monkeypatch):
    engine = Engine(object(), sample_rate=100, blocksize=256)
    engine.project.bpm = 60
    engine.project.loop_start = 0
    engine.project.loop_end = 1
    engine.project.rows[0].clips = [
        Clip(kind="pattern", ref=engine.project.pattern().id, start_beat=0, length_beats=1)
    ]
    engine.project.pattern().steps[0] = {0: 1}
    engine.mode, engine.playing, engine.loop_song = "song", True, True
    triggers = []

    def capture(pad, idx, vel, off, *, live_trigger, sequence_id):
        assert not live_trigger  # Loop playback must stay separate from manual taps.
        assert sequence_id == engine.project.rows[0].clips[0].id
        triggers.append(off)

    monkeypatch.setattr(engine, "_spawn", capture)
    for _ in range(10):
        engine._callback(np.zeros((256, 2), np.float32), 256, None, False)
    assert engine.beat == pytest.approx(0.6)
    assert len(triggers) == 26  # 25.6 seconds, including the event at time zero
    assert engine._cb_filled == 10  # one timing measurement per host callback


def test_loop_split_preserves_entire_microphone_cue():
    engine = Engine(object(), sample_rate=48000, blocksize=256)
    engine.project.bpm = 120
    engine.project.loop_end = 128 / 24000
    engine.mode, engine.playing, engine.loop_song = "song", True, True
    engine._monitor_audio[0] = np.linspace(0.01, 0.1, 256)[:, None]
    engine._monitor_lengths[0] = 256
    engine._monitor_write = 1
    out = np.zeros((256, 2), np.float32)
    engine._callback(out, 256, None, False)
    np.testing.assert_allclose(out, engine._monitor_audio[0], atol=1e-6)


def test_short_loop_slices_keep_prepared_filter_and_overlap():
    ir = np.linspace(0.1, 0, 2300, dtype=np.float32)
    kernel = BlockConvolver(ir)
    kernel.prepare(4096)
    source = np.random.default_rng(42).normal(size=(4096, 2)).astype(np.float32)
    expected = np.column_stack([np.convolve(source[:, c], ir)[:4096] for c in range(2)])
    actual = source.copy()
    plans = set(kernel._plans)
    offset = 0
    for size in (1, 127, 128, 768, 3072):
        kernel.process(actual[offset : offset + size], prepared_only=True)
        offset += size
    assert set(kernel._plans) == plans
    np.testing.assert_allclose(actual, expected, atol=2e-6)
