"""Linux host negotiation and best-effort real-time preparation."""

from __future__ import annotations

import os
import sys
import types

from mpclab.engine import Engine
from mpclab.linux_audio import LinuxAudioRuntime


class _Library:
    pass


def test_pipewire_quantum_tracks_the_selected_engine_block(monkeypatch):
    monkeypatch.delenv("PIPEWIRE_LATENCY", raising=False)
    monkeypatch.delenv("PULSE_LATENCY_MSEC", raising=False)
    runtime = LinuxAudioRuntime()
    runtime.status.enabled = True

    runtime.configure_host(256, 48_000)

    assert os.environ["PIPEWIRE_LATENCY"] == "256/48000"
    assert os.environ["PULSE_LATENCY_MSEC"] == "5"
    assert runtime.status.quantum == "256/48000"


def test_callback_thread_requests_fifo_once(monkeypatch):
    calls = []
    runtime = LinuxAudioRuntime(priority=70)
    runtime.status.enabled = True
    monkeypatch.setattr(os, "sched_getscheduler", lambda _pid: os.SCHED_OTHER)
    monkeypatch.setattr(os, "sched_get_priority_max", lambda _policy: 99)
    monkeypatch.setattr(
        os,
        "sched_setscheduler",
        lambda pid, policy, param: calls.append((pid, policy, param.sched_priority)),
    )

    runtime.prepare_callback_thread()
    runtime.prepare_callback_thread()

    assert calls == [(0, os.SCHED_FIFO, 70)]
    assert runtime.status.scheduler == "FIFO 70"


def test_portaudio_stream_uses_low_overhead_linux_safe_flags(monkeypatch):
    opened = []

    class FakeStream:
        latency = 0.005

        def __init__(self, **kwargs):
            opened.append(kwargs)

        def start(self):
            pass

        def stop(self):
            pass

        def close(self):
            pass

    monkeypatch.setitem(sys.modules, "sounddevice", types.SimpleNamespace(OutputStream=FakeStream))
    engine = Engine(_Library(), blocksize=256)

    engine.start()
    try:
        assert opened[0]["blocksize"] == 256
        assert opened[0]["latency"] == "low"
        assert opened[0]["clip_off"] is True
        assert opened[0]["dither_off"] is True
        assert opened[0]["prime_output_buffers_using_stream_callback"] is True
        assert os.environ["PIPEWIRE_LATENCY"] == "256/48000"
    finally:
        engine.stop()
