from types import SimpleNamespace as NS

from mpclab.ui.recording_destination import recording_destination


def window():
    row = NS(id="mic", name="Vocal", record_source="audio")
    capture = NS(
        active=False,
        pending=False,
        target=None,
        armed_id="mic",
        row_monitor=lambda _: {"count_in_bars": 2},
    )
    return NS(
        track_capture=capture,
        project=NS(rows=[row], pattern=lambda: NS(name="Beat 1")),
        studio=NS(selected=1),
        TAB_SEQ=1,
        TAB_PIANO=6,
        TAB_SYNTH=4,
        TAB_PLAYLIST=2,
        engine=NS(mode="song", recording=False, playing=False),
        track_inspector=NS(row=lambda: None),
        _record_count_deadline=None,
    )


def test_pattern_workspace_ignores_armed_microphone():
    w = window()
    assert recording_destination(w) == ("Record → Beat 1 · Pads & notes", "4-beat count-in")
    w.engine.playing = True
    assert recording_destination(w)[1] == "Join playback"


def test_song_shows_armed_audio_and_local_count_in():
    w = window()
    w.studio.selected = 2
    assert recording_destination(w) == ("Record → Vocal · Audio input", "8-beat count-in")
    w.engine.playing = True
    assert recording_destination(w)[1] == "8-beat count-in"


def test_unarmed_song_explains_new_destination():
    w = window()
    w.studio.selected = 2
    w.track_capture.armed_id = None
    assert recording_destination(w) == ("Record → New Song notes track", "4-beat count-in")


def test_active_song_take_keeps_destination_after_navigation():
    w = window()
    w.track_capture.active = True
    w.track_capture.target = w.project.rows[0]
    assert recording_destination(w) == ("Recording → Vocal · Audio input", "Take in progress")
    w._record_count_deadline = 100
    assert recording_destination(w)[1] == "Counting in"


def test_active_pattern_take_does_not_claim_armed_audio_after_navigation():
    w = window()
    w.engine.recording = True
    w.studio.selected = 2
    assert recording_destination(w)[0] == "Recording → Beat 1 · Pads & notes"
