"""Layered patterns keep their own sample articulation, live and in exports."""

from copy import deepcopy

import numpy as np
import pytest

from mpclab import engine as engine_module
from mpclab.engine import Engine, FADE
from mpclab.model import Clip, Pad, Pattern, Project

SR = 8000
BLOCK = 128


class MemoryLibrary:
    def __init__(self):
        self.data = np.full((SR, 2), 0.025, dtype=np.float32)

    def audio(self, ref):
        return self.data

    def reversed_audio(self, ref):
        return self.data[::-1]


def layered_engine(rule="source", same_pattern=False):
    engine = Engine(MemoryLibrary(), sample_rate=SR, blocksize=BLOCK)
    project = engine.project
    project.bpm = 120
    project.self_choke = rule == "source"
    for index in (0, 16, 32):
        project.pads[index] = Pad(
            sample_id="song" if rule != "group" or index == 0 else "other",
            choke=3 if rule == "group" else 0,
            mode=rule if rule in ("gate", "loop") else "one-shot",
            start=0,
            end=1,
        )
    first = project.pattern()
    first.bars = 1
    if rule in ("gate", "loop"):
        first.steps = {0: {0: 1.0, 2: 1.0}}
        layer_pad = 0
    else:
        first.steps = {0: {0: 1.0}, 16: {2: 1.0}}
        layer_pad = 32
    second = first if same_pattern else Pattern(name="Layer", bars=1, steps={layer_pad: {0: 1.0}})
    if not same_pattern:
        project.patterns.append(second)
    project.rows[0].clips = [Clip(ref=first.id, start_beat=0, length_beats=4)]
    project.rows[1].clips = [Clip(ref=second.id, start_beat=0.125, length_beats=4)]
    engine.mode = "song"
    return engine


def render_live(engine, blocks):
    output = np.zeros((blocks, BLOCK, 2), dtype=np.float32)
    for block in output:
        engine._callback(block, BLOCK, None, False)
    assert np.isfinite(output).all()
    return output


@pytest.mark.parametrize("rule", ["source", "group", "gate", "loop"])
def test_later_hit_cuts_its_sequence_but_preserves_other_pattern(rule):
    engine = layered_engine(rule)
    engine.play(0)
    render_live(engine, 8)  # Both patterns have started; first pattern's next hit is pending.
    first_id = engine.project.rows[0].clips[0].id
    layer_id = engine.project.rows[1].clips[0].id
    first = next(v for v in engine.voices if v.sequence_id == first_id)
    layer = next(v for v in engine.voices if v.sequence_id == layer_id)
    natural = layer.length
    render_live(engine, 12)  # Cross the next hit at 0.25 seconds.
    assert first.dead
    assert not layer.dead
    assert layer.length == natural
    assert {v.sequence_id for v in engine.voices} == {first_id, layer_id}


@pytest.mark.parametrize("rule", ["source", "group", "gate", "loop"])
@pytest.mark.parametrize("same_pattern", [False, True])
def test_live_mix_equals_independent_layers(rule, same_pattern):
    layered = layered_engine(rule, same_pattern)
    layers = []
    for index in range(2):
        engine = layered_engine(rule, same_pattern)
        engine.project.rows[1 - index].mute = True
        engine.play(0)
        layers.append(render_live(engine, 50))
    layered.play(0)
    np.testing.assert_allclose(render_live(layered, 50), layers[0] + layers[1], atol=1e-7)


@pytest.mark.parametrize("rule", ["source", "group", "gate", "loop"])
@pytest.mark.parametrize("same_pattern", [False, True])
def test_export_matches_independent_layers_and_reference(rule, same_pattern):
    engine = layered_engine(rule, same_pattern)
    # Ownership is recovered from saved clips; no migration or new project setting.
    engine.project = Project.from_dict(engine.project.to_dict())
    before = deepcopy(engine.project.to_dict())
    combined = engine.render_offline(mode="song", tail=0)
    reference = engine._render_offline_reference(mode="song", tail=0)
    np.testing.assert_array_equal(combined, reference)
    layers = []
    for index in range(2):
        engine.project.rows[1 - index].mute = True
        layers.append(engine.render_offline(mode="song", tail=0))
        engine.project.rows[1 - index].mute = False
    np.testing.assert_allclose(combined, layers[0] + layers[1], atol=1e-7)
    assert engine.project.to_dict() == before


@pytest.mark.parametrize("first_offset", [0, 32])
def test_cut_starts_at_the_new_hit_within_the_audio_buffer(first_offset):
    engine = layered_engine()
    engine._spawn(
        engine.project.pads[0],
        0,
        1,
        first_offset,
        live_trigger=False,
        sequence_id="first",
    )
    first = engine.voices[-1]
    uncut = deepcopy(first)
    engine._spawn(
        engine.project.pads[16],
        16,
        1,
        96,
        live_trigger=False,
        sequence_id="first",
    )
    assert first.length == 96 - first_offset + int(FADE * SR)
    out = np.zeros((BLOCK, 2), dtype=np.float32)
    expected = np.zeros_like(out)
    first.render(out, first_offset)
    uncut.render(expected, first_offset)
    np.testing.assert_array_equal(out[:96], expected[:96])
    assert np.max(out[112:]) < np.min(expected[112:])


def test_pattern_repeats_keep_cutting_their_own_previous_cycle():
    engine = layered_engine("loop")
    engine.project.rows[0].clips[0].length_beats = 8
    engine.project.rows[1].mute = True
    engine.project.pattern().steps = {0: {0: 1}}
    engine.play(0)
    render_live(engine, 10)
    previous = engine.voices[0]
    render_live(engine, 120)
    assert previous.dead
    assert len(engine.voices) == 1
    assert engine.voices[0].sequence_id == previous.sequence_id


def test_voice_limit_preserves_other_pattern_layers(monkeypatch):
    engine = layered_engine()
    monkeypatch.setattr(engine_module, "MAX_PAD_VOICES", 2)
    for owner in ("first", "layer"):
        engine._spawn(engine.project.pads[0], 0, 1, live_trigger=False, sequence_id=owner)
    first, layer = engine.voices
    engine._spawn(engine.project.pads[16], 16, 1, live_trigger=False, sequence_id="first")
    assert layer in engine.voices
    assert first not in engine.voices
    assert layer.length == SR
    assert len(engine.voices) == 2
