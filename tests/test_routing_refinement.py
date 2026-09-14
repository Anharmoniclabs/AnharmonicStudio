"""Regression contracts for destination routing and full-song navigation."""

import numpy as np
import soundfile as sf

from mpclab.model import Pad
from mpclab.ui.sample_drag import send_local_to_arrange
from mpclab.ui.sample_workflow import valid_sample_range
from test_arrangement_workflow import window as window


def test_explicit_arrange_workspace_click_enters_song_mode(window):
    window.set_mode("pattern")
    # Programmatic navigation is allowed to preserve transport state.
    window.studio.select(2)
    assert window.engine.mode == "pattern"

    window.studio.buttons[2].click()
    assert window.studio.selected == 2
    assert window.engine.mode == "song"
    assert window.btn_song.isChecked() and not window.btn_pattern.isChecked()
    assert "SONG mode" in window.status.currentMessage()


def test_sample_replacement_preserves_performance_and_routing_settings(window):
    old = window.library.add_audio(np.zeros((4800, 2), dtype=np.float32), "Old 808")
    new = window.library.add_audio(np.zeros((9600, 2), dtype=np.float32), "New 808")
    window.project.pads[0] = Pad(
        sample_id=old.id,
        name="Old 808",
        start=0.0,
        end=old.duration,
        gain=0.41,
        pan=-0.22,
        pitch=-3.0,
        attack=0.012,
        release=0.21,
        mode="gate",
        choke=4,
        track=6,
        root_note=43,
        mono=True,
        reverse=True,
        sync_beats=4.0,
    )

    result = window.sample_workflow.send(
        new.id,
        0.01,
        0.15,
        destination="notes",
        index=0,
    )
    assert result == 0
    pad = window.project.pads[0]
    assert pad.sample_id == new.id and pad.name == "New 808"
    assert pad.start == 0.01 and pad.end == 0.15
    assert pad.root_note == 43 and pad.mono
    assert pad.gain == 0.41 and pad.pan == -0.22 and pad.pitch == -3.0
    assert pad.attack == 0.012 and pad.release == 0.21
    assert pad.mode == "gate" and pad.choke == 4 and pad.track == 6
    assert not pad.reverse and pad.sync_beats == 0.0


def test_new_notes_instrument_uses_explicit_c4_default(window):
    media = window.library.add_audio(np.zeros((4800, 2), dtype=np.float32), "Lead sample")
    assert window.project.pads[0].empty
    result = window.sample_workflow.send(media.id, destination="notes")
    assert result == 0
    pad = window.project.pads[0]
    assert pad.root_note == 60
    assert pad.mode == "gate"


def test_range_validation_rejects_infinite_outside_and_tiny_ranges(window):
    media = window.library.add_audio(np.zeros((4800, 2), dtype=np.float32), "Range test")
    assert valid_sample_range(media, 0.0, media.duration, 48000) == (0.0, media.duration)
    assert valid_sample_range(media, -0.1, 0.1, 48000) is None
    assert valid_sample_range(media, 0.0, float("inf"), 48000) is None
    assert valid_sample_range(media, 0.0, 1 / 48000, 48000) is None


def test_local_audio_file_drop_maps_whole_file_to_arrange_and_enters_song_mode(window, tmp_path):
    source = tmp_path / "full song.wav"
    audio = np.zeros((9600, 2), dtype=np.float32)
    audio[:, 0] = np.linspace(-0.1, 0.1, len(audio), dtype=np.float32)
    sf.write(source, audio, 48000)
    window.set_mode("pattern")

    assert send_local_to_arrange(window, [source])
    blocks = [clip for row in window.project.rows for clip in row.clips if clip.kind == "audio"]
    assert len(blocks) == 1
    block = blocks[0]
    meta = window.library.clips[block.ref]
    assert meta.name == "full song"
    assert block.offset == 0.0
    assert abs(block.source_length - meta.duration) < 1e-9
    assert abs(block.length_beats - meta.duration * window.project.bpm / 60.0) < 1e-9
    assert window.engine.mode == "song"
    assert window.btn_song.isChecked() and not window.btn_pattern.isChecked()
