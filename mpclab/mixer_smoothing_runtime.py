"""Callback-safe smoothing for manual mixer gain and pan transitions.

Automation already arrives as sample-accurate arrays.  This layer only expands
manual scalar mixer controls into preallocated float64 ramps so the native and
NumPy mixer paths can consume them without zippering or callback-time audio
allocation.  Oversized emergency callbacks fail open to the original scalar
controls instead of allocating on the audio thread.
"""

from __future__ import annotations

import math

import numpy as np

from . import engine_mixing
from .parameter_smoothing import smoothing_frames

DEFAULT_MIXER_RAMP_MS = 5.0
_INSTALLED = False


class RealtimeMixerControlSmoother:
    """Preallocated per-track linear ramps for stereo mixer coefficients."""

    def __init__(
        self,
        sample_rate: float,
        blocksize: int,
        tracks: int,
        *,
        time_ms: float = DEFAULT_MIXER_RAMP_MS,
    ) -> None:
        if not math.isfinite(sample_rate) or sample_rate <= 0:
            raise ValueError("sample_rate must be positive and finite")
        if not math.isfinite(time_ms):
            raise ValueError("time_ms must be finite")
        self.sample_rate = float(sample_rate)
        self.time_ms = max(0.0, float(time_ms))
        self.ramp_frames = smoothing_frames(self.sample_rate, self.time_ms)
        self.blocksize = 0
        self.track_count = 0
        self._ramp = np.empty(0, dtype=np.float64)
        self.left_buffers: tuple[np.ndarray, ...] = ()
        self.right_buffers: tuple[np.ndarray, ...] = ()
        self._current_left = np.empty(0, dtype=np.float64)
        self._current_right = np.empty(0, dtype=np.float64)
        self._target_left = np.empty(0, dtype=np.float64)
        self._target_right = np.empty(0, dtype=np.float64)
        self._step_left = np.empty(0, dtype=np.float64)
        self._step_right = np.empty(0, dtype=np.float64)
        self._remaining_left = np.empty(0, dtype=np.int64)
        self._remaining_right = np.empty(0, dtype=np.int64)
        self._initialized = np.empty(0, dtype=np.bool_)
        self.prepare(blocksize, tracks)

    def prepare(self, blocksize: int, tracks: int) -> None:
        """Allocate all callback working storage outside the audio thread."""
        blocksize = int(blocksize)
        tracks = int(tracks)
        if blocksize <= 0:
            raise ValueError("blocksize must be positive")
        if tracks < 0:
            raise ValueError("tracks must be non-negative")
        self.blocksize = blocksize
        self.track_count = tracks
        self._ramp = np.arange(1, blocksize + 1, dtype=np.float64)
        self.left_buffers = tuple(np.empty(blocksize, dtype=np.float64) for _ in range(tracks))
        self.right_buffers = tuple(np.empty(blocksize, dtype=np.float64) for _ in range(tracks))
        self._current_left = np.zeros(tracks, dtype=np.float64)
        self._current_right = np.zeros(tracks, dtype=np.float64)
        self._target_left = np.zeros(tracks, dtype=np.float64)
        self._target_right = np.zeros(tracks, dtype=np.float64)
        self._step_left = np.zeros(tracks, dtype=np.float64)
        self._step_right = np.zeros(tracks, dtype=np.float64)
        self._remaining_left = np.zeros(tracks, dtype=np.int64)
        self._remaining_right = np.zeros(tracks, dtype=np.int64)
        self._initialized = np.zeros(tracks, dtype=np.bool_)

    def ensure(self, blocksize: int, tracks: int) -> None:
        """Rebuild only when the stopped engine changes layout or block size."""
        if int(blocksize) != self.blocksize or int(tracks) != self.track_count:
            self.prepare(blocksize, tracks)

    def invalidate(self) -> None:
        """Make the next observed project state the new click-free baseline."""
        self._initialized.fill(False)
        self._remaining_left.fill(0)
        self._remaining_right.fill(0)

    def snap(self, index: int, left: float, right: float) -> None:
        """Synchronize state to a sample-accurate automation endpoint."""
        if not 0 <= index < self.track_count:
            return
        left = float(left)
        right = float(right)
        self._current_left[index] = self._target_left[index] = left
        self._current_right[index] = self._target_right[index] = right
        self._step_left[index] = self._step_right[index] = 0.0
        self._remaining_left[index] = self._remaining_right[index] = 0
        self._initialized[index] = True

    @staticmethod
    def _retarget(
        index: int,
        target: float,
        current: np.ndarray,
        targets: np.ndarray,
        steps: np.ndarray,
        remaining: np.ndarray,
        frames: int,
    ) -> None:
        if target == float(targets[index]):
            return
        targets[index] = target
        if frames <= 0 or target == float(current[index]):
            current[index] = target
            steps[index] = 0.0
            remaining[index] = 0
            return
        remaining[index] = frames
        steps[index] = (target - float(current[index])) / frames

    def _fill_channel(
        self,
        out: np.ndarray,
        index: int,
        target: float,
        frames: int,
        current: np.ndarray,
        targets: np.ndarray,
        steps: np.ndarray,
        remaining: np.ndarray,
    ) -> None:
        self._retarget(index, target, current, targets, steps, remaining, self.ramp_frames)
        count = int(remaining[index])
        take = min(frames, count)
        value = float(current[index])
        step = float(steps[index])
        if take:
            np.multiply(self._ramp[:take], step, out=out[:take])
            np.add(out[:take], value, out=out[:take])
            value += step * take
            count -= take
            if count == 0:
                value = float(targets[index])
                step = 0.0
            current[index] = value
            steps[index] = step
            remaining[index] = count
        if take < frames:
            out[take:frames].fill(value)

    def render(
        self, index: int, left: float, right: float, frames: int
    ) -> tuple[float | np.ndarray, float | np.ndarray]:
        """Return scalar controls or preallocated ramps for one realtime render chunk."""
        index = int(index)
        frames = int(frames)
        left = float(left)
        right = float(right)
        if not 0 <= index < self.track_count or frames <= 0:
            return left, right
        if frames > self.blocksize:
            # PortAudio should use the configured fixed block. The engine has an
            # emergency oversized-block path; preserve it without allocating a
            # larger control buffer from inside that callback.
            self.snap(index, left, right)
            return left, right
        lbuf = self.left_buffers[index]
        rbuf = self.right_buffers[index]
        if not bool(self._initialized[index]):
            self.snap(index, left, right)
            lbuf[:frames].fill(left)
            rbuf[:frames].fill(right)
        else:
            self._fill_channel(
                lbuf,
                index,
                left,
                frames,
                self._current_left,
                self._target_left,
                self._step_left,
                self._remaining_left,
            )
            self._fill_channel(
                rbuf,
                index,
                right,
                frames,
                self._current_right,
                self._target_right,
                self._step_right,
                self._remaining_right,
            )
        if frames == self.blocksize:
            return lbuf, rbuf
        return lbuf[:frames], rbuf[:frames]


