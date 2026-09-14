"""Rubber Band R3 streaming pitch shift with aligned output and stereo linking.

The native library is GPL-2.0-or-later (Particular Programs Ltd.). This GPL-3
application loads the system library, or the copy shipped in its bundle.
"""

from __future__ import annotations

import ctypes as C
from ctypes.util import find_library
from pathlib import Path
import sys
import threading

import numpy as np

_FLOAT = C.POINTER(C.c_float)
_PLANES = C.POINTER(_FLOAT)


def library():
    roots = [
        Path(getattr(sys, "_MEIPASS", Path(__file__).parents[2])),
        Path(__file__).parents[2] / ".native",
    ]
    candidates = [
        root / name
        for root in roots
        for name in ("librubberband.so.3", "librubberband.dylib", "rubberband.dll")
    ]
    name = next((str(p) for p in candidates if p.is_file()), None) or find_library("rubberband")
    if not name:
        raise RuntimeError(
            "Autotune V2 requires Rubber Band 3 or later. Install the native library."
        )
    lib = C.CDLL(name)
    signatures = {
        "new": ([C.c_uint, C.c_uint, C.c_int, C.c_double, C.c_double], C.c_void_p),
        "delete": ([C.c_void_p], None),
        "set_pitch_scale": ([C.c_void_p, C.c_double], None),
        "set_formant_scale": ([C.c_void_p, C.c_double], None),
        "set_max_process_size": ([C.c_void_p, C.c_uint], None),
        "get_preferred_start_pad": ([C.c_void_p], C.c_uint),
        "get_start_delay": ([C.c_void_p], C.c_uint),
        "available": ([C.c_void_p], C.c_int),
        "process": ([C.c_void_p, _PLANES, C.c_uint, C.c_int], None),
        "retrieve": ([C.c_void_p, _PLANES, C.c_uint], C.c_uint),
    }
    for name, (args, result) in signatures.items():
        fn = getattr(lib, "rubberband_" + name)
        fn.argtypes, fn.restype = args, result
    return lib


def _planes(data):
    return (_FLOAT * len(data))(*(row.ctypes.data_as(_FLOAT) for row in data))


class Shifter:
    """Single-owner native state; never shared with the audio callback."""

    def __init__(self, sr=48000, channels=2, block=512, initial_ratio=1.0):
        if (
            type(sr) is not int
            or not 8000 <= sr <= 192000
            or type(channels) is not int
            or channels not in (1, 2)
        ):
            raise ValueError("invalid pitch engine audio specification")
        if (
            type(block) is not int
            or not 16 <= block <= 16384
            or not np.isfinite(initial_ratio)
            or not 0.25 <= initial_ratio <= 4
        ):
            raise ValueError("invalid pitch engine block or ratio")
        self._lock = threading.Lock()
        self._final = False
        self.lib = library()
        self.channels, self.block = channels, block
        # Real-time variable ratio, R3, short window, no internal worker,
        # phase-linked channels, high-consistency pitch, preserved formants.
        options = 0x00000001 | 0x20000000 | 0x00100000 | 0x00010000
        options |= 0x10000000 | 0x04000000 | 0x01000000
        self.state = self.lib.rubberband_new(sr, channels, options, 1.0, float(initial_ratio))
        if not self.state:
            raise RuntimeError("Could not allocate Autotune V2 shifter")
        self.lib.rubberband_set_max_process_size(self.state, block)
        self.pad = self.lib.rubberband_get_preferred_start_pad(self.state)
        self.delay = self.lib.rubberband_get_start_delay(self.state)

    def close(self):
        with self._lock:
            if self.state:
                self.lib.rubberband_delete(self.state)
                self.state = None

    def push(self, data, ratio=1.0, preservation=1.0, final=False):
        if not np.isfinite(ratio) or not 0.25 <= ratio <= 4 or not 0 <= preservation <= 1:
            raise ValueError("invalid pitch or formant ratio")
        data = np.asarray(data, dtype=np.float32)
        if (
            data.ndim != 2
            or data.shape[1] != self.channels
            or not 0 < len(data) <= self.block
            or not np.isfinite(data).all()
        ):
            raise ValueError("invalid native pitch block")
        data = np.ascontiguousarray(data.T)
        # Return detached blocks, not a generator holding a native pointer
        # across yields. A close between yields previously called C with NULL.
        with self._lock:
            if not self.state or self._final:
                raise RuntimeError("pitch engine is closed or finalized")
            self.lib.rubberband_set_pitch_scale(self.state, float(ratio))
            self.lib.rubberband_set_formant_scale(self.state, float(ratio ** (-preservation)))
            self.lib.rubberband_process(self.state, _planes(data), data.shape[1], int(final))
            self._final = bool(final)
            chunks = []
            while (count := self.lib.rubberband_available(self.state)) > 0:
                out = np.empty((self.channels, min(count, self.block)), dtype=np.float32)
                got = self.lib.rubberband_retrieve(self.state, _planes(out), out.shape[1])
                if not got:
                    break
                chunks.append(out[:, :got].T.copy())
            return chunks


def shift(audio, ratios, times, sr, preservation, progress=None, cancelled=None):
    from ..vocal import _check_cancel

    audio = np.asarray(audio, dtype=np.float32)
    ratios, times = np.asarray(ratios, dtype=np.float64), np.asarray(times, dtype=np.float64)
    if (
        audio.ndim != 2
        or audio.shape[1] not in (1, 2)
        or not len(audio)
        or not np.isfinite(audio).all()
    ):
        raise ValueError("invalid pitch source")
    if (
        ratios.ndim != 1
        or times.ndim != 1
        or not len(ratios)
        or len(ratios) != len(times)
        or not np.isfinite(times).all()
        or not np.all(np.diff(times) > 0)
        or not np.isfinite(ratios).all()
        or not np.all((ratios >= 0.25) & (ratios <= 4))
    ):
        raise ValueError("invalid pitch control curve")
    shifter = Shifter(sr, audio.shape[1], initial_ratio=float(ratios[0]))
    result = np.zeros_like(audio)
    written, discard = 0, shifter.delay

    def collect(chunks):
        nonlocal written, discard
        for chunk in chunks:
            skip = min(discard, len(chunk))
            discard -= skip
            chunk = chunk[skip:]
            count = min(len(chunk), len(result) - written)
            result[written : written + count] = chunk[:count]
            written += count

    try:
        for start in range(0, shifter.pad, shifter.block):
            _check_cancel(cancelled)
            collect(
                shifter.push(
                    np.zeros((min(shifter.block, shifter.pad - start), audio.shape[1]), np.float32),
                    float(ratios[0]),
                    preservation,
                )
            )
        # Ratio is applied before native analysis. Source and control both use
        # input-frame time; start padding and output delay are compensated once.
        for start in range(0, len(audio), shifter.block):
            _check_cancel(cancelled)
            ratio = float(np.interp(start / sr, times, ratios))
            collect(shifter.push(audio[start : start + shifter.block], ratio, preservation))
            if progress:
                progress(start / max(1, len(audio)))
        # Explicit end padding ensures all delayed source samples are emitted.
        for start in range(0, shifter.delay + shifter.pad + shifter.block, shifter.block):
            _check_cancel(cancelled)
            collect(
                shifter.push(
                    np.zeros((shifter.block, audio.shape[1]), np.float32),
                    float(ratios[-1]),
                    preservation,
                    final=start + shifter.block >= shifter.delay + shifter.pad + shifter.block,
                )
            )
        if written != len(result):
            raise RuntimeError(f"Pitch engine returned {written} of {len(result)} frames")
        return result
    finally:
        shifter.close()
