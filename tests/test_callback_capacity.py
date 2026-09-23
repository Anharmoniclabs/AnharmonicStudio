"""Host buffer variation must preserve audio without growing callback scratch."""

import numpy as np
import pytest

from mpclab.engine import Engine


@pytest.mark.parametrize("frames", [17, 32, 101])
def test_oversized_callback_preserves_monitor_and_prepared_buffers(frames):
    engine = Engine(object(), blocksize=16)
    source = np.linspace(0.01, 0.2, frames * 2, dtype=np.float32).reshape(-1, 2)
    engine.queue_monitor(source)
    names = (
        "_monitor_scratch",
        "_monitor_audio",
        "_tbuf",
        "_master",
        "_bus",
        "_preview",
        "_send_scratch",
        "_meter_scratch",
    )
    buffers = {name: getattr(engine, name) for name in names}
    out = np.full_like(source, np.nan)

    engine._callback(out, frames, None, False)

    np.testing.assert_allclose(out, source, atol=1e-7)
    assert all(getattr(engine, name) is value for name, value in buffers.items())
    assert engine._monitor_read == frames
    assert engine.monitor_dropped_frames == engine.monitor_missing_frames == 0
    assert engine._cb_filled == 1


def test_oversized_callback_keeps_midi_offsets_and_monitor_across_loop_seams(monkeypatch):
    engine = Engine(object(), sample_rate=100, blocksize=16)
    engine.project.bpm = 60
    engine.project.loop_end = 0.25
    engine.mode, engine.playing, engine.loop_song = "song", True, True
    frames = 101
    source = np.linspace(0.01, 0.2, frames * 2, dtype=np.float32).reshape(-1, 2)
    engine.queue_monitor(source)
    events = [
        (0, "start"),
        (16, "at capacity"),
        (24, "before loop"),
        (25, "loop seam"),
        (73, "late"),
        (100, "last sample"),
    ]
    applied = []
    spans = []
    original_render = engine._render_block

    def render(out, count, cue):
        spans.append(count)
        original_render(out, count, cue)

    monkeypatch.setattr(engine.midi, "process", lambda *args, **kwargs: events)
    monkeypatch.setattr(
        engine.midi, "apply_event", lambda event: applied.append((sum(spans), event))
    )
    monkeypatch.setattr(engine, "_render_block", render)
    out = np.full_like(source, np.nan)
    engine._callback(out, frames, None, False)

    assert applied == events
    assert max(spans) <= engine.blocksize
    assert sum(spans) == frames
    assert engine.beat == pytest.approx(0.01)
    assert engine._cb_filled == 1
    np.testing.assert_allclose(out, source, atol=1e-7)
