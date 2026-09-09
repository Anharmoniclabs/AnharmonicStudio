"""Linux-specific preparation for the small real-time audio working set.

The DSP stays platform-neutral. This module only negotiates the Linux host
boundary: PipeWire quantum, PortAudio's callback thread scheduler, and page
locking for the handful of arrays touched on every callback.
"""

from __future__ import annotations

import ctypes
import os
import sys
from dataclasses import dataclass

import numpy as np


@dataclass
class LinuxAudioStatus:
    enabled: bool = sys.platform.startswith("linux")
    quantum: str = ""
    scheduler: str = "not started"
    locked_bytes: int = 0
    lock_error: str = ""

    def summary(self) -> str:
        if not self.enabled:
            return "platform audio"
        parts = [f"PipeWire request {self.quantum}" if self.quantum else "Linux audio"]
        parts.append(self.scheduler)
        if self.locked_bytes:
            parts.append(f"{self.locked_bytes / 1024:.0f} KiB locked")
        elif self.lock_error:
            parts.append(f"mlock {self.lock_error}")
        return " · ".join(parts)


class LinuxAudioRuntime:
    """Best-effort RT setup that always degrades to ordinary audio safely."""

    def __init__(self, priority: int = 70):
        self.priority = int(priority)
        self.status = LinuxAudioStatus()
        self._thread_prepared = False
        self._locked: dict[tuple[int, int], np.ndarray] = {}

    def configure_host(self, blocksize: int, sample_rate: int) -> None:
        if not self.status.enabled:
            return
        blocksize = int(blocksize)
        sample_rate = int(sample_rate)
        # pipewire-alsa reads this while opening the PCM client. Matching the
        # graph quantum can avoid an adapter buffer. This is a request, not
        # confirmation of the negotiated graph quantum (inspect pw-top).
        self.status.quantum = f"{blocksize}/{sample_rate}"
        os.environ["PIPEWIRE_LATENCY"] = self.status.quantum
        # Used only when PortAudio reaches the server through pipewire-pulse.
        period_ms = max(1, round(blocksize / sample_rate * 1000.0))
        os.environ["PULSE_LATENCY_MSEC"] = str(period_ms)

    def __del__(self):
        try:
            self.reset_locks()
        except (AttributeError, OSError):
            pass

    def begin_stream(self) -> None:
        self._thread_prepared = False
        self.status.scheduler = "RT pending"

    def reset_locks(self) -> None:
        """Unlock old mappings while retained arrays still own their memory."""
        if self._locked:
            libc = ctypes.CDLL(None)
            for address, size in self._locked:
                libc.munlock(ctypes.c_void_p(address), ctypes.c_size_t(size))
        self._locked.clear()
        self.status.locked_bytes = 0
        self.status.lock_error = ""

    def prepare_callback_thread(self) -> None:
        """Run once from the actual PortAudio callback thread."""
        if not self.status.enabled or self._thread_prepared:
            return
        self._thread_prepared = True
        try:
            policy = os.sched_getscheduler(0)
            if policy in (os.SCHED_FIFO, os.SCHED_RR):
                priority = os.sched_getparam(0).sched_priority
                name = "FIFO" if policy == os.SCHED_FIFO else "RR"
                self.status.scheduler = f"{name} {priority}"
                return
            maximum = os.sched_get_priority_max(os.SCHED_FIFO)
            priority = max(1, min(self.priority, maximum))
            os.sched_setscheduler(0, os.SCHED_FIFO, os.sched_param(priority))
            self.status.scheduler = f"FIFO {priority}"
        except (AttributeError, OSError, PermissionError) as exc:
            errno = getattr(exc, "errno", None)
            self.status.scheduler = f"RT unavailable{f' ({errno})' if errno else ''}"

    def lock_arrays(self, *arrays: np.ndarray) -> None:
        """Pin only callback scratch, never the potentially huge sample cache."""
        if not self.status.enabled:
            return
        try:
            libc = ctypes.CDLL(None, use_errno=True)
            mlock = libc.mlock
            mlock.argtypes = (ctypes.c_void_p, ctypes.c_size_t)
            mlock.restype = ctypes.c_int
        except (AttributeError, OSError) as exc:
            self.status.lock_error = str(exc) or type(exc).__name__
            return
        for array in arrays:
            if not isinstance(array, np.ndarray) or not array.flags.c_contiguous:
                continue
            key = (int(array.ctypes.data), int(array.nbytes))
            if not key[1] or key in self._locked:
                continue
            if mlock(ctypes.c_void_p(key[0]), ctypes.c_size_t(key[1])) == 0:
                self._locked[key] = array
                self.status.locked_bytes += key[1]
            else:
                error = ctypes.get_errno()
                self.status.lock_error = os.strerror(error) if error else "failed"
