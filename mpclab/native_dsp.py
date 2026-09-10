"""Load prebuilt DSP hot loops. Never compile or load libraries in a callback."""

import ctypes
import hashlib
import os
from pathlib import Path
import platform

import numpy as np

from .native_core import CoreBindings

SOURCE = Path(__file__).with_name("native") / "synth.cpp"
SOURCES = tuple(sorted(SOURCE.parent.glob("*.cpp")))
FLAGS = (
    "-O3",
    "-std=c++17",
    "-dynamiclib" if platform.system() == "Darwin" else "-shared",
    *(() if platform.system() == "Windows" else ("-fPIC",)),
    "-fno-fast-math",
    "-ffp-contract=off",
)


def binary_path():
    inputs = (*SOURCES, *sorted(SOURCE.parent.glob("*.hpp")))
    identity = b"".join(p.name.encode() + p.read_bytes() for p in inputs)
    identity += repr((FLAGS, platform.machine(), platform.system())).encode()
    digest = hashlib.sha256(identity).hexdigest()[:20]
    suffix = {"Windows": ".dll", "Darwin": ".dylib"}.get(platform.system(), ".so")
    return SOURCE.parent.parent.parent / ".native" / f"dsp-{digest}{suffix}"


def _array(array, size=None):
    if (
        not isinstance(array, np.ndarray)
        or array.dtype != np.float64
        or array.ndim != 1
        or not array.flags.c_contiguous
        or not array.flags.writeable
        or (size is not None and len(array) != size)
    ):
        raise ValueError("Native DSP requires contiguous writable float64 vectors of matching size")
    return array.ctypes.data


class NativeDSP:
    def __init__(self, path):
        # CDLL releases the GIL during the C routines; PyDLL would not.
        self.lib = ctypes.CDLL(str(path))
        self.lib.mpc_dsp_abi.restype = ctypes.c_int
        if self.lib.mpc_dsp_abi() != 1:
            raise ValueError("Unsupported native DSP ABI")
        ptr, count, integer, real = (
            ctypes.c_void_p,
            ctypes.c_size_t,
            ctypes.c_int64,
            ctypes.c_double,
        )
        self.lib.mpc_envelope.argtypes = [
            ptr,
            count,
            ptr,
            integer,
            integer,
            real,
            real,
            real,
            integer,
        ]
        self.lib.mpc_envelope.restype = None
        self.lib.mpc_filter.argtypes = [ptr, ptr, ptr, count, real, ptr, ptr, ptr]
        self.lib.mpc_filter.restype = None
        self.lib.mpc_synth.argtypes = [ptr, count, ptr, ptr, ptr, integer, integer]
        self.lib.mpc_synth.restype = None
        self.lib.mpc_onepole.argtypes = [ptr, count, count, ptr, real]
        self.lib.mpc_onepole.restype = None
        self.core = CoreBindings(self.lib)

    def onepole(self, block, state, b):
        if (
            block.dtype != np.float32
            or block.ndim != 2
            or not block.flags.c_contiguous
            or not block.flags.writeable
            or state.dtype != np.float32
            or state.shape != (block.shape[1],)
            or not state.flags.c_contiguous
            or not state.flags.writeable
        ):
            raise ValueError("Native smoothing needs contiguous float32 audio and channel state")
        self.lib.mpc_onepole(block.ctypes.data, len(block), block.shape[1], state.ctypes.data, b)

    def synth(self, output, noise, params, state, age, gate):
        if (
            output.dtype != np.float32
            or output.ndim != 2
            or output.shape[1] != 2
            or not output.flags.c_contiguous
            or not output.flags.writeable
        ):
            raise ValueError("Native synth output requires contiguous writable stereo float32")
        self.lib.mpc_synth(
            output.ctypes.data,
            len(output),
            _array(noise, len(output)),
            _array(params, 23),
            _array(state, 12),
            age,
            gate,
        )

    def envelope(self, out, state, age, gate, attack, decay, sustain, release_frames):
        self.lib.mpc_envelope(
            _array(out),
            len(out),
            _array(state, 4),
            age,
            gate,
            attack,
            decay,
            sustain,
            max(1, release_frames),
        )

    def filter(self, left, right, g, state, out_l, out_r, k):
        n = len(left)
        self.lib.mpc_filter(
            _array(left),
            _array(right, n),
            _array(g, n),
            n,
            k,
            _array(state, 4),
            _array(out_l, n),
            _array(out_r, n),
        )


NATIVE = None
STATUS = "Python DSP fallback; run scripts/build_native.py for production acceleration"
if os.environ.get("MPC_NATIVE_DSP") == "0":
    STATUS = "Python DSP reference (MPC_NATIVE_DSP=0)"
else:
    try:
        NATIVE = NativeDSP(binary_path())
        STATUS = "C++ sample, synth and mixer DSP · GIL released"
    except (OSError, ValueError, AttributeError) as exc:
        STATUS += f" ({type(exc).__name__})"
