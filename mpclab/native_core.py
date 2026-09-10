"""Validated control-plane binding to the portable C++ render kernels."""

from __future__ import annotations

import ctypes as ct

import numpy as np


class SampleState(ct.Structure):
    _fields_ = [
        *(
            (name, ct.c_int64)
            for name in ("start", "end", "attack", "release", "length", "age", "crossfade")
        ),
        *((name, ct.c_double) for name in ("rate", "gain", "left", "right")),
        *((name, ct.c_int32) for name in ("loop", "offline", "dead")),
    ]


def stereo(array, *, writable=False):
    if (
        not isinstance(array, np.ndarray)
        or array.dtype != np.float32
        or array.ndim != 2
        or array.shape[1] != 2
        or not array.flags.c_contiguous
        or (writable and not array.flags.writeable)
    ):
        raise ValueError("C++ audio requires contiguous stereo float32 buffers")
    return array.ctypes.data


def state_buffer(array, dtype, size):
    if (
        not isinstance(array, np.ndarray)
        or array.dtype != dtype
        or array.shape != (size,)
        or not array.flags.c_contiguous
        or not array.flags.writeable
    ):
        raise ValueError(f"Native state requires a writable {dtype} vector of size {size}")
    return array.ctypes.data


class CoreBindings:
    def __init__(self, library):
        self.lib = library
        library.anh_core_abi.argtypes = []
        library.anh_core_abi.restype = ct.c_int
        library.anh_sample_voice_size.argtypes = []
        library.anh_sample_voice_size.restype = ct.c_size_t
        if library.anh_core_abi() != 1 or library.anh_sample_voice_size() != ct.sizeof(SampleState):
            raise ValueError("Unsupported C++ engine ABI or structure layout")
        ptr, size, real = ct.c_void_p, ct.c_size_t, ct.c_double
        library.anh_sample_render.argtypes = [
            ptr,
            ct.c_int64,
            ptr,
            ct.c_int64,
            ct.POINTER(SampleState),
        ]
        library.anh_sample_render.restype = ct.c_int
        library.anh_limit.argtypes = [ptr, size, real, real, ptr]
        library.anh_limit.restype = None
        library.anh_compress.argtypes = [ptr, size, real, real, real, real, real, ptr, ptr, ptr]
        library.anh_compress.restype = None
        library.anh_saturate.argtypes = [ptr, size, real]
        library.anh_saturate.restype = None
        library.anh_mix_meter.argtypes = [ptr, ptr, size, ptr, ptr, ct.c_int, ptr]
        library.anh_mix_meter.restype = None
        library.anh_convolver_create.argtypes = [ptr, size, size, size]
        library.anh_convolver_create.restype = ptr
        library.anh_convolver_destroy.argtypes = [ptr]
        library.anh_convolver_destroy.restype = None
        library.anh_convolver_reset.argtypes = [ptr]
        library.anh_convolver_reset.restype = None
        library.anh_convolver_process.argtypes = [ptr, ptr, size]
        library.anh_convolver_process.restype = ct.c_int
        library.anh_convolver_transfer.argtypes = [ptr, ptr]
        library.anh_convolver_transfer.restype = ct.c_int
        for name in ("delay", "reverb"):
            for suffix, args, result in (
                ("create", [size], ptr),
                ("destroy", [ptr], None),
                ("reset", [ptr], None),
                ("process", [ptr, ptr, ptr, size, ptr], ct.c_int),
            ):
                function = getattr(library, f"anh_{name}_{suffix}")
                function.argtypes, function.restype = args, result

    def sample(self, voice, output):
        source = stereo(voice.data)
        destination = stereo(output, writable=True)
        state = SampleState(
            voice.s0,
            voice.s1,
            voice.attack,
            voice.release,
            voice.length,
            voice.age,
            voice.loop_crossfade,
            voice.rate,
            voice.gain,
            voice.pan_l,
            voice.pan_r,
            voice.loop,
            voice.quality == "offline",
            voice.dead,
        )
        result = self.lib.anh_sample_render(
            source, len(voice.data), destination, len(output), ct.byref(state)
        )
        if result:
            raise ValueError("Invalid C++ sample voice range or parameters")
        voice.age, voice.dead = state.age, bool(state.dead)

    def limit(self, audio, ceiling, step, state):
        self.lib.anh_limit(
            stereo(audio, writable=True),
            len(audio),
            ceiling,
            step,
            state_buffer(state, np.float64, 2),
        )

    def compress(self, audio, threshold, ratio, attack, release, makeup, slow, fast, reduction):
        self.lib.anh_compress(
            stereo(audio, writable=True),
            len(audio),
            threshold,
            ratio,
            attack,
            release,
            makeup,
            state_buffer(slow, np.float32, 1),
            state_buffer(fast, np.float32, 1),
            state_buffer(reduction, np.float64, 1),
        )

    def saturate(self, audio, drive):
        self.lib.anh_saturate(stereo(audio, writable=True), audio.size, drive)

    def mix_meter(self, source, output, left, right, meter):
        automated = isinstance(left, np.ndarray)
        if automated:
            if any(
                a.dtype != np.float64 or a.shape != (len(source),) or not a.flags.c_contiguous
                for a in (left, right)
            ):
                raise ValueError("Automation needs contiguous float64 gains matching the audio")
            lp, rp = left.ctypes.data, right.ctypes.data
        else:
            l, r = ct.c_double(left), ct.c_double(right)
            lp, rp = ct.byref(l), ct.byref(r)
        if output.shape != source.shape:
            raise ValueError("Mixer output must match source size")
        self.lib.anh_mix_meter(
            stereo(source),
            stereo(output, writable=True),
            len(source),
            lp,
            rp,
            automated,
            state_buffer(meter, np.float64, 2),
        )


