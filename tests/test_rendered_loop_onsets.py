"""Rendered transport loops must create exactly one audible onset per cycle."""

import numpy as np

from mpclab.engine import Engine
from mpclab.model import Clip, Pad


SR = 8000
BLOCK = 128
BPM = 120
BAR_FRAMES = int(4 * 60 / BPM * SR)


class MemoryLibrary:
    def __init__(self):
        self.click = np.zeros((64, 2), dtype=np.float32)
        self.click[:8] = 0.2

    def audio(self, sample_id):
        return self.click if sample_id == "click" else None

    def reversed_audio(self, sample_id):
        data = self.audio(sample_id)
        return None if data is None else data[::-1]


def test_song_loop_has_one_rendered_onset_per_cycle():
    engine = Engine(MemoryLibrary(), sample_rate=SR, blocksize=BLOCK)
    engine.project.bpm = BPM
    engine.project.pads[0] = Pad(
        sample_id="click",
        start=0,
        end=64 / SR,
        attack=0,
        release=0,
        mode="one-shot",
    )
    pattern = engine.project.pattern()
    pattern.bars = 1
    pattern.steps = {0: {0: 1.0}}
    engine.project.rows[0].clips = [
        Clip(kind="pattern", ref=pattern.id, start_beat=0, length_beats=4)
    ]
    engine.project.loop_start = 0
    engine.project.loop_end = 4
    engine.mode = "song"
    engine.loop_song = True
    engine.play(0)

    cycles = 5
    rendered = np.zeros((cycles * BAR_FRAMES, 2), dtype=np.float32)
    for start in range(0, len(rendered), BLOCK):
        engine._callback(rendered[start : start + BLOCK], BLOCK, None, False)

    energy = np.max(np.abs(rendered), axis=1)
    active = energy > 1e-6
    starts = np.flatnonzero(active & np.r_[True, ~active[:-1]])

    assert len(starts) == cycles
    np.testing.assert_array_equal(np.diff(starts), np.full(cycles - 1, BAR_FRAMES))
