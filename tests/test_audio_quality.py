"""Audio-profile, cue-bus, cache, pan, and meter correctness."""

from __future__ import annotations

import numpy as np

from mpclab.engine import Engine, _balance_gains


class _MemoryLibrary:
    def __init__(self, value: float = 0.1, frames: int = 8_000):
        self.data = np.full((frames, 2), value, dtype=np.float32)

    def audio(self, _sample_id):
        return self.data

    def reversed_audio(self, _sample_id):
        return self.data[::-1]


def _callback(engine: Engine, status=None) -> np.ndarray:
    out = np.zeros((engine.blocksize, 2), dtype=np.float32)
    engine._callback(out, engine.blocksize, None, status)
    return out


def test_stereo_balance_is_unity_at_centre_without_hard_pan_boost():
    assert _balance_gains(0.0) == (1.0, 1.0)
    np.testing.assert_allclose(_balance_gains(-1.0), (1.0, 0.0), atol=1e-7)
    np.testing.assert_allclose(_balance_gains(1.0), (0.0, 1.0), atol=1e-7)


def test_preview_uses_cue_bus_even_when_track_one_is_muted():
    engine = Engine(_MemoryLibrary(), sample_rate=8_000, blocksize=128)
    engine.project.tracks[0].mute = True
    engine.audition("sample", 0.0, 0.5)
    assert float(np.max(np.abs(_callback(engine)))) > 0.01


def test_silent_nonzero_send_knob_does_not_keep_send_engine_awake():
    engine = Engine(_MemoryLibrary(), sample_rate=8_000, blocksize=128)
    engine.project.tracks[0].fx.send_reverb = 1.0
    _callback(engine)
    assert engine._send_tail == 0


def test_rt_cache_miss_does_not_fall_back_to_disk_loader():
    class CacheOnly:
        def cached_audio(self, _sample_id):
            return None

        def cached_reversed_audio(self, _sample_id):
            return None

        def audio(self, _sample_id):
            raise AssertionError("callback attempted a disk-capable load")

    engine = Engine(CacheOnly(), sample_rate=8_000, blocksize=128)
    engine.project.pads[0].sample_id = "not-ready"
    engine.trigger_pad(0)
    out = _callback(engine)
    assert not np.any(out)
    assert engine.cache_misses == 1


def test_buffer_reconfigure_resizes_state_and_clears_live_voices():
    engine = Engine(_MemoryLibrary(), sample_rate=48_000, blocksize=512)
    engine.project.pads[0].sample_id = "sample"
    engine._spawn(engine.project.pads[0], 0, 1.0)
    assert engine.voices
    engine.configure_blocksize(256)
    assert engine.blocksize == 256
    assert engine._tbuf.shape == (8, 256, 2)
    assert engine._preview.shape == (256, 2)
    assert not engine.voices
    assert engine.period_ms == 256 / 48_000 * 1000.0


def test_only_output_underflow_counts_as_an_xrun():
    class UnrelatedStatus:
        output_underflow = False

        def __bool__(self):
            return True

    engine = Engine(_MemoryLibrary(), sample_rate=8_000, blocksize=128)
    _callback(engine, UnrelatedStatus())
    assert engine.underruns == 0
