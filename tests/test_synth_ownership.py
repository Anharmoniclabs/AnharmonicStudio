"""Live performance and pattern playback must release only their own notes."""

from dataclasses import replace

import numpy as np
import pytest

from mpclab.engine import Engine
from mpclab.model import Clip
from mpclab.music import Note


SR, BLOCK = 48000, 512


def make_engine(*, sequence=False, arp=False):
    engine = Engine(object(), sample_rate=SR, blocksize=BLOCK)
    engine.project.synth = replace(
        engine.project.synth,
        attack=0.002,
        decay=0.01,
        sustain=0.8,
        release=0.02,
        noise=0,
        volume=0.1,
    )
    engine.project.bpm = 120
    engine.project.arp.enabled = arp
    engine.project.arp.rate_beats = 1
    engine.project.arp.gate = 1
    if sequence:
        engine.project.pattern().notes = [Note(60, 0, 2, 0.7)]
    engine.project.rows[0].clips = [
        Clip(kind="pattern", ref=engine.project.pattern().id, length_beats=4)
    ]
    return engine


def render(engine, blocks):
    out = np.zeros((blocks, BLOCK, 2), dtype=np.float32)
    for block in out:
        engine._callback(block, BLOCK, None, False)
    assert np.isfinite(out).all()
    return out


@pytest.mark.parametrize("mode", ["pattern", "song"])
@pytest.mark.parametrize("press", [False, True])
def test_live_key_release_preserves_backing_note_audio(mode, press):
    control, performed = make_engine(sequence=True), make_engine(sequence=True)
    for engine in (control, performed):
        engine.mode = mode
        engine.play(0)
        render(engine, 8)
    backing = performed.synth_voices[0]
    if press:
        performed.synth_note_on(60, 0.3)
    render(performed, 4)
    render(control, 4)
    performed.synth_note_off(60)
    actual, expected = render(performed, 64), render(control, 64)
    assert not backing.dead
    assert backing.stage != "release"
    # The live release has finished. The backing sustain must be unchanged.
    np.testing.assert_array_equal(actual[8:], expected[8:])
    assert np.max(np.abs(actual[-8:])) > 0.001
    # The pattern's own gate still releases to exact silence.
    np.testing.assert_array_equal(render(performed, 48), render(control, 48))
    assert not performed.synth_voices


@pytest.mark.parametrize("arp", [False, True])
def test_sequence_retrigger_preserves_held_live_note(arp):
    engine = make_engine(sequence=True, arp=arp)
    engine.synth_note_on(60, 0.4)
    render(engine, 8)
    live = engine.synth_voices[0]
    engine.play(0)
    render(engine, 4)
    assert not live.dead
    assert live.stage != "release"
    assert len(engine.synth_voices) == 2


@pytest.mark.parametrize("mode", ["pattern", "song"])
@pytest.mark.parametrize("command", ["stop", "rewind", "seek"])
@pytest.mark.parametrize("arp", [False, True])
def test_transport_preserves_live_keyboard_and_arp_audio(command, arp, mode):
    control, performed = make_engine(arp=arp), make_engine(arp=arp)
    for engine in (control, performed):
        engine.mode = mode
        engine.play(0)
        engine.synth_note_on(60, 0.4)
        render(engine, 8)
    if command == "seek":
        performed.set_position(1)
    else:
        performed.stop_transport(command == "rewind")
    np.testing.assert_array_equal(render(performed, 32), render(control, 32))
    assert performed.arp_state.held == {60}
    performed.synth_note_off(60)
    assert np.max(np.abs(render(performed, 12)[-4:])) == 0


@pytest.mark.parametrize("command", ["stop", "rewind", "seek"])
def test_transport_still_releases_sequence_notes(command):
    engine = make_engine(sequence=True)
    engine.play(0)
    render(engine, 8)
    if command == "seek":
        engine.set_position(3)
    else:
        engine.stop_transport(command == "rewind")
    assert np.max(np.abs(render(engine, 12)[-4:])) == 0


@pytest.mark.parametrize("source", ["keyboard", "arp", "sequence"])
def test_same_source_retrigger_still_releases_old_note(source):
    engine = make_engine(sequence=source == "sequence", arp=source == "arp")
    if source == "sequence":
        engine.project.pattern().notes.append(Note(60, 0.25, 1, 0.5))
        engine.play(0)
    else:
        engine.synth_note_on(60, 0.4)
    render(engine, 8)
    previous = engine.synth_voices[0]
    if source == "keyboard":
        engine.synth_note_on(60, 0.6)
    render(engine, 48)
    assert previous.dead
    assert engine.synth_voices


@pytest.mark.parametrize("command", ["panic", "synth_panic"])
def test_panic_releases_both_sources(command):
    engine = make_engine(sequence=True)
    engine.play(0)
    engine.synth_note_on(67, 0.4)
    render(engine, 8)
    getattr(engine, command)()
    assert np.max(np.abs(render(engine, 12)[-4:])) == 0
    assert not engine.arp_state.held
