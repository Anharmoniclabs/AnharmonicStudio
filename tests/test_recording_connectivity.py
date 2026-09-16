"""Recording and MIDI acceptance checks without touching desktop devices."""

import ctypes as ct
from types import SimpleNamespace

import numpy as np
import pytest

from mpclab.engine import Engine
from mpclab.midi_devices import MidiRouter
from mpclab.midi_output import MidiOutputService
from mpclab.model import Project
from mpclab.music import MidiControl
from mpclab.native_dsp import NATIVE
from mpclab.native_input import InputQueue


def engine(frames=128):
    return Engine(SimpleNamespace(audio=lambda _id: None), blocksize=frames)


@pytest.mark.skipif(NATIVE is None, reason="Build native DSP")
def test_capture_callback_splits_blocks_and_retains_stereo_and_adc_position():
    q = InputQueue(NATIVE.lib, 16, 2, slots=4)

    class HostTime(ct.Structure):
        _fields_ = [("adc", ct.c_double), ("current", ct.c_double), ("dac", ct.c_double)]

    source = np.arange(70, dtype=np.float32).reshape(35, 2)
    stamp = HostTime(10, 11, 12)
    try:
        q.lib.anh_input_callback(source.ctypes.data, None, 35, ct.byref(stamp), 0, q.handle)
        blocks = []
        for position in (0, 16, 32):
            assert q.read()
            assert q.info.position == position
            assert q.info.adc == pytest.approx(10 + position / 48000)
            blocks.append(q.audio[: q.info.frames].copy())
        np.testing.assert_array_equal(np.concatenate(blocks), source)
        assert not q.read()
    finally:
        q.close()


@pytest.mark.skipif(NATIVE is None, reason="Build native DSP")
def test_capture_overflow_preserves_exact_gap_and_does_not_overwrite_unread_audio():
    q = InputQueue(NATIVE.lib, 8, 1, slots=2)
    source = np.arange(32, dtype=np.float32)
    try:
        q.lib.anh_input_callback(source.ctypes.data, None, 24, None, 0, q.handle)
        assert q.dropped == 8 and q.total == 24
        assert q.read()
        np.testing.assert_array_equal(q.audio[:, 0], source[:8])
        assert q.read()
        np.testing.assert_array_equal(q.audio[:, 0], source[8:16])
        q.lib.anh_input_callback(source[24:].ctypes.data, None, 8, None, 2, q.handle)
        assert q.read() and q.info.position == 24 and q.info.flags == 2
        np.testing.assert_array_equal(q.audio[:, 0], source[24:])
    finally:
        q.close()


def test_monitor_preserves_ramp_through_different_input_and_output_block_sizes():
    e = engine(16)
    source = np.column_stack([np.arange(101, dtype=np.float32)] * 2)
    e.queue_monitor(source[:37])
    blocks = [e.read_monitor(16).copy(), e.read_monitor(16).copy()]
    e.queue_monitor(source[37:])
    while e._monitor_read < e._monitor_write:
        blocks.append(e.read_monitor(16).copy())
    np.testing.assert_array_equal(np.concatenate(blocks)[:101], source)
    assert e.monitor_dropped_frames == 0
    assert e.monitor_missing_frames == 11


def test_midi_performance_renders_without_ui_dispatch_and_preserves_take_timestamps():
    e = engine()
    e.playing = True
    e.project.bpm = 120
    e.beat = 4
    e.audio_clock = (10, 4, 120, True)
    take = e.midi.begin_take(4)
    e.midi.submit("piano", [0x92, 60, 100], 10.1)
    e.midi.process(128, 10.2)
    assert len(e.synth_voices) == 1
    e.midi.submit("piano", [0x82, 60, 35], 10.6)
    e.midi.process(128, 10.7)
    assert len(take.notes) == 1
    note = take.notes[0]
    assert note.start == pytest.approx(0.2)
    assert note.duration == pytest.approx(1.0)
    assert note.channel == 2 and note.release_velocity == 35


def test_note_release_uses_original_instrument_after_selection_change():
    e = engine()
    e.midi.submit("piano", [0x90, 60, 100], 1)
    e.midi.process(128, 1)
    voice = e.synth_voices[0]
    e.midi.route = (None, "different", 0)
    e.midi.submit("piano", [0x80, 60, 0], 1.1)
    e.midi.process(128, 1.1)
    assert voice.stage == "release"


def test_midi_controls_round_trip_with_channel_and_instrument():
    project = Project()
    project.pattern().midi_controls = [MidiControl(1, [0xB3, 64, 95]), MidiControl(2, [0xD3, 80])]
    restored = Project.from_dict(project.to_dict())
    assert restored.pattern().midi_controls == project.pattern().midi_controls
    with pytest.raises(ValueError):
        MidiControl(1, [0xB0, 128, 64])


def test_sostenuto_holds_only_notes_down_when_pressed_and_soft_pedal_changes_velocity():
    calls = []
    r = MidiRouter(
        lambda *a: calls.append(("on", *a)),
        lambda *a: calls.append(("off", *a)),
        lambda *a: None,
        lambda *a: None,
        lambda: 0,
        lambda *a: None,
    )
    r.handle("keys", [0x90, 60, 127])
    r.handle("keys", [0xB0, 66, 127])
    r.handle("keys", [0xB0, 67, 127])
    r.handle("keys", [0x90, 64, 127])
    r.handle("keys", [0x80, 60, 0])
    r.handle("keys", [0x80, 64, 0])
    assert ("off", 64) in calls and ("off", 60) not in calls
    assert ("on", 64, 0.7) in calls
    r.handle("keys", [0xB0, 66, 0])
    assert calls[-1] == ("off", 60)


def test_external_clock_distinguishes_start_continue_and_times_out():
    e = engine()
    e.midi.router.settings["mpc"] = {"clock": True}
    e.midi.submit("mpc", [0xF2, 16, 0], 10)
    e.midi.submit("mpc", [0xFB], 10)
    e.midi.process(128, 10)
    e._process_commands()
    assert e.beat == 4 and e.playing
    for i in range(1, 25):
        e.midi.submit("mpc", [0xF8], 10 + i / 48)
    e.midi.process(128, 10.5)
    assert e.project.bpm == pytest.approx(120)
    e.midi.process(128, 12)
    e._process_commands()
    assert not e.playing and "lost" in e.midi.clock.status


def test_output_clock_has_24_ticks_per_beat_without_ui_and_does_not_burst_after_stall():
    output = MidiOutputService()
    output.clock_enabled = True
    output.transport = (True, 0, 120, 10)
    sent = []
    for i in range(500):
        output.pump_clock(sent.append, 10 + i / 1000)
    assert sent[0] == [0xFA]
    assert sent.count([0xF8]) == 24
    before = len(sent)
    output.pump_clock(sent.append, 12)
    assert len(sent) - before == 1
    assert output.late_ticks > 0
    output.transport = (False, 4, 120, 12)
    output.pump_clock(sent.append, 12.01)
    assert sent[-1] == [0xFC]
