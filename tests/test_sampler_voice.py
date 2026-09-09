"""Sampler voice range, continuity, and envelope regression tests."""

from __future__ import annotations

import math
import sys
import types

import numpy as np
import pytest

sys.modules.setdefault("sounddevice", types.SimpleNamespace(OutputStream=object))

import mpclab.engine as engine_module
from mpclab.engine import Engine, PadVoice


def _voice(data: np.ndarray, **overrides) -> PadVoice:
    values = {
        "data": data,
        "source_id": "sample",
        "s0": 1,
        "s1": 5,
        "rate": 1.25,
        "gain": 1.0,
        "pan_l": 1.0,
        "pan_r": 1.0,
        "attack": 1,
        "release": 1,
        "length": 16,
        "track": 0,
        "choke": 0,
        "pad_index": 0,
        "loop": False,
    }
    values.update(overrides)
    return PadVoice(**values)


def test_fractional_loop_interpolation_never_reads_outside_source_range():
    """The second interpolation tap at a loop seam must wrap to ``s0``."""
    data = np.zeros((8, 2), dtype=np.float32)
    data[5] = 100.0  # immediately outside the selected [1, 5) range
    output = np.zeros((16, 2), dtype=np.float32)

    _voice(data, loop=True).render(output, 0)

    np.testing.assert_array_equal(output, np.zeros_like(output))


def test_short_pitched_non_loop_never_bleeds_past_source_end():
    """A long envelope may not extend a pitched voice into adjacent audio."""
    data = np.zeros((12, 2), dtype=np.float32)
    data[5:] = 100.0  # audio after the selected [1, 5) range
    output = np.zeros((20, 2), dtype=np.float32)

    # The four-frame range lasts fewer than four output frames at this rate,
    # while attack + release asks for twenty. Source bounds remain authoritative.
    _voice(data, attack=10, release=10, length=20).render(output, 0)

    np.testing.assert_array_equal(output, np.zeros_like(output))


def test_fractional_voice_is_invariant_to_callback_block_partitioning():
    rng = np.random.default_rng(20260822)
    data = rng.standard_normal((128, 2)).astype(np.float32) * 0.1
    kwargs = {
        "s0": 7,
        "s1": 113,
        "rate": 2.0 ** (3.7 / 12.0),
        "attack": 11,
        "release": 17,
        "length": 97,
        "loop": True,
    }

    whole = np.zeros((97, 2), dtype=np.float32)
    _voice(data, **kwargs).render(whole, 0)

    partitioned_voice = _voice(data, **kwargs)
    chunks = []
    for frames in (13, 29, 7, 48):
        block = np.zeros((frames, 2), dtype=np.float32)
        partitioned_voice.render(block, 0)
        chunks.append(block)
    partitioned = np.concatenate(chunks)

    np.testing.assert_allclose(partitioned, whole, rtol=0.0, atol=1e-7)


def test_offline_large_voice_is_chunked_and_bit_identical(monkeypatch):
    sample_rate = 8_000
    frames = engine_module.OFFLINE_VOICE_CHUNK * 2 + 137
    rng = np.random.default_rng(77)
    data = rng.standard_normal((frames, 2)).astype(np.float32) * 0.01

    class _Library:
        def audio(self, _sample_id):
            return data

        def reversed_audio(self, _sample_id):
            return data[::-1]

    engine = Engine(_Library(), sample_rate=sample_rate, blocksize=256)
    engine.project.bpm = 50.0
    engine.project.master = 0.5
    engine.project.tracks[0].gain = 0.5
    engine.project.pattern().bars = 2
    engine.project.pattern().steps = {0: {0: 0.5}}
    pad = engine.project.pads[0]
    pad.sample_id = "sample"
    pad.end = frames / sample_rate
    pad.pitch = 3.7

    calls = []
    original_render = PadVoice.render

    def traced_render(voice, destination, offset, workspace=None):
        calls.append((len(destination), workspace))
        return original_render(voice, destination, offset, workspace)

    monkeypatch.setattr(PadVoice, "render", traced_render)
    chunked = engine.render_offline(mode="pattern", tail=0.0)
    assert len(calls) >= 2
    assert all(length <= engine_module.OFFLINE_VOICE_CHUNK for length, _workspace in calls)
    assert all(workspace is not None for _length, workspace in calls)

    monkeypatch.setattr(PadVoice, "render", original_render)
    monkeypatch.setattr(engine_module, "OFFLINE_VOICE_CHUNK", len(chunked) + 1)
    one_chunk = engine.render_offline(mode="pattern", tail=0.0)
    np.testing.assert_array_equal(chunked, one_chunk)


class _ConstantLibrary:
    def __init__(self, frames: int = 2_000):
        self.data = np.full((frames, 2), 0.1, dtype=np.float32)

    def audio(self, _sample_id):
        return self.data

    def reversed_audio(self, _sample_id):
        return self.data[::-1]


@pytest.mark.parametrize("mode", ["gate", "loop"])
def test_note_off_uses_configured_pad_release(mode: str):
    sample_rate = 1_000
    blocksize = 10
    engine = Engine(_ConstantLibrary(), sample_rate=sample_rate, blocksize=blocksize)
    engine.project.master = 1.0
    engine.project.tracks[0].gain = 1.0

    pad = engine.project.pads[0]
    pad.sample_id = "sample"
    pad.end = 2.0
    pad.mode = mode
    pad.attack = 0.001
    pad.release = 0.050  # 50 frames, deliberately much longer than FADE (4 ms)

    output = np.zeros((blocksize, 2), dtype=np.float32)
    engine.trigger_pad(0)
    engine._callback(output, blocksize, None, False)
    assert any(v.pad_index == 0 and not v.dead for v in engine.voices)

    engine.release_pad(0)
    output.fill(0.0)
    engine._callback(output, blocksize, None, False)

    # Ten milliseconds after note-off, a 50 ms release must still be audible
    # and alive. The fixed four-millisecond choke fade fails both assertions.
    assert any(v.pad_index == 0 and not v.dead for v in engine.voices)
    assert math.fabs(float(output[-1, 0])) > 1e-6


def test_unreleased_gate_stops_at_source_boundary_but_loop_sustains():
    sample_rate = 1_000
    blocksize = 16

    def play_blocks(mode: str, count: int):
        engine = Engine(_ConstantLibrary(), sample_rate=sample_rate, blocksize=blocksize)
        pad = engine.project.pads[0]
        pad.sample_id = "sample"
        pad.end = 0.020  # selected source range is exactly twenty frames
        pad.mode = mode
        pad.attack = 0.001
        pad.release = 0.005
        engine.trigger_pad(0)
        output = np.zeros((blocksize, 2), dtype=np.float32)
        for _ in range(count):
            engine._callback(output, blocksize, None, False)
        return engine

    gate = play_blocks("gate", 2)
    assert not any(v.pad_index == 0 and not v.dead for v in gate.voices)

    loop = play_blocks("loop", 4)
    assert any(v.pad_index == 0 and not v.dead for v in loop.voices)
