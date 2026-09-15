"""Native PortAudio capture callback; Python consumes copies on its own worker.

The device callback only copies into bounded preallocated slots. Frame positions
survive queue overflow, allowing the recorder to preserve gaps in the take.
"""

from __future__ import annotations

import ctypes as ct
import threading
import time
from types import SimpleNamespace

import numpy as np


class CaptureInfo(ct.Structure):
    _fields_ = [
        ("position", ct.c_uint64),
        ("frames", ct.c_uint64),
        ("flags", ct.c_uint64),
        ("adc", ct.c_double),
    ]


class InputQueue:
    def __init__(self, library, frames, channels=1, slots=256, sample_rate=48000):
        self.lib, self.frames, self.channels = library, frames, channels
        signatures = {
            "create": ([ct.c_size_t, ct.c_size_t, ct.c_size_t, ct.c_double], ct.c_void_p),
            "destroy": ([ct.c_void_p], None),
            "read": ([ct.c_void_p, ct.c_void_p, ct.c_size_t, ct.c_void_p], ct.c_int),
            "total": ([ct.c_void_p], ct.c_uint64),
            "dropped": ([ct.c_void_p], ct.c_uint64),
            "callback": (
                [ct.c_void_p, ct.c_void_p, ct.c_ulong, ct.c_void_p, ct.c_ulong, ct.c_void_p],
                ct.c_int,
            ),
        }
        for name, (args, result) in signatures.items():
            function = getattr(library, "anh_input_" + name)
            function.argtypes, function.restype = args, result
        self.handle = library.anh_input_create(slots, frames, channels, sample_rate)
        if not self.handle:
            raise ValueError("Capture queue exceeds its size or channel limits")
        self.audio = np.zeros((frames, channels), np.float32)
        self.info = CaptureInfo()

    def read(self):
        if not self.handle:
            raise RuntimeError("Capture queue is closed")
        result = self.lib.anh_input_read(
            self.handle, self.audio.ctypes.data, self.frames, ct.byref(self.info)
        )
        if result < 0:
            raise RuntimeError("Capture block exceeds prepared storage")
        return bool(result)

    @property
    def total(self):
        return int(self.lib.anh_input_total(self.handle))

    @property
    def dropped(self):
        return int(self.lib.anh_input_dropped(self.handle))

    def close(self):
        if self.handle:
            self.lib.anh_input_destroy(self.handle)
            self.handle = None


class NativeInputStream:
    native_callback = True

    def __init__(
        self, sd, native, *, callback, samplerate, blocksize, channels, engine=None, **options
    ):
        self.queue = InputQueue(native.lib, blocksize, channels, sample_rate=samplerate)
        self.callback = callback
        self.error = ""
        self._stop = threading.Event()
        self._worker = None
        self._closed = False
        self._host = None
        self.total_frames = self.dropped_frames = 0
        self.clock_offset = None
        self._latency = 0.0
        try:
            if engine is not None:
                self._host = DuplexEndpoint(engine, self.queue, sd, native, options.get("device"))
                return
            address = ct.cast(native.lib.anh_input_callback, ct.c_void_p).value
            self._host = sd._StreamBase(
                "input",
                samplerate=samplerate,
                blocksize=blocksize,
                channels=channels,
                callback=sd._ffi.cast("PaStreamCallback*", address),
                userdata=sd._ffi.cast("void*", self.queue.handle),
                wrap_callback=None,
                **options,
            )
        except BaseException:
            self.queue.close()
            raise

    def _consume(self):
        try:
            while True:
                if self.queue.read():
                    info = self.queue.info
                    timing = SimpleNamespace(
                        inputBufferAdcTime=info.adc,
                        capture_frame=int(info.position),
                        capture_monotonic=(info.adc + self.clock_offset)
                        if info.adc >= 0 and self.clock_offset is not None
                        else None,
                    )

                    # bool(status) has the same meaning as sounddevice flags.
                    class Status:
                        input_overflow = bool(info.flags & 2)

                        def __bool__(self):
                            return self.input_overflow

                    self.callback(
                        self.queue.audio[: info.frames], int(info.frames), timing, Status()
                    )
                elif self._stop.is_set():
                    break
                else:
                    self._stop.wait(0.001)
        except BaseException as exc:
            self.error = str(exc) or type(exc).__name__

    def start(self):
        if self._closed or self._worker is not None:
            raise RuntimeError("Capture stream is closed or already started")
        try:
            self._host.start()
            self._latency = float(self._host.latency)
            if hasattr(self._host, "time"):
                self.clock_offset = time.monotonic() - float(self._host.time)
            self._worker = threading.Thread(
                target=self._consume, name="anharmonic-capture", daemon=True
            )
            self._worker.start()
        except BaseException:
            self.close()
            raise
        return self

    def stop(self):
        try:
            if self._host is not None:
                self._host.stop()
        finally:
            # Stop the host producer before draining the remaining slots.
            try:
                if self._host is not None:
                    self._host.close()
                    self._host = None
            finally:
                self._stop.set()
                if self._worker is not None:
                    self._worker.join(timeout=5)
                    if self._worker.is_alive():
                        raise RuntimeError("Capture worker is still draining; retry save")
                self.total_frames, self.dropped_frames = self.queue.total, self.queue.dropped

    def close(self):
        if self._closed:
            return
        self.stop()
        if self._host is not None:
            raise RuntimeError("Capture host did not close; retaining its buffers")
        self.queue.close()
        self._closed = True

    @property
    def latency(self):
        return float(self._host.latency) if self._host is not None else self._latency

    @property
    def active(self):
        return self._host is not None and self._host.active and not self.error


class DuplexEndpoint:
    """Temporarily own the engine's device stream for synchronous recording.

    Only the capture owner closes the duplex stream. The output-only stream is
    restored after the device callback has stopped, before freeing capture data.
    """

    def __init__(self, engine, capture_queue, sd, native, input_device):
        from .native_output import NativeOutputStream

        self.engine = engine
        self.was_running = engine.stream is not None
        self.stream = None
        engine.stop()
        try:
            self.stream = NativeOutputStream(
                sd,
                native,
                callback=engine._callback,
                samplerate=engine.sr,
                blocksize=engine.blocksize,
                channels=2,
                output_channels=engine.output_channels,
                capture_queue=capture_queue,
                device=(input_device, engine.output_device),
                dtype="float32",
                latency="low",
                clip_off=True,
                dither_off=True,
            )
            engine.stream = self.stream
            engine._native_output_active = True
        except BaseException:
            if self.was_running:
                engine.start()
            raise

    def start(self):
        self.stream.start()

    @property
    def time(self):
        return self.stream._host.time

    @property
    def latency(self):
        return float(self.stream._host.latency[0])

    @property
    def active(self):
        return self.stream is not None and self.stream.active

    def stop(self):
        if self.stream is not None:
            self.stream.close()
            if self.engine.stream is self.stream:
                self.engine.stream = None
                self.engine._native_output_active = False
            self.stream = None

    def close(self):
        self.stop()
        if self.was_running:
            self.was_running = False
            self.engine.start()
