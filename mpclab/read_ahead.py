"""Managed read-ahead for file-backed decoded audio.

Large clips remain NumPy memmaps. This service moves predictable page faults to
a background worker by touching upcoming regions before the audio callback needs
them. It never reads source codecs or opens files on the callback thread.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
import threading
import time

import numpy as np


@dataclass(frozen=True, slots=True)
class ReadAheadStats:
    registered: int
    requests: int
    completed: int
    dropped: int
    pending: int
    pressure: float
    warmed_frames: int
    running: bool


class ReadAheadManager:
    def __init__(self, read_ahead_frames: int = 262_144, capacity: int = 1024):
        self.read_ahead_frames = max(4096, int(read_ahead_frames))
        self.capacity = max(16, int(capacity))
        self._arrays: dict[str, np.ndarray] = {}
        self._requests = deque(maxlen=self.capacity)
        self._lock = threading.RLock()
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._thread = None
        self._enabled = False
        self.requests = 0
        self.completed = 0
        self.dropped = 0
        self.warmed_frames = 0

    @property
    def running(self) -> bool:
        return bool(self._enabled and self._thread is not None and self._thread.is_alive())

    def start(self) -> None:
        self._enabled = True
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="Audio read-ahead", daemon=True)
        self._thread.start()

    def pause(self) -> None:
        """Stop page warming while retaining registrations for a later resume."""
        self._enabled = False
        self._stop.set()
        self._wake.set()
        thread, self._thread = self._thread, None
        if thread is not None and thread.is_alive():
            thread.join(timeout=1.0)
        with self._lock:
            self._requests.clear()
        self._stop.clear()

    def close(self) -> None:
        self.pause()
        with self._lock:
            self._arrays.clear()

    def register(self, clip_id: str, audio: np.ndarray) -> None:
        if not isinstance(clip_id, str) or not clip_id:
            raise ValueError("read-ahead clip id must be non-empty")
        if not isinstance(audio, np.ndarray) or audio.ndim != 2:
            raise ValueError("read-ahead audio must be a frame-major array")
        with self._lock:
            self._arrays[clip_id] = audio

    def unregister(self, clip_id: str) -> None:
        with self._lock:
            self._arrays.pop(clip_id, None)

    def request(
        self,
        clip_id: str,
        frame: int,
        *,
        reverse: bool = False,
        frames: int | None = None,
    ) -> bool:
        """Queue a bounded page-warming request without opening any file."""
        if not self._enabled or clip_id not in self._arrays:
            return False
        frame = max(0, int(frame))
        count = self.read_ahead_frames if frames is None else max(1, int(frames))
        if len(self._requests) >= self.capacity:
            self.dropped += 1
            return False
        self._requests.append((clip_id, frame, bool(reverse), count))
        self.requests += 1
        self._wake.set()
        return True

    def _pop_request(self):
        try:
            return self._requests.popleft()
        except IndexError:
            return None

    @staticmethod
    def _warm(audio: np.ndarray, frame: int, reverse: bool, count: int) -> int:
        total = len(audio)
        if total <= 0:
            return 0
        if reverse:
            end = min(total, max(0, frame) + 1)
            start = max(0, end - count)
        else:
            start = min(total, max(0, frame))
            end = min(total, start + count)
        if end <= start:
            return 0
        stride = max(1, 4096 // max(1, audio.dtype.itemsize * audio.shape[1]))
        view = audio[start:end:stride]
        if len(view):
            float(np.sum(view[:, 0], dtype=np.float64))
            if audio.shape[1] > 1:
                float(np.sum(view[:, -1], dtype=np.float64))
            _ = audio[end - 1, 0]
        return end - start

    def _run(self) -> None:
        while not self._stop.is_set():
            request = self._pop_request()
            if request is None:
                self._wake.wait(0.1)
                self._wake.clear()
                continue
            clip_id, frame, reverse, count = request
            with self._lock:
                audio = self._arrays.get(clip_id)
            if audio is None:
                continue
            try:
                warmed = self._warm(audio, frame, reverse, count)
            except (OSError, ValueError, IndexError):
                warmed = 0
            self.warmed_frames += warmed
            self.completed += 1

    @property
    def pressure(self) -> float:
        return min(1.0, len(self._requests) / self.capacity)

    def stats(self) -> ReadAheadStats:
        return ReadAheadStats(
            registered=len(self._arrays),
            requests=self.requests,
            completed=self.completed,
            dropped=self.dropped,
            pending=len(self._requests),
            pressure=self.pressure,
            warmed_frames=self.warmed_frames,
            running=self.running,
        )

    def wait_idle(self, timeout: float = 2.0) -> bool:
        deadline = time.monotonic() + max(0.0, timeout)
        while self._requests and time.monotonic() < deadline:
            time.sleep(0.002)
        return not self._requests
