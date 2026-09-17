import numpy as np

from mpclab.engine import Engine


class _MemoryLibrary:
    def __init__(self):
        wave = np.linspace(-0.5, 0.5, 2048, dtype=np.float32)
        self._audio = np.column_stack((wave, wave)).astype(np.float32)

    def cached_audio(self, _sample_id):
        return self._audio

    def cached_reversed_audio(self, _sample_id):
        return self._audio[::-1]


def _engine():
    engine = Engine(_MemoryLibrary(), sample_rate=1000, blocksize=64)
    engine.project.pads[0].sample_id = "kick"
    engine.set_audio_trace_enabled(True, clear=True)
    return engine


def test_trace_is_disabled_by_default():
    engine = Engine(_MemoryLibrary(), sample_rate=1000, blocksize=64)
    engine.project.pads[0].sample_id = "kick"
    engine._spawn(engine.project.pads[0], 0, 1.0)
    assert engine.audio_trace_snapshot() == ()


def test_pad_trace_names_voice_and_retrigger_reason():
    engine = _engine()
    pad = engine.project.pads[0]
    pad.mode = "gate"
    pad.retrigger = "restart"
    engine._spawn(pad, 0, 1.0)
    first = engine.voices[-1]
    engine._spawn(pad, 0, 0.8, offset=7)

    rows = engine.audio_trace_snapshot()
    assert [row.kind for row in rows] == ["pad_on", "pad_steal", "pad_on"]
    assert rows[1].voice == id(first)
    assert rows[1].source == 0
    assert rows[1].frame == 7
    assert rows[1].reason == "pad_restart"


def test_transport_trace_records_play_seek_stop():
    engine = _engine()
    engine.play(2.0)
    engine.set_position(5.5)
    engine.stop_transport(rewind=True)
    engine._process_commands()

    transport = [row for row in engine.audio_trace_snapshot() if row.kind.startswith("transport_")]
    assert [row.kind for row in transport] == [
        "transport_play",
        "transport_seek",
        "transport_stop",
    ]
    assert transport[1].reason == 5.5
    assert transport[2].reason == "rewind"


def test_synth_trace_records_retriggered_voice():
    engine = _engine()
    engine._spawn_synth(60, 1.0)
    first = engine.synth_voices[-1]
    engine._spawn_synth(60, 0.8, offset=9)

    rows = engine.audio_trace_snapshot()
    assert [row.kind for row in rows] == ["synth_on", "synth_steal", "synth_on"]
    assert rows[1].voice == id(first)
    assert rows[1].source == 60
    assert rows[1].frame == 9
    assert rows[1].reason == "retrigger"
