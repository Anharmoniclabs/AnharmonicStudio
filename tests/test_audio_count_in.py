"""Count-in clicks and recording starts follow sample time without a GUI timer."""

from types import SimpleNamespace

import numpy as np
import pytest

from mpclab.engine import Engine
from mpclab.library import Library


def test_four_count_in_beats_and_downbeat_without_ui(tmp_path, monkeypatch):
    engine = Engine(Library(tmp_path), sample_rate=48000, blocksize=512)
    engine.project.bpm = 120
    now = [10.0]
    monkeypatch.setattr("mpclab.engine.time.monotonic", lambda: now[0])
    clicks = []
    monkeypatch.setattr(
        engine,
        "_click",
        lambda offset, accent: clicks.append((engine._trace_frame + offset, accent)),
    )
    engine.record_after(12.0, count_beats=4)
    output = np.zeros((512, 2), np.float32)
    for frame in range(0, 98304, 512):
        now[0] = 10 + frame / engine.sr
        engine._callback(output, 512, SimpleNamespace(output_monotonic=now[0]), None)
    assert clicks == [(0, True), (24000, False), (48000, False), (72000, False), (96000, True)]
    assert engine.recording and engine.playing
    assert engine.beat == pytest.approx((98304 - 96000) / 48000 * 2)


def test_cancel_removes_future_clicks_and_start(tmp_path, monkeypatch):
    engine = Engine(Library(tmp_path))
    monkeypatch.setattr("mpclab.engine.time.monotonic", lambda: 10.0)
    clicks = []
    monkeypatch.setattr(engine, "_click", lambda *args: clicks.append(args))
    engine.record_after(14.0, count_beats=4)
    engine._process_commands()
    engine.cancel_count_in()
    engine._process_commands()
    engine._advance_audio_count_in(20.0)
    assert not clicks and not engine.playing and not engine.recording
