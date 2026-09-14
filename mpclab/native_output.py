"""Portable C++ device callback with a bounded render-worker handoff.

The host invokes a C function pointer directly. Python project scheduling and
isolated plugin orchestration execute on a producer thread, never the device
callback. The two-block queue is included in reported output latency. This is
an explicit migration boundary, not a claim that scheduling is already C++.
"""

from __future__ import annotations

import ctypes as ct
import threading

import numpy as np

from .native_core import stereo


class OutputQueue:
    def __init__(self, library, capacity):
        self.lib = library
        self.capacity = int(capacity)
        for name, args, result in (
            ("create", [ct.c_size_t], ct.c_void_p),
            ("destroy", [ct.c_void_p], None),
            ("write", [ct.c_void_p, ct.c_void_p, ct.c_size_t], ct.c_int),
            ("available", [ct.c_void_p], ct.c_uint64),
            ("underruns", [ct.c_void_p], ct.c_uint64),
            ("reset_stats", [ct.c_void_p], None),
        ):
            function = getattr(library, f"anh_output_{name}")
            function.argtypes, function.restype = args, result
        library.anh_output_callback.argtypes = [
            ct.c_void_p,
            ct.c_void_p,
            ct.c_ulong,
            ct.c_void_p,
            ct.c_ulong,
            ct.c_void_p,
        ]
        library.anh_output_callback.restype = ct.c_int
        self.handle = library.anh_output_create(self.capacity)
        if not self.handle:
            raise ValueError("Native output capacity must be 1–65536 frames")

    def write(self, audio):
        if not self.handle:
            raise RuntimeError("Output queue is closed")
        return self.lib.anh_output_write(self.handle, stereo(audio), len(audio)) == 0

    @property
    def available(self):
        return int(self.lib.anh_output_available(self.handle))

    @property
    def underruns(self):
        return int(self.lib.anh_output_underruns(self.handle))

    def reset_stats(self):
        self.lib.anh_output_reset_stats(self.handle)

    def close(self):
        # Owner must stop/join both callback and producer before releasing this.
        if self.handle:
            self.lib.anh_output_destroy(self.handle)
            self.handle = None


class NativeOutputStream:
    """Control-plane lifecycle for one native callback and its render producer."""

    native_callback = True

    def __init__(self, sd, native, *, callback, samplerate, blocksize, **options):
        self.frames, self.sample_rate = blocksize, samplerate
        self.render = callback
        self.queue = OutputQueue(native.lib, blocksize * 2)
        self._stop = threading.Event()
        self._ready = threading.Event()
        self._worker = None
        self._host = None
        self.render_error = ""
        self._closed = False
        self._audio = np.zeros((blocksize, 2), dtype=np.float32)
        try:
            address = ct.cast(native.lib.anh_output_callback, ct.c_void_p).value
            self._host = sd._StreamBase(
                "output",
                samplerate=samplerate,
                blocksize=blocksize,
                callback=sd._ffi.cast("PaStreamCallback*", address),
                userdata=sd._ffi.cast("void*", self.queue.handle),
                wrap_callback=None,
                **options,
            )
        except BaseException:
            self.queue.close()
            raise

    def _produce(self):
        try:
            while not self._stop.is_set():
                if self.queue.available <= self.frames:
                    self.render(self._audio, self.frames, None, False)
                    if not self.queue.write(self._audio):
                        raise RuntimeError("Native output producer violated its queue bound")
                    if self.queue.available >= self.frames * 2:
                        self._ready.set()
                else:
                    self._ready.set()
                    self._stop.wait(min(0.001, self.frames / self.sample_rate / 4))
        except BaseException as exc:
            self.render_error = str(exc) or type(exc).__name__
        finally:
            self._ready.set()

    def start(self):
        if self._closed:
            raise RuntimeError("Native stream is closed")
        if self._worker is not None:
            raise RuntimeError("Native stream is already started")
        self._worker = threading.Thread(target=self._produce, name="anharmonic-render", daemon=True)
        self._worker.start()
        try:
            if not self._ready.wait(5):
                raise RuntimeError("Audio renderer did not prepare its first buffers")
            if self.render_error:
                raise RuntimeError(self.render_error)
            self._host.start()
        except BaseException:
            self.close()
            raise
        return self

    def stop(self):
        self._stop.set()
        try:
            if self._host is not None:
                self._host.stop()
        finally:
            if self._worker is not None:
                self._worker.join(timeout=5)
                if self._worker.is_alive():
                    raise RuntimeError("Audio render worker did not stop; retaining its buffers")

    def close(self):
        if self._closed:
            return
        # PortAudio close joins its callback; producer join precedes buffer free.
        try:
            self.stop()
        finally:
            if self._host is not None:
                self._host.close()
                self._host = None
            if self._worker is None or not self._worker.is_alive():
                self.queue.close()
                self._closed = True

    @property
    def latency(self):
        return (
            float(self._host.latency) + self.frames * 2 / self.sample_rate
            if self._host is not None
            else 0.0
        )

    @property
    def underruns(self):
        return self.queue.underruns

    def reset_timing(self):
        self.queue.reset_stats()

    @property
    def active(self):
        return self._host is not None and self._host.active and not self.render_error