class NativeConvolver:
    """Control-plane owner; allocation occurs only during explicit preparation."""

    def __init__(self, core, ir, channels, frames, previous=None):
        if not 1 <= frames <= 16384 or channels not in (1, 2):
            raise ValueError("Convolution requires 1–16384 frames and one or two channels")
        if ir.dtype != np.float32 or ir.ndim != 1 or not ir.flags.c_contiguous:
            raise ValueError("Convolution IR must be contiguous float32")
        self.lib = core.lib
        self.channels = channels
        fft_size = 1 << (len(ir) + frames - 2).bit_length()
        self.frames = min(16384, fft_size - len(ir) + 1)
        self.handle = self.lib.anh_convolver_create(ir.ctypes.data, len(ir), channels, self.frames)
        if not self.handle:
            raise ValueError("Unable to prepare bounded native convolution")
        if previous is not None and self.lib.anh_convolver_transfer(previous.handle, self.handle):
            self.close()
            raise ValueError("Convolution state transfer requires matching channels and IR size")

    def process(self, block):
        if (
            block.dtype != np.float32
            or block.ndim != 2
            or block.shape[1] != self.channels
            or not block.flags.c_contiguous
            or not block.flags.writeable
        ):
            raise ValueError("Convolution needs contiguous writable float32 audio")
        return self.lib.anh_convolver_process(self.handle, block.ctypes.data, len(block)) == 0

    def reset(self):
        self.lib.anh_convolver_reset(self.handle)

    def close(self):
        if self.handle:
            self.lib.anh_convolver_destroy(self.handle)
            self.handle = None

    def __del__(self):
        if getattr(self, "handle", None):
            self.close()


class NativeSend:
    """Fixed-capacity send with native-owned delay history."""

    def __init__(self, core, kind, frames=16384):
        self.process_fn = getattr(core.lib, f"anh_{kind}_process")
        self.reset_fn = getattr(core.lib, f"anh_{kind}_reset")
        self.destroy_fn = getattr(core.lib, f"anh_{kind}_destroy")
        self.params = np.zeros(5, dtype=np.float64)
        self.output = np.zeros((frames, 2), dtype=np.float32)
        self.handle = getattr(core.lib, f"anh_{kind}_create")(frames)
        if not self.handle:
            raise ValueError("Unable to prepare native send")

    def process(self, audio, params):
        self.params[:] = params
        if self.process_fn(
            self.handle, stereo(audio), self.output.ctypes.data, len(audio), self.params.ctypes.data
        ):
            raise ValueError("Send exceeds its prepared audio capacity")
        return self.output[: len(audio)]

    def reset(self):
        self.reset_fn(self.handle)

    def __del__(self):
        if getattr(self, "handle", None):
            self.destroy_fn(self.handle)