def install_mixer_smoothing_runtime() -> None:
    """Install smoothing after advanced mixer controls are already published."""
    global _INSTALLED
    if _INSTALLED:
        return

    from .engine import Engine

    previous_controls = engine_mixing.track_controls
    original_init = Engine.__init__
    original_configure_tracks = Engine.configure_tracks
    original_configure_blocksize = Engine.configure_blocksize
    original_start = Engine.start
    original_render_block = Engine._render_block

    def init(engine, *args, **kwargs):
        original_init(engine, *args, **kwargs)
        engine._mixer_control_frames = engine.blocksize
        engine._mixer_control_realtime = False
        engine._mixer_control_smoothing = RealtimeMixerControlSmoother(
            engine.sr,
            engine.blocksize,
            len(engine.project.tracks),
        )

    def configure_tracks(engine, project=None):
        result = original_configure_tracks(engine, project)
        state = getattr(engine, "_mixer_control_smoothing", None)
        if state is not None:
            project = project or engine.project
            state.ensure(engine.blocksize, len(project.tracks))
        return result

    def configure_blocksize(engine, frames):
        result = original_configure_blocksize(engine, frames)
        state = getattr(engine, "_mixer_control_smoothing", None)
        if state is not None:
            state.ensure(engine.blocksize, len(engine.project.tracks))
        return result

    def start(engine, *args, **kwargs):
        state = getattr(engine, "_mixer_control_smoothing", None)
        if state is not None:
            state.invalidate()
        return original_start(engine, *args, **kwargs)

    def render_block(engine, outdata, frames, monitor=None):
        # A callback may be split into multiple render chunks at transport/loop
        # boundaries. Scope the control-array length to the exact chunk that is
        # being mixed, not to the outer PortAudio request. Offline bounce does
        # not call this realtime boundary and therefore retains exact reference
        # output with scalar/manual controls.
        previous_realtime = getattr(engine, "_mixer_control_realtime", False)
        previous_frames = getattr(engine, "_mixer_control_frames", engine.blocksize)
        engine._mixer_control_realtime = True
        engine._mixer_control_frames = int(frames)
        try:
            return original_render_block(engine, outdata, frames, monitor)
        finally:
            engine._mixer_control_realtime = previous_realtime
            engine._mixer_control_frames = previous_frames

    def track_controls(engine, index, beats):
        left, right = previous_controls(engine, index, beats)
        state = getattr(engine, "_mixer_control_smoothing", None)
        if (
            state is None
            or not getattr(engine, "_mixer_control_realtime", False)
            or not 0 <= index < state.track_count
        ):
            return left, right

        left_array = isinstance(left, np.ndarray)
        right_array = isinstance(right, np.ndarray)
        if left_array or right_array:
            # Automation is already sample accurate. Do not smooth it again;
            # only remember the endpoint so a later manual move continues from
            # the actual automated value instead of an old manual target.
            last_left = float(left[-1]) if left_array and len(left) else float(left)
            last_right = float(right[-1]) if right_array and len(right) else float(right)
            state.snap(index, last_left, last_right)
            return left, right

        frames = int(getattr(engine, "_mixer_control_frames", engine.blocksize))
        return state.render(index, float(left), float(right), frames)

    Engine.__init__ = init
    Engine.configure_tracks = configure_tracks
    Engine.configure_blocksize = configure_blocksize
    Engine.start = start
    Engine._render_block = render_block
    engine_mixing.track_controls = track_controls
    _INSTALLED = True
