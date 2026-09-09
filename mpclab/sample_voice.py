"""Shared sample playback and reusable render scratch for every platform.

This module owns interpolation, loop crossfades and sample envelopes. It has no
engine, device, plugin-host or operating-system dependency.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np


class PadRenderWorkspace:
    """Reusable vector scratch for allocation-free pad rendering per block."""

    def __init__(self, frames: int = 0):
        self.capacity = 0
        if frames:
            self.prepare(frames)

    def prepare(self, frames: int) -> None:
        frames = max(1, int(frames))
        if frames <= self.capacity:
            return
        self.capacity = frames
        self.base = np.arange(frames, dtype=np.float64)
        self.t = np.empty(frames, dtype=np.float64)
        self.idx = np.empty(frames, dtype=np.float64)
        self.sample_pos = np.empty(frames, dtype=np.float64)
        self.blend_pos = np.empty(frames, dtype=np.float64)
        self.loop_phase = np.empty(frames, dtype=np.float64)
        self.loop_alpha = np.empty(frames, dtype=np.float32)
        self.first_cycle = np.empty(frames, dtype=np.bool_)
        self.i0 = np.empty(frames, dtype=np.int32)
        self.i1 = np.empty(frames, dtype=np.int32)
        self.frac = np.empty(frames, dtype=np.float32)
        self.env = np.empty(frames, dtype=np.float32)
        self.release_env = np.empty(frames, dtype=np.float32)
        self.scale = np.empty(frames, dtype=np.float32)
        self.tmp = np.empty(frames, dtype=np.float32)
        self.seg = np.empty((frames, 2), dtype=np.float32)
        self.other = np.empty((frames, 2), dtype=np.float32)
        self.loop_seg = np.empty((frames, 2), dtype=np.float32)
        self.loop_other = np.empty((frames, 2), dtype=np.float32)


@dataclass(eq=False)
class PadVoice:
    data: np.ndarray  # (frames, 2) float32 source
    source_id: str | None  # library sample; shared by slices across banks
    s0: int  # first source frame
    s1: int  # last source frame (exclusive)
    rate: float
    gain: float
    pan_l: float
    pan_r: float
    attack: int  # frames
    release: int  # frames
    length: int  # total output frames (including release tail)
    track: int
    choke: int
    pad_index: int
    loop: bool
    live_trigger: bool = False  # manual MPC performance, separate from sequenced pad voices
    sequence_id: str | None = None  # pattern audition or individual arrangement placement
    note: int | None = None  # chromatic performance, distinct from drum-pad triggers
    gated: bool = False  # remember release behavior even after the source is replaced
    loop_crossfade: int = 0  # source frames
    quality: str = "live"  # offline selects windowed-sinc interpolation
    age: int = 0
    start_offset: int = 0  # frames to wait inside the first block
    dead: bool = False

    def release_now(self, fade: int, delay: int = 0) -> None:
        self.length = min(self.length, self.age + max(0, delay) + fade)
        self.release = max(1, fade)

    def render(
        self, dest: np.ndarray, offset: int, workspace: PadRenderWorkspace | None = None
    ) -> None:
        """Add this voice into dest[offset:], advancing its age."""
        n = len(dest) - offset
        if n <= 0 or self.dead:
            return
        span = self.s1 - self.s0
        source_length = max(1, int(np.ceil(span / max(self.rate, 1e-12))))
        effective_length = self.length if self.loop else min(self.length, source_length)
        n = min(n, effective_length - self.age)
        if n <= 0:
            self.dead = True
            return

        if workspace is None:
            workspace = PadRenderWorkspace(n)
        else:
            workspace.prepare(n)
        t = workspace.t[:n]
        np.add(workspace.base[:n], self.age, out=t)

        if self.rate == 1.0 and not self.loop:
            a = self.s0 + self.age
            b = min(self.s1, a + n)
            n = max(0, b - a)
            if n <= 0:
                self.dead = True
                return
            seg = self.data[a:b]
            t = t[:n]
        else:
            if span < 1:
                self.dead = True
                return
            travel = workspace.idx[:n]
            np.multiply(t, self.rate, out=travel)
            fade = min(max(0, int(self.loop_crossfade)), max(0, span // 2 - 1))
            blend = alpha = None
            if self.loop and fade > 0 and span > fade * 2:
                # Preserve the requested start on the first pass. Its tail is
                # blended with the head; subsequent cycles resume just after
                # that consumed head and repeat the same overlap. The boundary
                # is continuous without doing any work outside fixed scratch.
                cycle = float(span - fade)
                phase = workspace.loop_phase[:n]
                np.subtract(travel, float(span), out=phase)
                np.remainder(phase, cycle, out=phase)
                idx = workspace.sample_pos[:n]
                np.add(phase, float(fade + self.s0), out=idx)
                blend = workspace.blend_pos[:n]
                np.subtract(phase, float(span - 2 * fade), out=blend)
                alpha = workspace.loop_alpha[:n]
                np.divide(blend, float(fade), out=alpha, casting="unsafe")
                np.clip(alpha, 0.0, 1.0, out=alpha)

                first = workspace.first_cycle[:n]
                np.less(travel, float(span), out=first)
                np.add(travel, float(self.s0), out=phase)
                np.copyto(idx, phase, where=first)
                np.subtract(travel, float(span - fade), out=phase)
                # Only the first pass uses absolute travel for its head.
                # Later cycles must keep the cycle-relative head computed
                # above, or the blend drifts and jumps on every later wrap.
                np.copyto(blend, phase, where=first)
                np.add(blend, float(self.s0), out=blend)
                np.divide(phase, float(fade), out=phase)
                np.clip(phase, 0.0, 1.0, out=phase)
                np.copyto(alpha, phase, where=first, casting="unsafe")
                np.subtract(blend, self.s0, out=phase)
                np.remainder(phase, float(span), out=blend)
                np.add(blend, self.s0, out=blend)
            else:
                idx = workspace.sample_pos[:n]
                if self.loop:
                    np.remainder(travel, float(span), out=idx)
                    np.add(idx, self.s0, out=idx)
                else:
                    np.add(travel, self.s0, out=idx)

            if self.quality == "offline" and self.rate != 1.0:
                seg = self._sinc_samples(idx, span, self.loop)
            else:
                seg = workspace.seg[:n]
                self._linear_samples(idx, seg, workspace.other[:n], workspace)

            if blend is not None and alpha is not None:
                if self.quality == "offline" and self.rate != 1.0:
                    loop_seg = self._sinc_samples(blend, span, True)
                else:
                    loop_seg = workspace.loop_seg[:n]
                    self._linear_samples(blend, loop_seg, workspace.loop_other[:n], workspace)
                np.subtract(1.0, alpha, out=workspace.frac[:n])
                np.multiply(seg, workspace.frac[:n, None], out=seg)
                np.multiply(loop_seg, alpha[:, None], out=loop_seg)
                np.add(seg, loop_seg, out=seg)

        target = dest[offset : offset + n]
        tmp = workspace.tmp[:n]
        # Almost every block of a sustained sample sits wholly between its
        # attack and release ramps.  Avoid rebuilding a unity envelope for
        # every voice on every such callback; the edge blocks retain the exact
        # sample-by-sample envelope below.
        sustain = self.age >= self.attack and self.age + n <= effective_length - self.release
        if sustain:
            left = self.gain * self.pan_l
            right = self.gain * self.pan_r
        else:
            env = workspace.env[:n]
            np.divide(t, self.attack, out=env, casting="unsafe")
            np.clip(env, 0.0, 1.0, out=env)
            release_env = workspace.release_env[:n]
            idx = workspace.idx[:n]
            np.subtract(effective_length, t, out=idx)
            np.divide(idx, self.release, out=release_env, casting="unsafe")
            np.clip(release_env, 0.0, 1.0, out=release_env)
            np.minimum(env, release_env, out=env)
            left = workspace.scale[:n]
            right = release_env
            np.multiply(env, self.gain * self.pan_l, out=left)
            np.multiply(env, self.gain * self.pan_r, out=right)

        np.multiply(seg[:, 0], left, out=tmp)
        np.add(target[:, 0], tmp, out=target[:, 0])
        np.multiply(seg[:, 1], right, out=tmp)
        np.add(target[:, 1], tmp, out=target[:, 1])

        self.age += n
        if self.age >= effective_length:
            self.dead = True

    def _linear_samples(
        self,
        positions: np.ndarray,
        dest: np.ndarray,
        other: np.ndarray,
        workspace: PadRenderWorkspace,
    ) -> None:
        """Interpolate positions using only the voice workspace."""
        n = len(positions)
        i0 = workspace.i0[:n]
        np.copyto(i0, positions, casting="unsafe")
        frac = workspace.frac[:n]
        np.subtract(positions, i0, out=frac, casting="unsafe")
        i1 = workspace.i1[:n]
        np.add(i0, 1, out=i1)
        if self.loop:
            np.subtract(i1, self.s0, out=i1)
            np.remainder(i1, self.s1 - self.s0, out=i1)
            np.add(i1, self.s0, out=i1)
        else:
            np.minimum(i1, self.s1 - 1, out=i1)
        np.take(self.data, i0, axis=0, out=dest)
        np.take(self.data, i1, axis=0, out=other)
        np.subtract(other, dest, out=other)
        np.multiply(other, frac[:, None], out=other)
        np.add(dest, other, out=dest)

    def _sinc_samples(self, positions: np.ndarray, span: int, loop: bool) -> np.ndarray:
        """Eight-lobe Lanczos interpolation for offline export only."""
        radius = 8
        centre = np.floor(positions).astype(np.int64)
        taps = np.arange(-radius + 1, radius + 1, dtype=np.int64)
        indices = centre[:, None] + taps[None, :]
        distance = positions[:, None] - indices
        # Downward pitch shifts read more densely and need only interpolation;
        # upward shifts decimate the source, so narrow the reconstruction band
        # before sampling to suppress aliases above the new Nyquist limit.
        cutoff = min(1.0, 1.0 / max(self.rate, 1e-12))
        weights = cutoff * np.sinc(distance * cutoff) * np.sinc(distance / radius)
        weights[np.abs(distance) >= radius] = 0.0
        if loop:
            indices = (indices - self.s0) % span + self.s0
        else:
            np.clip(indices, self.s0, self.s1 - 1, out=indices)
        normal = np.sum(weights, axis=1, keepdims=True)
        weights /= np.where(np.abs(normal) > 1e-12, normal, 1.0)
        result = np.einsum("nt,ntc->nc", weights, self.data[indices], optimize=True)
        return np.ascontiguousarray(result, dtype=np.float32)


def _pan_gains(pan: float) -> tuple[float, float]:
    """Constant-power pan for a sampler voice."""
    p = (max(-1.0, min(1.0, pan)) + 1.0) * 0.25 * np.pi
    return float(np.cos(p)), float(np.sin(p))


def _balance_gains(pan: float) -> tuple[float, float]:
    """Stereo balance: unity at centre, attenuate only the opposite side."""
    p = max(-1.0, min(1.0, float(pan)))
    left = 1.0 if p <= 0.0 else float(np.cos(p * np.pi * 0.5))
    right = 1.0 if p >= 0.0 else float(np.cos(-p * np.pi * 0.5))
    return left, right
