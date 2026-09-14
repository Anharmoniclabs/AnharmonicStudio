"""Exercise the exact C callback and lifecycle without opening audio devices."""

import ctypes as ct
from types import SimpleNamespace

import cffi
import numpy as np
import pytest

from mpclab.native_dsp import NATIVE
from mpclab.native_output import NativeOutputStream, OutputQueue

pytestmark = pytest.mark.skipif(NATIVE is None, reason="Build the portable C++ engine")


def consume(queue, frames, flags=0):
    audio = np.full((frames, 2), -999, np.float32)
    result = queue.lib.anh_output_callback(
        None, audio.ctypes.data, frames, None, flags, queue.handle
    )
    assert result == 0
    return audio


def test_native_queue_wrap_backpressure_and_short_callbacks_preserve_sample_order():
    queue = OutputQueue(NATIVE.lib, 16)
    try:
        source = np.arange(24, dtype=np.float32).reshape(-1, 2)
        assert queue.write(source)
        assert not queue.write(source)
        np.testing.assert_array_equal(consume(queue, 7), source[:7])
        assert queue.write(source[:9])
        np.testing.assert_array_equal(consume(queue, 14), np.concatenate((source[7:], source[:9])))
        assert queue.available == 0
    finally:
        queue.close()


def test_native_starvation_writes_silence_and_counts_only_output_faults():
    queue = OutputQueue(NATIVE.lib, 16)
    try:
        source = np.ones((7, 2), np.float32)
        queue.write(source)
        audio = consume(queue, 16)
        np.testing.assert_array_equal(audio[:7], 1)
        np.testing.assert_array_equal(audio[7:], 0)
        assert queue.underruns == 1
        np.testing.assert_array_equal(consume(queue, 16), 0)
        assert queue.underruns == 2
        queue.write(source)
        consume(queue, 7, flags=2)  # input overflow is not an output xrun
        assert queue.underruns == 2
        queue.write(source)
        consume(queue, 7, flags=4)
        assert queue.underruns == 3
        queue.reset_stats()
        assert queue.underruns == 0
    finally:
        queue.close()
    with pytest.raises(RuntimeError, match="closed"):
        queue.write(source)


def fake_driver(*, fail_start=False, fail_stop=False):
    ffi = cffi.FFI()
    ffi.cdef(
        "typedef int PaStreamCallback(const void*, void*, unsigned long, const void*, unsigned long, void*);"
    )
    hosts = []

    class Host:
        latency = 0.01
        active = False

        def __init__(self, kind, **options):
            assert kind == "output" and options["wrap_callback"] is None
            self.options = options
            self.closed = False
            self.received = None
            hosts.append(self)

        def start(self):
            if fail_start:
                raise RuntimeError("device busy")
            self.active = True
            data = np.zeros((self.options["blocksize"], 2), np.float32)
            self.options["callback"](
                ffi.NULL,
                ffi.cast("void*", data.ctypes.data),
                len(data),
                ffi.NULL,
                0,
                self.options["userdata"],
            )
            self.received = data

        def stop(self):
            self.active = False
            if fail_stop:
                raise RuntimeError("device disconnected")

        def close(self):
            self.closed = True

    return SimpleNamespace(_ffi=ffi, _StreamBase=Host), hosts


def make_stream(driver, renderer=None):
    def render(output, frames, _time, _status):
        output[:] = 0.25

    return NativeOutputStream(
        driver,
        NATIVE,
        samplerate=48000,
        blocksize=128,
        callback=renderer or render,
        channels=2,
        dtype="float32",
    )


def test_host_receives_native_pointer_and_worker_latency_is_reported():
    driver, hosts = fake_driver()
    stream = make_stream(driver)
    try:
        host = hosts[0]
        expected = ct.cast(NATIVE.lib.anh_output_callback, ct.c_void_p).value
        assert int(driver._ffi.cast("uintptr_t", host.options["callback"])) == expected
        stream.start()
        np.testing.assert_array_equal(host.received, 0.25)
        assert stream.active
        assert stream.latency == pytest.approx(0.01 + 256 / 48000)
    finally:
        stream.close()
    stream.close()
    assert not stream._worker.is_alive()
    assert not stream.queue.handle
    assert hosts[0].closed


@pytest.mark.parametrize("failure", ["start", "render", "stop"])
def test_failure_joins_owned_worker_and_frees_buffers_after_host_close(failure):
    driver, hosts = fake_driver(fail_start=failure == "start", fail_stop=failure == "stop")

    def fail_render(*_args):
        raise RuntimeError("render failed")

    stream = make_stream(driver, fail_render if failure == "render" else None)
    if failure == "stop":
        stream.start()
        with pytest.raises(RuntimeError, match="device disconnected"):
            stream.close()
    else:
        with pytest.raises(RuntimeError, match="device busy|render failed"):
            stream.start()
    assert not stream._worker.is_alive()
    assert hosts[0].closed
    assert not stream.queue.handle


def test_production_engine_selects_native_callback_and_closes_its_worker(monkeypatch):
    import sys
    from mpclab.engine import Engine

    driver, hosts = fake_driver()
    monkeypatch.setitem(sys.modules, "sounddevice", driver)
    engine = Engine(SimpleNamespace(audio=lambda _id: None), blocksize=128)
    engine.linux_audio.status.enabled = False
    engine.start()
    stream = engine.stream
    assert isinstance(stream, NativeOutputStream)
    assert engine._native_output_active
    assert engine.latency_ms == pytest.approx((0.01 + 256 / 48000) * 1000)
    engine.stop()
    assert not stream._worker.is_alive()
    assert not engine._native_output_active
    assert hosts[0].closed
    assert engine.stream is None
