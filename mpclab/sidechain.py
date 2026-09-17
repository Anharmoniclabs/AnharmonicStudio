"""Audio sidechain capture and nonblocking live plugin bridge."""

from __future__ import annotations

from collections import defaultdict
import queue
import threading

import numpy as np


class SidechainRouter:
    """Preallocated per-target auxiliary audio captured before track inserts."""

    def __init__(self, project, blocksize: int):
        self.blocksize = max(1, int(blocksize))
        self.routes = ()
        self._buffers: dict[str, np.ndarray] = {}
        self._target_routes: dict[str, tuple[dict, ...]] = {}
        self.configure(project, blocksize)

    def configure(self, project, blocksize: int | None = None) -> None:
        if blocksize is not None:
            self.blocksize = max(1, int(blocksize))
        state = getattr(project, "daw_expansion", {})
        routes = state.get("sidechains", []) if isinstance(state, dict) else []
        track_ids = {track.id: index for index, track in enumerate(project.tracks)}
        compiled = []
        buffers = {}
        targets = defaultdict(list)
        for raw in routes:
            if not raw.get("enabled", True):
                continue
            source = raw.get("source", "")
            if not source.startswith("track:"):
                # Bus/master captures require a later graph-stage tap. Refuse to
                # silently substitute previous-block RMS/control information.
                continue
            track_id = source.split(":", 1)[1]
            source_index = track_ids.get(track_id)
            if source_index is None:
                continue
            route = {
                **raw,
                "source_index": source_index,
                "target": raw["target"],
                "slot": int(raw.get("slot", 0)),
            }
            compiled.append(route)
            buffers[raw["id"]] = np.zeros((self.blocksize, 2), dtype=np.float32)
            targets[raw["target"]].append(route)
        self.routes = tuple(compiled)
        self._buffers = buffers
        self._target_routes = {key: tuple(value) for key, value in targets.items()}

    def capture_tracks(self, project, tracks: np.ndarray, frames: int) -> None:
        del project
        frames = int(frames)
        if frames > self.blocksize:
            raise RuntimeError("sidechain block exceeds prepared size")
        for route in self.routes:
            destination = self._buffers[route["id"]][:frames]
            source = tracks[route["source_index"], :frames]
            gain = np.float32(route.get("gain", 1.0))
            if gain == 1.0:
                np.copyto(destination, source)
            else:
                np.multiply(source, gain, out=destination)

    def for_target(self, target: str, frames: int) -> dict[int, np.ndarray]:
        routes = self._target_routes.get(target, ())
        if not routes:
            return {}
        result = {}
        for route in routes:
            slot = route["slot"]
            source = self._buffers[route["id"]][:frames]
            existing = result.get(slot)
            if existing is None:
                result[slot] = source
            else:
                # Multiple sends to one plugin slot sum into the first route's
                # prepared buffer. This mutation is safe after capture and the
                # buffer is overwritten on the next block.
                np.add(existing, source, out=existing)
        return result

    def pressure(self) -> float:
        return 0.0


class SidechainLivePlugin:
    """Two-block isolated bridge carrying real auxiliary audio per plugin slot."""

    def __init__(self, plugin, blocksize: int):
        self.plugin = plugin
        self.blocksize = int(blocksize)
        self.info = plugin.info
        self.requests: queue.Queue = queue.Queue(maxsize=4)
        self.results: queue.Queue = queue.Queue(maxsize=4)
        self.error = ""
        self.misses = 0
        self._consecutive_misses = 0
        self._position = 0
        self._pending = {}
        self.scope = np.zeros((512, 2), dtype=np.float32)
        self._stop = threading.Event()
        self._thread = threading.Thread(
            target=self._run, name="Sidechain plugin audio bridge", daemon=True
        )
        self._thread.start()

    def _run(self):
        try:
            while not self._stop.is_set():
                try:
                    sequence, audio, frames, reset, sidechains = self.requests.get(timeout=0.1)
                except queue.Empty:
                    continue
                output = self.plugin.render(
                    audio,
                    frames,
                    reset=reset,
                    sidechains=sidechains,
                )
                self.scope = output[-512:].copy()
                try:
                    self.results.put_nowait((sequence, output))
                except queue.Full:
                    self.error = "Plugin output queue overflowed; reload the chain"
                    break
        except Exception as exc:
            self.error = str(exc) or "Sidechain plugin processing failed"
        finally:
            self.plugin.close()

    def render(self, audio, frames, *, reset=False, sidechains=None):
        if self.error:
            return None
        if not 0 < frames <= self.blocksize:
            self.error = "Plugin buffer changed; reload the chain"
            return None
        sequence = self._position
        self._position += frames
        copied = {
            int(slot): np.array(block, dtype=np.float32, copy=True, order="C")
            for slot, block in (sidechains or {}).items()
        }
        try:
            self.requests.put_nowait(
                (sequence, np.array(audio, dtype=np.float32, copy=True, order="C"), frames, reset, copied)
            )
        except queue.Full:
            self.error = "Plugin could not keep up; reload it or increase the audio buffer"
            return None
        while True:
            try:
                number, result = self.results.get_nowait()
                self._pending[number] = result
            except queue.Empty:
                break
        wanted = sequence - 2 * self.blocksize
        end = wanted + frames
        result = np.zeros((frames, 2), dtype=np.float32)
        covered = 0
        for number, chunk in list(self._pending.items()):
            first, last = max(number, wanted), min(number + len(chunk), end)
            if last > first:
                result[first - wanted : last - wanted] = chunk[first - number : last - number]
                covered += last - first
            if number + len(chunk) <= end:
                del self._pending[number]
        if covered < max(0, end - max(0, wanted)):
            self.misses += 1
            self._consecutive_misses += 1
            if self._consecutive_misses >= 8:
                self.error = "Plugin repeatedly missed its audio deadline; increase the buffer"
                return None
        else:
            self._consecutive_misses = 0
        return result

    def close(self):
        self._stop.set()
        self._thread.join(timeout=0.2)
        if self._thread.is_alive():
            self.plugin.close()
            self._thread.join(timeout=1.0)
