"""The floating keyboard follows the visible instrument and retains note ownership."""

import pytest

from mpclab.ui.typing_keyboard import TypingKeyboardWindow
from tests.test_track_recording import arm, window as recording_window  # noqa: F401


@pytest.fixture
def window(recording_window):  # noqa: F811 - pytest injects the imported fixture
    return recording_window


def test_instruments_record_notes_despite_stale_sample_selection(window, monkeypatch):
    window.show_tab(window.TAB_SYNTH)
    window.piano_roll.target_pad = 0
    window.engine.playing = True
    window.engine.recording = True
    window.engine.mode = "pattern"
    window.engine.beat = 0.25
    played = []
    monkeypatch.setattr(window.engine, "synth_note_on", lambda *a, **kw: played.append((a, kw)))
    keyboard = TypingKeyboardWindow(window)
    keyboard._note_on(1, 60, 0.8)
    window.engine.beat = 0.75
    keyboard._note_off(1)
    assert played == [((60, 0.8), {})]
    note = window.project.pattern().notes[-1]
    assert note.pitch == 60
    assert note.pad is None
    assert note.instrument is None
    assert not window.sample_workflow.recorded


def test_release_retains_instrument_when_workspace_and_selection_change(window, monkeypatch):
    window.show_tab(window.TAB_SYNTH)
    played, released = [], []
    monkeypatch.setattr(window, "play_synth_note", lambda *a, **kw: played.append((a, kw)))
    monkeypatch.setattr(window, "release_synth_note", lambda *a, **kw: released.append((a, kw)))
    keyboard = TypingKeyboardWindow(window)
    keyboard._note_on(1, 60, 0.8)
    window.show_tab(window.TAB_PIANO)
    window.project.selected_instrument = "another-instrument"
    keyboard._note_off(1)
    assert played == [((60, 0.8), {"instrument_id": None})]
    assert released == [((60,), {"instrument_id": None})]


def test_sustained_retrigger_releases_previous_destination(window, monkeypatch):
    window.show_tab(window.TAB_SYNTH)
    events = []
    monkeypatch.setattr(window, "play_synth_note", lambda *a, **kw: events.append("instrument on"))
    monkeypatch.setattr(
        window, "release_synth_note", lambda *a, **kw: events.append("instrument off")
    )
    monkeypatch.setattr(window, "play_selected_note", lambda *a: events.append("sample on"))
    monkeypatch.setattr(window, "release_selected_note", lambda *a: events.append("sample off"))
    keyboard = TypingKeyboardWindow(window)
    keyboard.sustain.setChecked(True)
    keyboard._note_on(1, 60, 0.8)
    keyboard._note_off(1)
    window.show_tab(window.TAB_PIANO)
    keyboard._note_on(1, 60, 0.8)
    keyboard._note_off(1)
    keyboard.sustain.setChecked(False)
    assert events == ["instrument on", "instrument off", "sample on", "sample off"]


@pytest.mark.parametrize("workspace", ["TAB_PIANO", "TAB_SYNTH"])
def test_record_in_notes_workspaces_does_not_open_armed_microphone(window, monkeypatch, workspace):
    arm(window)
    calls = []
    monkeypatch.setattr(window.track_capture.recorder, "start", lambda *a: calls.append(a))
    window.show_tab(getattr(window, workspace))
    window.engine.mode = "song"
    window.engine.playing = True
    window.btn_rec.click()
    assert window.engine.mode == "pattern"
    assert window.engine.recording
    assert not window.track_capture.active
    assert not window.track_capture.pending
    assert calls == []
    window.btn_rec.click()


def test_notes_sound_selector_names_loaded_vst(window, monkeypatch):
    window.project.plugins["instrument"] = {"path": "/plugins/Prism.vst3"}
    with monkeypatch.context() as patch:
        patch.setattr(window.engine.external, "instrument", object())
        window.piano_roll.sync_channels()
        assert window.piano_roll.channel.itemText(0) == "VST · Prism"
        assert window.piano_roll.channel.itemData(0) is None


@pytest.mark.parametrize("release", ["note_off", "unplug", "sustain"])
def test_midi_records_visible_prism_despite_stale_sample_selection(window, monkeypatch, release):
    window.show_tab(window.TAB_SYNTH)
    window.piano_roll.target_pad = 0
    window.engine.playing = window.engine.recording = True
    window.engine.mode = "pattern"
    window.engine.beat = 0.25
    window.engine.audio_clock = (10, 0.25, 120, True)
    window.devices._tick()
    performance = window.engine.midi
    performance.source = None
    played, released = [], []
    monkeypatch.setattr(window.engine, "_spawn_synth", lambda *a, **kw: played.append((a, kw)))
    monkeypatch.setattr(window.engine, "_release_synth", lambda *a, **kw: released.append((a, kw)))

    def event(message, timestamp):
        performance.submit("keys", message, timestamp)
        performance.process(128, timestamp)

    event([0x92, 60, 100], 10)
    window.show_tab(window.TAB_PIANO)
    performance.route = (0, None, 0)
    if release == "unplug":
        event([], 10.25)
    elif release == "sustain":
        event([0xB2, 64, 127], 10.1)
        event([0x82, 60, 0], 10.25)
        assert not released
        event([0xB2, 64, 0], 10.3)
    else:
        event([0x82, 60, 0], 10.25)
    assert len(played) == len(released) == 1
    assert played[0][1]["instrument_id"] is None
    assert released[0][0] == (60, None)
    assert len(window.project.pattern().notes) == 1
    note = window.project.pattern().notes[0]
    assert (note.pitch, note.start, note.duration, note.pad, note.instrument, note.channel) == (
        60,
        0.25,
        0.5,
        None,
        None,
        2,
    )
    assert not window.sample_workflow.recorded


def test_midi_router_uses_selected_instrument_destination(window, monkeypatch):
    from mpclab.model import SynthPatch

    window.show_tab(window.TAB_SYNTH)
    instrument = window.project.add_instrument("Selected", SynthPatch())
    window.piano_roll.select_channel(instrument.id)
    window.devices._tick()
    calls = []
    monkeypatch.setattr(window.engine, "_spawn_synth", lambda *a, **kw: calls.append((a, kw)))
    window.devices.router.handle("keys", [0x90, 60, 100])
    assert len(calls) == 1
    assert calls[0][1]["instrument_id"] == instrument.id
