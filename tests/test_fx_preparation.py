"""Tone kernels are prepared before, never constructed in, the callback."""

from __future__ import annotations

from unittest.mock import patch

import numpy as np

from mpclab import fx
from mpclab.engine import Engine


class _MemoryLibrary:
    def __init__(self, frames: int = 48_000):
        t = np.arange(frames, dtype=np.float32) / 48_000.0
        wave = (np.sin(2.0 * np.pi * 220.0 * t) * 0.15).astype(np.float32)
        self.data = np.column_stack((wave, wave))

    def audio(self, _sample_id):
        return self.data

    def reversed_audio(self, _sample_id):
        return self.data[::-1]


def _callback(engine: Engine, frames: int | None = None) -> np.ndarray:
    frames = frames or engine.blocksize
    out = np.zeros((frames, 2), dtype=np.float32)
    engine._callback(out, frames, None, False)
    return out


def _tone_engine() -> Engine:
    engine = Engine(_MemoryLibrary(), sample_rate=48_000, blocksize=128)
    engine.project.pads[0].sample_id = "sample"
    engine.project.pads[0].end = 0.25
    engine.project.tracks[0].gain = 1.0
    engine.project.tracks[0].fx.low = 6.0
    engine.project.master = 1.0
    engine.project.master_fx.high = 2.0
    return engine


def test_prepared_track_and_master_never_build_ir_in_callback():
    engine = _tone_engine()
    engine.prepare_fx()
    engine.trigger_pad(0)

    with patch.object(
        fx, "cascade_ir", side_effect=AssertionError("IR built in callback")
    ) as build:
        out = _callback(engine)

    assert build.call_count == 0
    assert np.max(np.abs(out)) > 0.001
    track = engine.rack.tracks[0]
    master = engine.rack.master
    assert track._tone_key == track.tone_key(engine.project.tracks[0].fx)
    assert master._tone_key == master.tone_key(engine.project.master_fx)
    assert track.tone is not None and track.tone._plans
    assert master.tone is not None and master.tone._plans


def test_live_path_bypasses_unprepared_tone_instead_of_building_it():
    engine = _tone_engine()
    engine.trigger_pad(0)

    with patch.object(
        fx, "cascade_ir", side_effect=AssertionError("IR built in callback")
    ) as build:
        _callback(engine)

    assert build.call_count == 0
    assert engine.rack.tracks[0].tone is None
    assert engine.rack.master.tone is None


def test_unexpected_callback_size_does_not_create_a_filter_fft_plan():
    engine = _tone_engine()
    engine.prepare_fx()
    engine.trigger_pad(0)
    _callback(engine)
    track = engine.rack.tracks[0]
    master = engine.rack.master
    track_plans = set(track.tone._plans)
    master_plans = set(master.tone._plans)

    # 1024 frames requires a larger convolution FFT than the prepared 128-frame
    # stream. The RT-safe behavior is one-block tone bypass, not plan creation.
    _callback(engine, 1024)

    assert set(track.tone._plans) == track_plans
    assert set(master.tone._plans) == master_plans


def test_buffer_reconfigure_queues_a_kernel_for_the_new_fft_size():
    engine = _tone_engine()
    engine.prepare_fx()
    engine.configure_blocksize(256)
    engine.trigger_pad(0)

    with patch.object(fx, "cascade_ir", side_effect=AssertionError("IR built in callback")):
        _callback(engine)

    track = engine.rack.tracks[0]
    expected_size = fx._next_pow2(256 + fx.TONE_TAPS - 1)
    assert expected_size in track.tone._plans
