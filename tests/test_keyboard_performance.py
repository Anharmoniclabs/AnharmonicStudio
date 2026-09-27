"""Typed chord timing, sample routing, and responsive audio-command ordering."""

import numpy as np
import pytest
import queue
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from mpclab.model import SynthPatch
from mpclab.ui.typing_keyboard import TypingKeyboardWindow
from tests.test_product_hardening_ui import window  # noqa: F401


def setup_keyboard(window, sampled):  # noqa: F811
    window.show_tab(window.TAB_PIANO)
    if sampled:
        clip = window.library.add_audio(np.full((48000, 2), 0.1, np.float32), "Chord sample")
        window.sample_workflow.send(clip.id, index=0, destination="notes")
        window.project.pads[0].mono = False
    else:
        instrument = window.project.add_instrument("Chord keys", SynthPatch())
        window.piano_roll.select_channel(instrument.id)
    keyboard = TypingKeyboardWindow(window)
    window.typing_keyboard = keyboard
    keyboard.show()
    window.engine.playing = window.engine.recording = True
    window.engine.mode = "pattern"
    window.project.pattern().bars = 4
    return keyboard


def queued(engine):
    commands = []
    while True:
        try:
            commands.append(engine.cmds.get_nowait())
        except queue.Empty:
            break
    for command in commands:
        engine.cmds.put(command)
    return commands


@pytest.mark.parametrize("sampled", [False, True])
def test_typed_chord_uses_audible_clock_and_one_deferred_refresh(window, monkeypatch, sampled):  # noqa: F811
    from mpclab import recording_timing

    keyboard = setup_keyboard(window, sampled)
    now = [101.0]
    monkeypatch.setattr(recording_timing.time, "monotonic", lambda: now[0])
    window.engine.beat = 4
    window.engine.audio_clock = (101.05, 4, 120, True)
    window.project.bpm = 120
    refreshed = []
    monkeypatch.setattr(window.piano_roll.canvas, "refresh", lambda: refreshed.append(True))
    keys = (Qt.Key_Z, Qt.Key_C, Qt.Key_B)
    for key in keys:
        QTest.keyPress(keyboard.keyboard, key)
    window.engine._process_commands()
    voices = window.engine.voices if sampled else window.engine.synth_voices
    assert len(voices) == 3
    # Changing the selected sound must not retarget held notes or their release.
    window.piano_roll.select_channel(None)
    refreshed.clear()
    now[0] = 101.25
    window.engine.beat = 6  # render position is intentionally ahead of audible time
    for key in keys:
        QTest.keyRelease(keyboard.keyboard, key)
    notes = window.project.pattern().notes
    assert len(notes) == 3
    assert {n.pitch for n in notes} == {keyboard.keyboard.base_note + n for n in (0, 4, 7)}
    assert all(n.start == pytest.approx(3.9) and n.duration == pytest.approx(0.5) for n in notes)
    assert all(n.pad == (0 if sampled else None) for n in notes)
    if not sampled:
        assert all(n.instrument == window.project.instruments[0].id for n in notes)
    commands = queued(window.engine)
    assert sum(c[0] == ("sampleoff" if sampled else "synthoff") for c in commands) == 3
    assert not refreshed
    QApplication.processEvents()
    assert refreshed == [True]
    assert not keyboard._held_keys and not keyboard._note_destinations


@pytest.mark.parametrize("sampled", [False, True])
def test_short_typing_note_is_not_stretched_to_a_sixteenth_beat(window, monkeypatch, sampled):  # noqa: F811
    from mpclab import recording_timing

    keyboard = setup_keyboard(window, sampled)
    now = [10.0]
    monkeypatch.setattr(recording_timing.time, "monotonic", lambda: now[0])
    window.engine.audio_clock = (10, 1, 120, True)
    window.project.bpm = 120
    QTest.keyPress(keyboard.keyboard, Qt.Key_Z)
    now[0] += 0.002
    QTest.keyRelease(keyboard.keyboard, Qt.Key_Z)
    note = window.project.pattern().notes[0]
    assert note.start == 1
    assert note.duration == pytest.approx(0.004)


@pytest.mark.parametrize("sampled", [False, True])
def test_note_is_enqueued_before_capture_bookkeeping(window, monkeypatch, sampled):  # noqa: F811
    keyboard = setup_keyboard(window, sampled)
    expected = "sampleon" if sampled else "synthon"
    observed = []
    monkeypatch.setattr(
        window.track_capture,
        "note_on",
        lambda *a, **k: observed.append(any(c[0] == expected for c in queued(window.engine))),
    )
    QTest.keyPress(keyboard.keyboard, Qt.Key_Z)
    assert observed == [True]
    QTest.keyRelease(keyboard.keyboard, Qt.Key_Z)


def test_mouse_piano_retains_keydown_instrument_across_selection(window):  # noqa: F811
    setup_keyboard(window, False)
    instrument = window.project.selected_instrument
    window.engine.beat = 1
    window.play_synth_note(60, 0.8)
    window.piano_roll.select_channel(None)
    assert not window.project.pattern().notes  # selection must not terminate the take
    window.engine.beat = 1.5
    window.release_synth_note(60)
    note = window.project.pattern().notes[0]
    assert (note.instrument, note.start, note.duration) == (instrument, 1, 0.5)
    assert ("synthoff", 60, instrument) in queued(window.engine)
    assert not window._synth_note_owners
