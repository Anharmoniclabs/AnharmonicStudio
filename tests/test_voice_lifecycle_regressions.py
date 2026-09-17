"""Focused regressions for scheduled voice ownership and gate cleanup."""

from mpclab.engine import Engine
from mpclab.external_dsp import ExternalDSP
from mpclab.library import Library
from mpclab.music import Note


def test_pattern_events_have_exactly_one_onset_at_each_loop_boundary(tmp_path):
    engine = Engine(Library(tmp_path / "audio"), sample_rate=8000, blocksize=128)
    pattern = engine.project.pattern()
    pattern.bars = 1
    pattern.steps = {0: {0: 1.0}, 1: {4: 1.0}}
    pattern.notes = [Note(60, 2, 0.25, 0.8)]

    events, _ = engine._collect(0.0, 32.0)

    assert sum(event[1] == 0 for event in events) == 8
    assert sum(event[1] == 1 for event in events) == 8
    assert sum(event[1] == -61 for event in events) == 8
    assert len(events) == 24
    assert len({(event[0], event[1], event[4]) for event in events}) == len(events)


def test_sequenced_native_same_pitch_voices_keep_placement_owners(tmp_path):
    engine = Engine(Library(tmp_path / "audio"), sample_rate=8000, blocksize=128)

    engine._spawn_synth(60, 1.0, gate_frames=64, live_trigger=False, sequence_id="clip-a")
    engine._spawn_synth(60, 1.0, gate_frames=64, live_trigger=False, sequence_id="clip-b")

    assert len(engine.synth_voices) == 2
    assert {voice.sequence_id for voice in engine.synth_voices} == {"clip-a", "clip-b"}


def test_external_same_pitch_gate_cleanup_is_owner_specific():
    external = ExternalDSP()
    external.note_on(60, 1.0, gate=32, live=False, sequence_id="clip-a")
    external.note_on(60, 1.0, gate=64, live=False, sequence_id="clip-b")

    assert {voice.sequence_id for voice in external.voices} == {"clip-a", "clip-b"}
    external.release_sequence("clip-a")
    assert any(not voice.dead and voice.sequence_id == "clip-b" for voice in external.voices)
    assert all(voice.sequence_id != "clip-a" for voice in external.voices if not voice.dead)
