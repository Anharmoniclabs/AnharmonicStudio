"""Deleting an event stops its current sound, including a just-recorded one-shot."""

import numpy as np
from mpclab.engine import Engine
from mpclab.music import Note
from mpclab.event_source import EventSource
from tests.test_product_hardening_ui import window  # noqa: F401


class MemoryAudio:
    def audio(self, _id):
        return np.full((20000, 2), 0.1, np.float32)


def engine_with_sample():
    engine = Engine(MemoryAudio(), sample_rate=8000, blocksize=128)
    for pad in engine.project.pads[:2]:
        pad.sample_id = "tone"
        pad.mode = "one-shot"
        pad.cut_self = False
        pad.attack = 0.001
    engine.mode = "pattern"
    return engine


def render(engine):
    output = np.zeros((128, 2), np.float32)
    engine._process_commands()
    engine._render_block(output, 128)
    return output


def test_deleted_pitched_event_fades_only_its_voice_and_does_not_retrigger():
    engine = engine_with_sample()
    pattern = engine.project.pattern()
    removed, kept = Note(60, 0, 1, 0.8, 0), Note(64, 0, 1, 0.8, 0)
    pattern.notes = [removed, kept]
    engine.playing = True
    render(engine)
    deleted_voice = next(v for v in engine.voices if v.event_source.note is removed)
    kept_voice = next(v for v in engine.voices if v.event_source.note is kept)
    engine.sample_note_on(0, 67)
    render(engine)
    live = next(v for v in engine.voices if v.note == 67)
    pattern.notes.remove(removed)
    render(engine)
    assert deleted_voice.dead
    assert not kept_voice.dead and not live.dead
    engine.beat = 0
    render(engine)
    assert all(v.event_source is None or v.event_source.note is not removed for v in engine.voices)


def test_deleting_recorded_pad_step_stops_original_live_one_shot():
    engine = engine_with_sample()
    engine.playing = engine.recording = True
    engine.trigger_pad(0)
    render(engine)
    original = next(v for v in engine.voices if v.live_trigger)
    assert original.event_source.pad == 0
    engine.recording = False
    engine.trigger_pad(1)
    render(engine)
    unrelated = next(v for v in engine.voices if v.pad_index == 1)
    engine.project.pattern().steps.clear()
    render(engine)
    assert original.dead
    assert not unrelated.dead
    assert all(v.pad_index != 0 for v in engine.voices)


def test_recorded_sample_token_binds_original_voice_not_newer_performance():
    engine = engine_with_sample()
    token = engine.sample_note_on(0, 60)
    render(engine)
    original = engine.voices[0]
    pattern = engine.project.pattern()
    note = Note(60, 0, 0.125, 0.8, 0)
    pattern.notes.append(note)
    engine.sample_note_on(0, 64)
    engine.bind_recorded_note(pattern, note, token)
    render(engine)
    kept = next(v for v in engine.voices if v.note == 64)
    assert original.event_source.note is note
    pattern.notes.clear()
    render(engine)
    assert original.dead and not kept.dead


def test_deleted_synth_event_does_not_release_unrelated_live_voice():
    engine = engine_with_sample()
    pattern = engine.project.pattern()
    note = Note(60, 0, 4, 0.8)
    pattern.notes.append(note)
    engine.playing = True
    render(engine)
    sequenced = engine.synth_voices[0]
    token = engine.synth_note_on(67)
    render(engine)
    live = next(v for v in engine.synth_voices if v.trigger_id is token)
    pattern.notes.clear()
    render(engine)
    assert sequenced.dead or sequenced.stage == "release"
    assert not live.dead and live.stage != "release"


def test_source_identity_distinguishes_equal_note_objects():
    engine = engine_with_sample()
    pattern = engine.project.pattern()
    removed, kept = Note(60, 0, 1, 0.8, 0), Note(60, 0, 1, 0.8, 0)
    pattern.notes = [kept]
    engine._spawn(engine.project.pads[0], 0, 0.8, event_source=EventSource(pattern, note=removed))
    voice = engine.voices[0]
    render(engine)
    assert voice.dead


