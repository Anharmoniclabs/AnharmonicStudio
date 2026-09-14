"""Deterministic regressions for loop, render and callback hardening."""

from __future__ import annotations

import hashlib

import numpy as np

import mpclab.engine as engine_module
from mpclab.audio_kernel import AUDIO_BUFFER_PROFILES
from mpclab.engine import Engine, PadRenderWorkspace, PadVoice
from mpclab.model import Clip, NTRACKS, Project
from scripts.bench_callback import verdict


class _Library:
    def __init__(self, frames: int = 4_096, sample_rate: int = 1_000):
        t = np.arange(frames, dtype=np.float32) / sample_rate
        tone = np.sin(np.float32(2 * np.pi * 87.0) * t).astype(np.float32)
        self.data = np.column_stack((tone, tone)).astype(np.float32)

    def audio(self, _sample_id):
        return self.data

    def reversed_audio(self, _sample_id):
        return np.ascontiguousarray(self.data[::-1])


def _voice(
    data: np.ndarray,
    *,
    quality: str = "live",
    rate: float = 1.0,
    loop: bool = False,
    crossfade: int = 0,
    length: int = 220,
) -> PadVoice:
    return PadVoice(
        data=data,
        source_id="sample",
        s0=0,
        s1=len(data),
        rate=rate,
        gain=0.5,
        pan_l=1.0,
        pan_r=1.0,
        attack=1,
        release=1,
        length=length,
        track=0,
        choke=0,
        pad_index=0,
        loop=loop,
        loop_crossfade=crossfade,
        quality=quality,
    )


def test_loop_crossfade_is_click_free_and_has_stable_golden_output():
    data = np.zeros((100, 2), dtype=np.float32)
    data[:10] = -1.0
    data[10:90] = np.linspace(-1.0, 1.0, 80, dtype=np.float32)[:, None]
    data[90:] = 1.0

    hard = np.zeros((220, 2), dtype=np.float32)
    _voice(data, loop=True).render(hard, 0, PadRenderWorkspace(220))
    smooth = np.zeros_like(hard)
    _voice(data, loop=True, crossfade=10).render(smooth, 0, PadRenderWorkspace(220))

    assert abs(float(hard[100, 0] - hard[99, 0])) == 1.0
    assert abs(float(smooth[100, 0] - smooth[99, 0])) < 0.11
    # Full-loop golden corrected against a scalar overlap-add reference:
    # later wraps now blend the slice head, rather than absolute travel.
    pcm = np.rint(smooth * 32767.0).astype("<i2")
    assert hashlib.sha256(pcm.tobytes()).hexdigest() == (
        "0d816da37ff25aac4a080d2324b243a3e5df647f1bd0201393de3fd67978737a"
    )


def test_offline_sinc_suppresses_downsampling_aliases():
    frames = 4_096
    high = np.sin(2 * np.pi * 0.4 * np.arange(frames)).astype(np.float32)
    data = np.column_stack((high, high))
    live = np.zeros((1_000, 2), dtype=np.float32)
    offline = np.zeros_like(live)
    _voice(data, rate=2.0, length=1_000).render(live, 0, PadRenderWorkspace(1_000))
    _voice(data, rate=2.0, quality="offline", length=1_000).render(
        offline, 0, PadRenderWorkspace(1_000)
    )

    live_rms = float(np.sqrt(np.mean(live[32:-32, 0] ** 2)))
    offline_rms = float(np.sqrt(np.mean(offline[32:-32, 0] ** 2)))
    assert offline_rms < live_rms * 0.02


def test_streamed_blocks_match_reference_and_never_allocate_song_buses(monkeypatch):
    engine = Engine(_Library(), sample_rate=1_000, blocksize=64)
    engine.project.bpm = 120.0
    engine.project.master = 0.5
    engine.project.pattern().bars = 1
    engine.project.pattern().steps = {0: {0: 1.0, 4: 0.7}}
    pad = engine.project.pads[0]
    pad.sample_id = "sample"
    pad.end = 0.35
    pad.pitch = 3.7
    pad.mode = "loop"
    pad.loop_crossfade = 0.010

    reference = engine._render_offline_reference(mode="pattern", repeats=1, tail=0.1)
    allocated = []
    real_zeros = engine_module.np.zeros

    def traced_zeros(shape, *args, **kwargs):
        allocated.append(shape)
        return real_zeros(shape, *args, **kwargs)

    monkeypatch.setattr(engine_module.np, "zeros", traced_zeros)
    blocks = list(engine.iter_offline_blocks(mode="pattern", repeats=1, tail=0.1))
    streamed = np.concatenate(blocks)

    np.testing.assert_array_equal(streamed, reference)
    assert max(map(len, blocks)) <= engine.blocksize
    total = len(reference)
    assert (total, 2) not in allocated
    assert (NTRACKS, total, 2) not in allocated


def test_monitor_ring_and_event_lists_stay_preallocated():
    engine = Engine(_Library(), sample_rate=1_000, blocksize=16)
    ring_id = id(engine._monitor_audio)
    note_id = id(engine._note_events)
    audio_id = id(engine._audio_events)
    block = np.full((64, 2), 0.1, dtype=np.float32)
    for _ in range(20):
        engine.queue_monitor(block, 0.5)
    output = np.zeros((16, 2), dtype=np.float32)
    engine._callback(output, 16, None, None)

    assert id(engine._monitor_audio) == ring_id
    assert id(engine._note_events) == note_id
    assert id(engine._audio_events) == audio_id
    np.testing.assert_allclose(output, 0.05, rtol=0.0, atol=1e-7)


def test_crossfade_settings_round_trip_for_pads_and_clips():
    project = Project()
    project.pads[0].loop_crossfade = 0.017
    project.rows[0].clips.append(Clip(kind="audio", ref="sample", loop=True, loop_crossfade=0.023))

    restored = Project.from_dict(project.to_dict())
    assert restored.pads[0].loop_crossfade == 0.017
    assert restored.rows[0].clips[0].loop_crossfade == 0.023


def test_buffer_labels_and_benchmark_policy_are_explicit_and_deterministic():
    labels = {frames: label for label, frames in AUDIO_BUFFER_PROFILES}
    assert "EXPERIMENTAL" in labels[128]
    assert labels[256] == "PRODUCTION"
    assert "SAFE" in labels[512]
    assert verdict(0.25, 0.99) == "comfortable"
    assert verdict(0.251, 0.99) == "tight"
    assert verdict(0.501, 0.99) == "no headroom"
    assert verdict(0.1, 1.0) == "will glitch"
