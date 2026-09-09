"""Opt-in checks that require physical PortAudio/PipeWire hardware.

Run with ``MPC_HARDWARE_TEST=1 uv run --no-sync pytest -q tests/hardware``.
These tests are skipped in CI because enumeration is not evidence that a real
USB interface, cable loopback, or hot-unplug path works on the release machine.
"""

from __future__ import annotations

import os
import time

import numpy as np
import pytest

pytestmark = pytest.mark.skipif(
    os.environ.get("MPC_HARDWARE_TEST") != "1",
    reason="set MPC_HARDWARE_TEST=1 on the physical audio workstation",
)


def _sounddevice():
    return pytest.importorskip("sounddevice")


def test_portaudio_exposes_input_and_output_devices():
    sd = _sounddevice()
    devices = list(sd.query_devices())
    assert any(int(device.get("max_input_channels", 0)) > 0 for device in devices)
    assert any(int(device.get("max_output_channels", 0)) > 0 for device in devices)


def test_default_duplex_stream_opens_twice_without_stale_device_state():
    sd = _sounddevice()
    callbacks = []
    errors = []

    def callback(indata, outdata, _frames, _time_info, status):
        # PortAudio prints callback exceptions rather than raising them in the
        # test thread. Collect observations and assert after closing the stream.
        callbacks.append(len(indata))
        if getattr(status, "input_overflow", False) or getattr(status, "output_underflow", False):
            errors.append(str(status))
        outdata.fill(0)
        if len(indata):
            outdata[:, 0] = indata[:, 0] * np.float32(0.0)

    for _attempt in range(2):
        callbacks.clear()
        errors.clear()
        with sd.Stream(
            samplerate=48_000,
            blocksize=512,
            channels=(1, 2),
            dtype="float32",
            latency="low",
            callback=callback,
        ) as stream:
            time.sleep(0.25)
            assert stream.active, "duplex callback stopped unexpectedly"
        assert callbacks, "duplex stream opened without delivering audio callbacks"
        assert not errors, errors


def test_each_available_output_can_open_a_short_silent_stream():
    sd = _sounddevice()
    outputs = [
        index
        for index, device in enumerate(sd.query_devices())
        if int(device.get("max_output_channels", 0)) >= 2
    ]
    assert outputs
    failures = []
    for index in outputs:
        callbacks = []
        errors = []

        def silent_callback(
            outdata, _frames, _time_info, status, callbacks=callbacks, errors=errors
        ):
            callbacks.append(len(outdata))
            if getattr(status, "output_underflow", False):
                errors.append(str(status))
            outdata.fill(0)

        try:
            with sd.OutputStream(
                device=index,
                samplerate=48_000,
                blocksize=512,
                channels=2,
                dtype="float32",
                callback=silent_callback,
            ) as stream:
                time.sleep(0.1)
                assert stream.active, "output callback stopped unexpectedly"
            assert callbacks, "output stream opened without delivering audio callbacks"
            assert not errors, errors
        except Exception as exc:  # report every unusable advertised destination
            failures.append(f"{index}: {exc}")
    assert not failures, failures