def test_sample_recording_ui_binds_tail_then_delete_stops_it(window):  # noqa: F811
    window.library.audio = MemoryAudio().audio
    window.engine._cached_sample = lambda *_: MemoryAudio().audio("tone")
    window.project.pads[0].sample_id = "tone"
    window.project.pads[0].mode = "one-shot"
    window.piano_roll.target_pad = 0
    window.engine.playing = window.engine.recording = True
    window.engine.mode = "pattern"
    window.sample_workflow.note_on(60, 0.8)
    window.engine._process_commands()
    original = window.engine.voices[0]
    window.engine.beat = 0.25
    window.sample_workflow.note_off(60)
    window.engine._process_commands()
    captured = window.project.pattern().notes[-1]
    assert original.event_source.note is captured
    window.piano_roll.canvas.selected = {len(window.project.pattern().notes) - 1}
    window.piano_roll.canvas.delete_selected()
    for _ in range(3):
        render(window.engine)
    assert original.dead
    window.engine.recording = window.engine.playing = False


class MidiCapture:
    def __init__(self):
        self.messages = []

    def render(self, audio, frames, midi=(), **kwargs):
        self.messages.extend(midi)
        return np.zeros((frames, 2), np.float32)


def test_deleted_vst_note_releases_its_channel_without_panicking_live_notes():
    engine = engine_with_sample()
    engine.external.instrument = plugin = MidiCapture()
    pattern = engine.project.pattern()
    removed = Note(60, 0, 4, 0.8)
    pattern.notes = [removed]
    engine.playing = True
    render(engine)
    engine.synth_note_on(67)
    render(engine)
    plugin.messages.clear()
    pattern.notes.clear()
    render(engine)
    assert ([0x81, 60, 0], 0) in plugin.messages
    assert all(message[0][0] not in (0x80, 0xB0, 0xB1) for message in plugin.messages)
    assert any(v.note == 67 and not v.dead for v in engine.external.voices)


def test_deleted_vst_shared_pitch_keeps_other_event_gate():
    engine = engine_with_sample()
    engine.external.instrument = plugin = MidiCapture()
    pattern = engine.project.pattern()
    removed, kept = Note(60, 0, 1, 0.8), Note(60, 0, 2, 0.8)
    pattern.notes = [removed, kept]
    engine.external.note_on(
        60, 0.8, gate=256, live=False, event_source=EventSource(pattern, note=removed)
    )
    engine.external.note_on(
        60, 0.8, gate=512, live=False, event_source=EventSource(pattern, note=kept)
    )
    render(engine)
    pattern.notes.remove(removed)
    plugin.messages.clear()
    render(engine)
    assert not any(message[0][0] == 0x81 for message in plugin.messages)
    assert engine.external.ends == [(256, 1, 60)]
    for _ in range(3):
        render(engine)
    assert [message for message in plugin.messages if message[0][0] == 0x81] == [([0x81, 60, 0], 0)]


def test_live_vst_recording_token_can_be_deleted_while_other_pitch_is_held():
    engine = engine_with_sample()
    engine.external.instrument = plugin = MidiCapture()
    token = engine.synth_note_on(60)
    render(engine)
    pattern = engine.project.pattern()
    captured = Note(60, 0, 1, 0.8)
    pattern.notes.append(captured)
    engine.bind_recorded_note(pattern, captured, token)
    engine.synth_note_on(67)
    render(engine)
    plugin.messages.clear()
    pattern.notes.clear()
    render(engine)
    assert ([0x80, 60, 0], 0) in plugin.messages
    assert not any(message[0] == [0x80, 67, 0] for message in plugin.messages)


def test_same_pitch_vst_gates_expire_in_time_order():
    engine = engine_with_sample()
    engine.external.instrument = plugin = MidiCapture()
    engine.external.note_on(60, 1, gate=90, live=False)
    engine.external.note_on(60, 1, gate=30, live=False)
    render(engine)
    assert [m for m in plugin.messages if m[0][0] == 0x81] == [([0x81, 60, 0], 90 / engine.sr)]


def test_default_notes_channel_does_not_follow_selected_named_instrument(window):  # noqa: F811
    from copy import deepcopy

    other = window.project.add_instrument("Other sound", deepcopy(window.project.synth))
    window.project.selected_instrument = other.id
    window.piano_roll.target_pad = None
    window.piano_roll.target_instrument = None
    window.engine.external.instrument = MidiCapture()
    window.sample_workflow.note_on(60, 0.8)
    window.engine._process_commands()
    assert not window.engine.synth_voices
    assert any(voice.note == 60 for voice in window.engine.external.voices)
    window.engine.external.render_instrument(np.zeros((128, 2), np.float32), 128, window.engine.sr)
    window.sample_workflow.note_off(60)
    window.engine._process_commands()
    assert ([0x80, 60, 0], 0) in window.engine.external.events
    window.engine.external.instrument = None
