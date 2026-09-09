"""Real-time audio engine.

One PortAudio callback does everything: it advances the transport, spawns
voices at sample-accurate offsets inside the block, mixes them through eight
track buses into a limited master, and reports meter levels back to the GUI.

The GUI thread never touches the voice list — it posts commands on a queue.
"""

from __future__ import annotations

import queue
import time
from dataclasses import dataclass
from typing import Any

import numpy as np

from .audio_kernel import (
    AUDIO_SAMPLE_RATE,
    DEFAULT_BLOCKSIZE,
    ULTRA_LOW_LATENCY_BLOCKSIZE,
    MasteringKernel,
)
from .fx import MasterChain, MixRack, TrackChain
from .linux_audio import LinuxAudioRuntime
from .model import Project, Pad, NTRACKS, NPADS
from .music import Note, automation_values
from .synth import ArpState, SynthVoice
from .orchestra import prepare_patch

FADE = 0.004  # seconds, used for chokes and panics
MAX_SYNTH_VOICES = 8
MAX_PAD_VOICES = 64
AUDITION = -2  # pad index reserved for the CHOP editor's preview voice
METRONOME = -3  # cue bus; independent of project Track 1
CALLBACK_HISTORY = 512  # ~11 s of callbacks at 1024 frames, ~1.4 s at 128
SEND_TAIL = 6.0  # seconds the sends keep running after the last send
TRACK_DSP_TAIL = 1.0  # let FIR/compressor state drain, then put it to sleep
OFFLINE_VOICE_CHUNK = 16_384  # bound bounce scratch to about 1.1 MiB
_CONFIGURED_DEVICE = object()


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


def _make_click(sample_rate: int, accent: bool) -> np.ndarray:
    """Build one reusable metronome transient outside the callback."""
    n = max(1, int(0.045 * sample_rate))
    t = np.arange(n, dtype=np.float32) / np.float32(sample_rate)
    wave = (
        np.sin(np.float32(2.0 * np.pi * (1600 if accent else 950)) * t)
        * np.exp(np.float32(-45.0) * t)
        * np.float32(0.5 if accent else 0.3)
    ).astype(np.float32)
    return np.ascontiguousarray(np.column_stack((wave, wave)), dtype=np.float32)


class Engine:
    def __init__(
        self, library, sample_rate: int = AUDIO_SAMPLE_RATE, blocksize: int = DEFAULT_BLOCKSIZE
    ):
        self.lib = library
        self.sr = sample_rate
        self.blocksize = blocksize
        self.project: Project = Project()

        # PortAudio is imported only when live audio is requested.  Keeping it
        # out of module import makes offline bounce, tests and the callback
        # benchmark genuinely device-independent (and avoids probing ALSA on
        # machines that intentionally have no audio device attached).
        self.stream: Any | None = None
        # ``None`` means the current system default. An explicit PortAudio
        # index pins this app to a device until the user changes it.
        self.output_device: int | str | None = None
        self.linux_audio = LinuxAudioRuntime()
        self._live_starting = False
        self.cmds: "queue.SimpleQueue[tuple]" = queue.SimpleQueue()
        self.voices: list[PadVoice] = []
        self.synth_voices: list[SynthVoice] = []
        self._synth_variants = {}
        self._variant_patch = None
        self.arp_state = ArpState()
        self._arp_samples_until = 0.0
        # Song note takes receive the same generated notes as the live arp.
        self.arp_note_capture: tuple[list[Note], float] | None = None

        # transport
        self.playing = False
        self.mode = "pattern"  # pattern | song
        self.beat = 0.0
        self.metronome = False
        self.recording = False
        self.loop_song = False
        self._resume_audio = True

        # feedback for the GUI (written by the audio thread, read by the GUI)
        self.meters = np.zeros(NTRACKS, dtype=np.float32)
        self.peaks = np.zeros(NTRACKS, dtype=np.float32)
        self.master_meter = np.zeros(2, dtype=np.float32)
        self.master_peak = 0.0
        self.underruns = 0
        self.cache_misses = 0
        self.pattern_dirty = False
        self.hit_flash: dict[int, float] = {}
        self.cpu = 0.0
        # Rolling window of callback durations, in seconds.  The instantaneous
        # `cpu` figure is too jumpy to tune a block size against — what decides
        # whether a buffer is safe is the *worst* callback, not the average, so
        # keep the last few seconds and let the GUI thread take percentiles.
        # Preallocated and written by scalar index: no allocation in the
        # audio thread.
        self._cb_times = np.zeros(CALLBACK_HISTORY, dtype=np.float64)
        self._cb_i = 0
        self._cb_filled = 0
        # Where the CHOP editor's preview voice is, in seconds into the source,
        # or None when nothing is previewing. Written by the audio thread.
        self.audition_time: float | None = None

        self._tbuf = np.zeros((NTRACKS, blocksize, 2), dtype=np.float32)
        self._master = np.zeros((blocksize, 2), dtype=np.float32)
        self._bus = np.zeros((blocksize, 2), dtype=np.float32)
        self._preview = np.zeros((blocksize, 2), dtype=np.float32)
        self._send_scratch = np.zeros((blocksize, 2), dtype=np.float32)
        self._meter_scratch = np.zeros((blocksize, 2), dtype=np.float32)
        # Microphone monitoring arrives from the independent input callback.
        # A lock-free, preallocated latest-block ring keeps both audio callbacks
        # away from Queue locks and heap allocations. Old cue audio is disposable.
        self._monitor_audio = np.zeros((8, blocksize, 2), dtype=np.float32)
        self._monitor_lengths = np.zeros(8, dtype=np.int32)
        self._monitor_write = 0
        self._monitor_read = 0
        self._note_events: list = []
        self._audio_events: list = []
        self._pad_workspace = PadRenderWorkspace(blocksize)
        self.preview_gain = 1.0
        self.mastering = MasteringKernel(sample_rate, blocksize=blocksize)
        # Insert chains, the two send buses and the master chain.  Untouched
        # tracks cost nothing: the callback skips a chain whose settings are
        # still at their defaults.
        self.rack = MixRack(NTRACKS)
        self.rack.prepare(blocksize)
        # GUI-side request keys suppress duplicate tone builds when a non-EQ
        # control moves. Prepared kernels themselves cross to the callback via
        # ``cmds``; GUI code never mutates an installed convolver.
        self._track_tone_requests: list[tuple | None] = [None] * NTRACKS
        self._master_tone_request: tuple | None = None
        self._send_tail = 0
        self._track_tail = np.zeros(NTRACKS, dtype=np.int64)
        self._click_plain = _make_click(sample_rate, False)
        self._click_accent = _make_click(sample_rate, True)
        self._lock_realtime_working_set()

    def _lock_realtime_working_set(self) -> None:
        """Keep callback scratch resident without pinning decoded songs."""
        workspace = self._pad_workspace
        mastering = self.mastering
        self.linux_audio.lock_arrays(
            self._tbuf,
            self._master,
            self._bus,
            self._preview,
            self._send_scratch,
            self._meter_scratch,
            self._monitor_audio,
            self._monitor_lengths,
            self._cb_times,
            self.meters,
            self.peaks,
            self.master_meter,
            workspace.base,
            workspace.t,
            workspace.idx,
            workspace.i0,
            workspace.sample_pos,
            workspace.blend_pos,
            workspace.loop_phase,
            workspace.loop_alpha,
            workspace.first_cycle,
            workspace.i1,
            workspace.frac,
            workspace.env,
            workspace.release_env,
            workspace.scale,
            workspace.tmp,
            workspace.seg,
            workspace.other,
            workspace.loop_seg,
            workspace.loop_other,
            mastering._peak,
            mastering._target,
            mastering._ramp,
            self._click_plain,
            self._click_accent,
        )

    def reset_fx(self) -> None:
        """Clear every effect's tail. Runs on the audio thread, via the queue."""
        self.cmds.put(("fxreset",))

    def queue_monitor(self, audio: np.ndarray, gain: float = 1.0) -> None:
        """Offer one microphone block to the cue bus without ever blocking."""
        chunk = np.asarray(audio, dtype=np.float32)
        if chunk.ndim != 2 or chunk.shape[1] != 2 or not len(chunk):
            return
        take = min(len(chunk), self._monitor_audio.shape[1])
        slot = self._monitor_write % len(self._monitor_audio)
        source = chunk[-take:]
        target = self._monitor_audio[slot, :take]
        np.multiply(source, np.float32(gain), out=target)
        self._monitor_lengths[slot] = take
        # Publish only after the slot is complete. Integer assignment is atomic
        # under the GIL; NumPy may release it during the copy above.
        self._monitor_write += 1

    def prepare_track_tone(self, index: int, fx=None) -> None:
        """Build one track's IR and filter FFT on the calling (GUI) thread."""
        index = int(index)
        if not 0 <= index < NTRACKS:
            return
        settings = fx if fx is not None else self.project.tracks[index].fx
        request = (self.blocksize, TrackChain.tone_key(settings))
        if self._track_tone_requests[index] == request:
            return
        prepared = TrackChain.prepare_tone(settings, self.rack.channels, self.blocksize)
        self._track_tone_requests[index] = request
        self.cmds.put(("tracktone", index, prepared))

    def prepare_master_tone(self, fx=None) -> None:
        """Build the master tone IR and filter FFT away from the callback."""
        settings = fx if fx is not None else self.project.master_fx
        request = (self.blocksize, MasterChain.tone_key(settings))
        if self._master_tone_request == request:
            return
        prepared = MasterChain.prepare_tone(settings, self.rack.channels, self.blocksize)
        self._master_tone_request = request
        self.cmds.put(("mastertone", prepared))

    def prepare_fx(self, project: Project | None = None) -> None:
        """Prepare every project tone kernel before live processing needs it."""
        proj = project or self.project
        for index, track in enumerate(proj.tracks[:NTRACKS]):
            self.prepare_track_tone(index, track.fx)
        self.prepare_master_tone(proj.master_fx)

    # ── lifecycle ────────────────────────────────────────────
    def start(self, device=_CONFIGURED_DEVICE) -> None:
        if self.stream is not None:
            return
        prepare_patch(self.project.synth)
        import sounddevice as sd

        output_device = self.output_device if device is _CONFIGURED_DEVICE else device
        # Exercise oscillator/filter/RNG initialization before PortAudio can
        # request its first live block. This voice never reaches a device.
        warmup = np.zeros((self.blocksize, 2), dtype=np.float32)
        SynthVoice(60, 0.5, self.sr, gate_frames=self.blocksize).render(warmup, self.project.synth)
        self.linux_audio.configure_host(self.blocksize, self.sr)
        self.linux_audio.begin_stream()
        self.prepare_fx(self.project)
        self.mastering.reset()
        # Fault in native code/RNG state before the first live note. These
        # temporary voices never enter the project or the output stream.
        warm = np.zeros((self.blocksize, 2), dtype=np.float32)
        for note in range(60, 60 + MAX_SYNTH_VOICES):
            voice = SynthVoice(note, 0.5, self.sr)
            for _ in range(3):
                warm.fill(0)
                voice.render(warm, self.project.synth)
        stream = sd.OutputStream(
            samplerate=self.sr,
            blocksize=self.blocksize,
            channels=2,
            dtype="float32",
            device=output_device,
            latency="low",
            clip_off=True,
            dither_off=True,
            prime_output_buffers_using_stream_callback=True,
            callback=self._callback,
        )
        try:
            self._live_starting = True
            stream.start()
        except Exception:
            try:
                stream.close()
            except Exception:
                pass
            raise
        finally:
            self._live_starting = False
        self.stream = stream
        self.output_device = output_device

    def stop(self) -> None:
        if self.stream is not None:
            stream, self.stream = self.stream, None
            try:
                stream.stop()
            finally:
                stream.close()

    def configure_blocksize(self, frames: int) -> None:
        """Prepare a fixed callback size while the stream is stopped."""
        frames = int(frames)
        if frames < 64 or frames > 4096 or frames & (frames - 1):
            raise ValueError("audio buffer must be a power of two from 64 to 4096")
        if self.stream is not None:
            raise RuntimeError("stop audio before changing its buffer")
        self.blocksize = frames
        self.voices.clear()
        self.synth_voices.clear()
        self.arp_state.held.clear()
        self._tbuf = np.zeros((NTRACKS, frames, 2), dtype=np.float32)
        self._master = np.zeros((frames, 2), dtype=np.float32)
        self._bus = np.zeros((frames, 2), dtype=np.float32)
        self._preview = np.zeros((frames, 2), dtype=np.float32)
        self._send_scratch = np.zeros((frames, 2), dtype=np.float32)
        self._meter_scratch = np.zeros((frames, 2), dtype=np.float32)
        self._monitor_audio = np.zeros((8, frames, 2), dtype=np.float32)
        self._monitor_lengths = np.zeros(8, dtype=np.int32)
        self._monitor_write = self._monitor_read = 0
        self._pad_workspace = PadRenderWorkspace(frames)
        self.mastering = MasteringKernel(self.sr, blocksize=frames)
        self.rack.reset()
        self.rack.prepare(frames)
        self._track_tone_requests = [None] * NTRACKS
        self._master_tone_request = None
        self.prepare_fx(self.project)
        self._send_tail = 0
        self._track_tail.fill(0)
        self.reset_timing()
        self.linux_audio.configure_host(self.blocksize, self.sr)
        self.linux_audio.reset_locks()
        self._lock_realtime_working_set()

    def restart(self, blocksize: int, device=_CONFIGURED_DEVICE) -> None:
        """Restart the live stream at a new fixed buffer, rolling back on error."""
        old = self.blocksize
        old_device = self.output_device
        output_device = old_device if device is _CONFIGURED_DEVICE else device
        was_running = self.stream is not None
        if was_running:
            self.stop()
        try:
            self.configure_blocksize(blocksize)
            if was_running:
                self.start(output_device)
        except Exception:
            self.configure_blocksize(old)
            self.output_device = old_device
            if was_running:
                try:
                    self.start(old_device)
                except Exception:
                    pass
            raise

    def restart_device(self, device: int | str | None) -> None:
        """Reconnect to an output, restoring the last working one on failure."""
        old_device = self.output_device
        was_running = self.stream is not None
        if was_running:
            self.stop()
        try:
            self.start(device)
        except Exception:
            self.output_device = old_device
            if was_running:
                try:
                    self.start(old_device)
                except Exception:
                    pass
            raise

    @property
    def latency_ms(self) -> float:
        return (self.stream.latency * 1000.0) if self.stream else 0.0

    @property
    def period_ms(self) -> float:
        """Wall-clock time one block of audio buys us."""
        return self.blocksize / self.sr * 1000.0

    def timing_stats(self) -> dict[str, float]:
        """Percentiles over the recent callback window, in milliseconds.

        Called from the GUI thread. The audio thread only ever writes single
        slots of `_cb_times`, so a torn read costs one stale sample and never
        blocks the callback — worth far more than a lock here.
        """
        period = self.period_ms
        n = self._cb_filled
        if not n:
            return {
                "p50": 0.0,
                "p99": 0.0,
                "max": 0.0,
                "period": period,
                "headroom": 1.0,
                "xruns": self.underruns,
                "blocks": 0,
            }
        window = self._cb_times[:n] * 1000.0
        worst = float(window.max())
        return {
            "p50": float(np.percentile(window, 50)),
            "p99": float(np.percentile(window, 99)),
            "max": worst,
            "period": period,
            "headroom": max(0.0, 1.0 - worst / period) if period else 0.0,
            "xruns": self.underruns,
            "blocks": n,
        }

    def reset_timing(self) -> None:
        """Forget the timing window and the xrun count — call after changing
        block size, so the new setting is judged on its own numbers."""
        self._cb_i = 0
        self._cb_filled = 0
        self._cb_times.fill(0.0)
        self.underruns = 0

    # ── commands from the GUI thread ─────────────────────────
    def trigger_pad(self, index: int, velocity: float = 1.0) -> None:
        self.cmds.put(("pad", index, velocity))

    def release_pad(self, index: int) -> None:
        self.cmds.put(("off", index))

    def sample_note_on(self, index: int, note: int, velocity: float = 1.0) -> None:
        if not 0 <= index < NPADS or not 0 <= note <= 127:
            raise ValueError("invalid sample note destination or pitch")
        self.cmds.put(("sampleon", index, note, velocity))

    def sample_note_off(self, index: int, note: int) -> None:
        self.cmds.put(("sampleoff", index, note))

    def sample_panic(self) -> None:
        self.cmds.put(("samplepanic",))

    def synth_note_on(self, note: int, velocity: float = 1.0) -> None:
        prepare_patch(self.project.synth)
        self.cmds.put(("synthon", max(0, min(127, int(note))), velocity))

    def synth_note_off(self, note: int) -> None:
        self.cmds.put(("synthoff", max(0, min(127, int(note)))))

    def synth_panic(self) -> None:
        self.cmds.put(("synthpanic",))

    def audition(
        self,
        sample_id: str,
        start: float,
        end: float,
        pitch: float = 0.0,
        gain: float = 1.0,
        loop: bool = False,
    ) -> None:
        self.cmds.put(("audition", sample_id, start, end, pitch, gain, loop))

    def stop_audition(self) -> None:
        self.cmds.put(("stopaudition",))

    def panic(self) -> None:
        self.cmds.put(("panic",))

    def play(self, from_beat: float | None = None) -> None:
        self.cmds.put(("play", from_beat))

    def stop_transport(self, rewind: bool = False) -> None:
        self.cmds.put(("stopt", rewind))

    def set_position(self, beat: float) -> None:
        self.cmds.put(("seek", max(0.0, beat)))

    def toggle_play(self) -> None:
        if self.playing:
            self.stop_transport(False)
        else:
            self.play(None)

    # ── voice construction ───────────────────────────────────
    def _cached_sample(self, sample_id: str, reverse: bool) -> np.ndarray | None:
        """Resolve an RT-ready sample without allowing production disk I/O."""
        method_name = "cached_reversed_audio" if reverse else "cached_audio"
        cached = getattr(self.lib, method_name, None)
        if cached is not None:
            data = cached(sample_id)
            if data is None:
                self.cache_misses += 1
            return data
        # Lightweight test/tool libraries predate the explicit cache protocol
        # and already keep their arrays in memory.
        loader = getattr(self.lib, "reversed_audio" if reverse else "audio", None)
        return loader(sample_id) if loader is not None else None

    def _voice_for_pad(self, pad: Pad, velocity: float, note: int | None = None) -> PadVoice | None:
        if pad.empty:
            return None
        data = self._cached_sample(pad.sample_id, pad.reverse)
        if data is None or len(data) < 4:
            return None
        total = len(data) / self.sr
        start, end = pad.start, (pad.end if pad.end > pad.start else total)
        if pad.reverse:
            start, end = total - end, total - start
        frame_index = round if pad.sync_beats > 0 else int
        s0 = max(0, frame_index(start * self.sr))
        s1 = min(len(data), frame_index(end * self.sr))
        if s1 - s0 < 8:
            return None

        pitch = pad.pitch + (note - pad.root_note if note is not None else 0)
        rate = float(2.0 ** (pitch / 12.0))
        if pad.sync_beats > 0 and self.project is not None:
            target_frames = self.sr * 60.0 * pad.sync_beats / max(1.0, self.project.bpm)
            rate *= (s1 - s0) / target_frames
        attack = max(1, int(pad.attack * self.sr))
        release = max(1, int(pad.release * self.sr))
        # Number of output frames whose source position is still inside the
        # selected range. Envelopes may overlap on a very short slice, but they
        # must never extend playback into audio after ``s1``.
        natural = max(1, int(np.ceil((s1 - s0) / rate)))
        # Gate playback still ends at the selected source boundary; only loop
        # mode is allowed to wrap indefinitely while held.
        length = int(self.sr * 3600) if pad.mode == "loop" else natural
        pl, pr = _pan_gains(pad.pan)
        return PadVoice(
            data=data,
            source_id=pad.sample_id,
            s0=s0,
            s1=s1,
            rate=rate,
            gain=float(pad.gain * velocity),
            pan_l=pl,
            pan_r=pr,
            attack=attack,
            release=release,
            length=length,
            track=max(0, min(NTRACKS - 1, pad.track)),
            choke=pad.choke,
            pad_index=-1,
            loop=(pad.mode == "loop"),
            note=note,
            gated=pad.mode != "one-shot",
            loop_crossfade=max(0, int(pad.loop_crossfade * self.sr)),
        )

    def _spawn(
        self,
        pad: Pad,
        index: int,
        velocity: float,
        offset: int = 0,
        gate_frames: int | None = None,
        *,
        live_trigger: bool = True,
        sequence_id: str | None = None,
        note: int | None = None,
    ) -> None:
        v = self._voice_for_pad(pad, velocity, note)
        if v is None:
            return
        v.pad_index = index
        v.live_trigger = live_trigger
        v.sequence_id = sequence_id
        v.start_offset = max(0, offset)
        if gate_frames is not None and pad.mode != "one-shot":
            v.length = min(
                v.length, max(gate_frames, 1 if note is not None else v.attack) + v.release
            )
        fade = int(FADE * self.sr)
        for other in self.voices:
            if not other.dead and self._pad_trigger_cuts(other, v, pad, index):
                other.release_now(fade, v.start_offset - other.start_offset)
        # Long one-shots may intentionally overlap, but callback work must stay
        # bounded under dense controller input or a malformed event stream.
        pad_voice_count = 0
        oldest = None
        for other in self.voices:
            if other.pad_index >= 0 and not other.dead:
                pad_voice_count += 1
                same_owner = other.live_trigger == live_trigger and (
                    live_trigger or other.sequence_id == sequence_id
                )
                if same_owner and (oldest is None or other.age > oldest.age):
                    oldest = other
        if index >= 0 and pad_voice_count >= MAX_PAD_VOICES:
            if oldest is None:
                # At the cap, preserve other performances and pattern layers.
                return
            self.voices.remove(oldest)
        self.voices.append(v)
        self.hit_flash[index] = time.monotonic()

    def _pad_trigger_cuts(
        self, previous: PadVoice, current: PadVoice, pad: Pad, index: int
    ) -> bool:
        """Whether a new pad hit steals an existing voice.

        Live MPC taps and each placed pattern own separate choking domains.
        Within one sequence, groups and CUT SOURCE still work across pad banks.
        Overlapping placements retain their layers when played or bounced.
        """
        if index < 0 or previous.pad_index < 0:
            return False
        if previous.live_trigger != current.live_trigger:
            return False
        if not current.live_trigger and previous.sequence_id != current.sequence_id:
            return False
        if previous.note is not None or current.note is not None:
            # A chromatic chord must not choke itself, a drum hit or another
            # instrument using the same source. Ownership above also protects
            # live playing from sequenced backing parts.
            return bool(
                previous.note is not None
                and current.note is not None
                and previous.pad_index == index
                and (pad.mono or previous.note == current.note)
            )
        if pad.choke and previous.choke == pad.choke:
            return True
        if pad.mode != "one-shot" and previous.pad_index == index:
            return True
        return bool(
            self.project.self_choke
            and current.source_id
            and previous.source_id == current.source_id
        )

    def _spawn_synth(
        self,
        note: int,
        velocity: float,
        offset: int = 0,
        gate_frames: int | None = None,
        *,
        voices=None,
        live_trigger: bool = True,
    ) -> None:
        voices = self.synth_voices if voices is None else voices
        patch = self.project.synth
        # Retrigger within the same performance source. Playing along must not
        # release the backing pattern (or the held live note at its next event).
        for voice in voices:
            if voice.note == note and voice.live_trigger == live_trigger and not voice.dead:
                voice.note_off(0.008)
        max_voices = 4 if self.blocksize <= ULTRA_LOW_LATENCY_BLOCKSIZE else MAX_SYNTH_VOICES
        live_count = 0
        oldest = None
        for voice in voices:
            if not voice.dead:
                live_count += 1
                if oldest is None or voice.age > oldest.age:
                    oldest = voice
        if live_count >= max_voices and oldest is not None:
            oldest.dead = True
            voices.remove(oldest)
        source_key = (patch.sample_source, patch.sample_layer)
        if source_key != self._variant_patch:
            self._synth_variants.clear()
            self._variant_patch = source_key
        variant = self._synth_variants.get(note, 0)
        self._synth_variants[note] = variant + 1
        voices.append(
            SynthVoice(
                note=note,
                velocity=float(velocity),
                sample_rate=self.sr,
                track=max(0, min(NTRACKS - 1, patch.track)),
                start_offset=max(0, offset),
                gate_frames=gate_frames,
                variant=variant,
                live_trigger=live_trigger,
            )
        )

    def _release_synth(self, note: int) -> None:
        for voice in self.synth_voices:
            if voice.note == note and voice.live_trigger and not voice.dead:
                voice.note_off(self.project.synth.release)

    def _schedule_arp(self, frames: int, start_beat: float) -> None:
        settings = self.project.arp
        if not settings.enabled or not self.arp_state.held:
            self._arp_samples_until = 0.0
            return
        step = max(
            1.0, self.sr * (60.0 / max(1.0, self.project.bpm)) * max(0.0625, settings.rate_beats)
        )
        pos = self._arp_samples_until
        while pos < frames:
            note = self.arp_state.next_note(settings)
            if note is None:
                break
            note = min(127, max(0, note))
            gate = max(1, int(step * min(1.0, max(0.05, settings.gate))))
            offset = int(max(0.0, pos))
            self._spawn_synth(note, 0.92, offset, gate)
            if self.playing:
                bps = self.project.bpm / 60.0 / self.sr
                beat = start_beat + offset * bps
                duration = gate * bps
                capture = self.arp_note_capture
                if capture is not None:
                    notes, origin = capture
                    notes.append(Note(note, max(0.0, beat - origin), duration, 0.92))
                elif self.recording and self.mode == "pattern":
                    pattern = self.project.pattern()
                    local = beat % pattern.length_beats
                    pattern.notes.append(Note(note, local, duration, 0.92))
                    self.pattern_dirty = True
            pos += step
        self._arp_samples_until = pos - frames

    # ── sequencing ───────────────────────────────────────────
    def _swing_offset(self, pat, step: int) -> float:
        if not self.project.swing or pat.div < 2:
            return 0.0
        per_eighth = max(1, pat.div // 2)
        if (step // per_eighth) % 2 == 1:
            return (self.project.swing / 100.0) * (1.0 / pat.div) * 0.66
        return 0.0

    def _pattern_events(
        self,
        pat,
        b0: float,
        b1: float,
        origin: float,
        limit: float,
        out: list,
        sequence_id: str,
    ) -> None:
        # Negative event indices encode synth pitch; nonnegative indices are pads.
        # Clip ends and pattern boundaries trim gates, preventing stuck notes.
        length = pat.length_beats
        if length > 0 and pat.notes:
            first = max(0, int((b0 - origin) // length))
            last = max(first, int((min(b1, limit) - origin) // length))
            for cycle in range(first, last + 1):
                base = origin + cycle * length
                for note in pat.notes:
                    beat = base + note.start
                    if note.start < length and b0 - 1e-10 <= beat < min(b1, limit) - 1e-10:
                        gate = min(note.duration, length - note.start, limit - beat)
                        # Preserve the existing five-field event protocol:
                        # -1..-128 = synth; 0..63 = drum; >=64 = sample slot/pitch.
                        destination = (
                            -note.pitch - 1
                            if note.pad is None
                            else NPADS + note.pad * 128 + note.pitch
                        )
                        out.append((beat, destination, note.velocity, gate, sequence_id))
        sd_ = 1.0 / pat.div
        total = pat.total_steps
        if total <= 0:
            return
        k = int(np.floor((b0 - origin) / sd_)) - 1
        while True:
            beat = origin + k * sd_
            if beat >= min(b1, limit) + sd_:
                break
            if k >= 0:
                step = k % total
                beat += self._swing_offset(pat, step)
                if b0 <= beat < b1 and beat < limit:
                    for pad_idx, row in pat.steps.items():
                        vel = row.get(step)
                        if vel:
                            out.append((beat, int(pad_idx), float(vel), None, sequence_id))
            k += 1

    def _collect(self, b0: float, b1: float, *, reuse: bool = False) -> tuple[list, list]:
        proj = self.project
        if reuse:
            notes = self._note_events
            audio = self._audio_events
            notes.clear()
            audio.clear()
        else:
            notes = []
            audio = []
        if self.mode == "pattern":
            pat = proj.pattern()
            self._pattern_events(pat, b0, b1, 0.0, float("inf"), notes, pat.id)
        else:
            any_row_solo = any(row.solo for row in proj.rows)
            for row in proj.rows:
                if row.mute or (any_row_solo and not row.solo):
                    continue
                for clip in row.clips:
                    if clip.mute:
                        continue
                    end = clip.start_beat + clip.length_beats
                    if end <= b0 or clip.start_beat >= b1:
                        continue
                    if clip.kind == "pattern":
                        pat = next((p for p in proj.patterns if p.id == clip.ref), None)
                        if pat:
                            self._pattern_events(
                                pat,
                                max(b0, clip.start_beat),
                                b1,
                                clip.start_beat,
                                end,
                                notes,
                                clip.id,
                            )
                    elif b0 <= clip.start_beat < b1:
                        audio.append(clip)
        notes.sort(key=lambda event: event[0])
        return notes, audio

    def _spawn_audio_clip(self, clip, offset: int, elapsed_beats: float = 0.0) -> None:
        data, s0, s1 = self._audio_clip_source(clip)
        if data is None:
            return
        spb = 60.0 / self.project.bpm
        if s1 - s0 < 8:
            return
        arranged = max(1, int(clip.length_beats * spb * self.sr))
        length = arranged if clip.loop else min(arranged, s1 - s0)
        elapsed = max(0, int(elapsed_beats * spb * self.sr))
        if elapsed >= length:
            return
        pl, pr = _balance_gains(0.0)
        self.voices.append(
            PadVoice(
                data=data,
                source_id=clip.ref,
                s0=s0,
                s1=s1,
                rate=1.0,
                gain=float(clip.gain),
                pan_l=pl,
                pan_r=pr,
                attack=max(1, int(0.003 * self.sr)),
                release=max(1, int(0.008 * self.sr)),
                length=length,
                track=max(0, min(NTRACKS - 1, clip.track)),
                choke=0,
                pad_index=-1,
                loop=bool(clip.loop),
                loop_crossfade=max(0, int(clip.loop_crossfade * self.sr)),
                age=elapsed,
                start_offset=max(0, offset),
            )
        )

    def _audio_overlaps(self, beat: float):
        """Audio blocks already in progress when transport starts or wraps."""
        any_row_solo = any(row.solo for row in self.project.rows)
        for row in self.project.rows:
            if row.mute or (any_row_solo and not row.solo):
                continue
            for clip in row.clips:
                if (
                    not clip.mute
                    and clip.kind == "audio"
                    and clip.start_beat < beat < clip.start_beat + clip.length_beats
                ):
                    yield clip, beat - clip.start_beat

    def _audio_clip_source(self, clip) -> tuple[np.ndarray | None, int, int]:
        """Return data and source bounds, preserving the same trim when reversed."""
        normal = self._cached_sample(clip.ref, False)
        if normal is None:
            return None, 0, 0
        original_start = max(0, min(len(normal), int(clip.offset * self.sr)))
        available = max(0, len(normal) - original_start)
        wanted = int(clip.source_length * self.sr) if clip.source_length > 0 else available
        original_end = min(len(normal), original_start + max(0, wanted))
        if clip.reverse:
            data = self._cached_sample(clip.ref, True)
            return data, len(normal) - original_end, len(normal) - original_start
        return normal, original_start, original_end

    def preload_project_audio(self, project: Project | None = None) -> None:
        """Decode every referenced source and reverse copy off the audio thread."""
        proj = project or self.project
        prepare_patch(proj.synth)
        normal: set[str] = set()
        reversed_refs: set[str] = set()
        for pad in proj.pads:
            if pad.sample_id:
                normal.add(pad.sample_id)
                if pad.reverse:
                    reversed_refs.add(pad.sample_id)
        for row in proj.rows:
            for clip in row.clips:
                if clip.kind == "audio" and clip.ref:
                    normal.add(clip.ref)
                    if clip.reverse:
                        reversed_refs.add(clip.ref)
        load = getattr(self.lib, "audio", None)
        reverse = getattr(self.lib, "reversed_audio", None)
        if load is not None:
            for sample_id in normal:
                load(sample_id)
        if reverse is not None:
            for sample_id in reversed_refs:
                reverse(sample_id)

    def _click(self, offset: int, accent: bool) -> None:
        data = self._click_accent if accent else self._click_plain
        n = len(data)
        pl, pr = _balance_gains(0.0)
        self.voices.append(
            PadVoice(
                data=data,
                source_id=None,
                s0=0,
                s1=n,
                rate=1.0,
                gain=1.0,
                pan_l=pl,
                pan_r=pr,
                attack=1,
                release=max(1, n // 4),
                length=n,
                track=0,
                choke=0,
                pad_index=METRONOME,
                loop=False,
                start_offset=max(0, offset),
            )
        )

    def _record(self, pad_index: int, velocity: float) -> None:
        pat = self.project.pattern()
        length = pat.length_beats
        local = self.beat % length if length else 0.0
        step = int(round(local * pat.div)) % pat.total_steps
        pat.set(pad_index, step, round(velocity, 3))
        self.pattern_dirty = True

    # ── the callback ─────────────────────────────────────────
    def _automation_beats(self, mode, start, frames):
        if mode != "song" or not any(a.enabled and a.points for a in self.project.automation):
            return None
        return start + np.arange(frames) * (self.project.bpm / 60.0 / self.sr)

    def _track_controls(self, index, beats):
        track = self.project.tracks[index]
        if beats is None:
            pl, pr = _balance_gains(track.pan)
            return track.gain * pl, track.gain * pr
        gain = automation_values(self.project, f"track:{index}:gain", beats, track.gain)
        pan = automation_values(self.project, f"track:{index}:pan", beats, track.pan)
        return (
            gain * np.cos(np.maximum(pan, 0) * np.pi * 0.5),
            gain * np.cos(np.minimum(pan, 0) * np.pi * 0.5),
        )

    def _callback(self, outdata, frames, time_info, status) -> None:
        t_start = time.perf_counter()
        if self._live_starting or self.stream is not None:
            self.linux_audio.prepare_callback_thread()
        output_underflow = getattr(status, "output_underflow", None)
        if bool(status) and (output_underflow is None or output_underflow):
            self.underruns += 1

        self._process_commands()
        published = self._monitor_write
        monitor = None
        if published != self._monitor_read:
            slot = (published - 1) % len(self._monitor_audio)
            monitor = self._monitor_audio[slot, : int(self._monitor_lengths[slot])]
            self._monitor_read = published
        offset = 0
        while offset < frames:
            count = frames - offset
            end = None
            if self.playing and self.mode == "song":
                proj = self.project
                loop_start = max(0.0, float(proj.loop_start))
                loop_end = float(proj.loop_end)
                end = loop_end if self.loop_song and loop_end > loop_start else proj.song_end()
                if end > 0:
                    until_end = (end - self.beat) * self.sr * 60.0 / proj.bpm
                    count = min(count, max(1, int(np.ceil(until_end - 1e-9))))
            cue = None if monitor is None else monitor[offset : offset + count]
            self._render_block(outdata[offset : offset + count], count, cue)
            offset += count
            # Finish the old audio before wrapping; the rest of this host
            # callback belongs to the next loop, never discarded musical time.
            if end is not None and end > 0 and self.beat >= end - 1e-9:
                self.beat = loop_start if self.loop_song else end
                self.playing = self.loop_song
                if self.loop_song:
                    self._resume_audio = True
                    fade = int(FADE * self.sr)
                    for voice in self.voices:
                        if voice.pad_index == -1 and not voice.dead:
                            voice.release_now(fade)

        elapsed = time.perf_counter() - t_start
        self.cpu = elapsed / (frames / self.sr)
        self._cb_times[self._cb_i] = elapsed
        self._cb_i = (self._cb_i + 1) % CALLBACK_HISTORY
        if self._cb_filled < CALLBACK_HISTORY:
            self._cb_filled += 1

    def _process_commands(self):
        pending_audition = None
        proj = self.project
        # 1 ─ commands
        while True:
            try:
                cmd = self.cmds.get_nowait()
            except queue.Empty:
                break
            kind = cmd[0]
            if kind == "pad":
                idx, vel = cmd[1], cmd[2]
                self._spawn(proj.pads[idx], idx, vel, 0)
                if self.recording and self.playing:
                    self._record(idx, vel)
            elif kind == "off":
                for v in self.voices:
                    if v.pad_index == cmd[1] and v.note is None and v.live_trigger and not v.dead:
                        pad = proj.pads[cmd[1]]
                        if pad.mode != "one-shot":
                            v.release_now(v.release)
            elif kind == "sampleon":
                _, index, note, velocity = cmd
                self._spawn(proj.pads[index], index, velocity, note=note)
            elif kind == "sampleoff":
                _, index, note = cmd
                for v in self.voices:
                    if v.pad_index == index and v.note == note and v.live_trigger and v.gated:
                        v.release_now(v.release)
            elif kind == "samplepanic":
                for v in self.voices:
                    if v.note is not None and v.live_trigger:
                        v.release_now(max(1, int(FADE * self.sr)))
            elif kind == "synthon":
                note, vel = cmd[1], cmd[2]
                was_empty = not self.arp_state.held
                self.arp_state.press(note)
                if proj.arp.enabled:
                    if was_empty:
                        self.arp_state.reset()
                        self._arp_samples_until = 0.0
                else:
                    self._spawn_synth(note, vel)
            elif kind == "synthoff":
                note = cmd[1]
                self.arp_state.release(note)
                self._release_synth(note)
            elif kind == "synthpanic":
                self.arp_state.held.clear()
                self._arp_samples_until = 0.0
                for voice in self.synth_voices:
                    voice.note_off(0.008)
            elif kind == "audition":
                # Scrub events can arrive faster than audio blocks. Only the
                # newest position can be heard; don't construct stale voices.
                pending_audition = cmd
            elif kind == "stopaudition":
                pending_audition = None
                fade = int(FADE * self.sr)
                for v in self.voices:
                    if v.pad_index == AUDITION and not v.dead:
                        v.release_now(fade)
            elif kind == "fxreset":
                # Effect tails belong to the project that made them; loading a
                # new one must not leave the old reverb ringing underneath it.
                self.rack.reset()
                self._send_tail = 0
                self._track_tail.fill(0)
            elif kind == "tracktone":
                _, index, prepared = cmd
                if prepared.blocksize == self.blocksize and 0 <= index < len(self.rack.tracks):
                    self.rack.tracks[index].install_tone(prepared)
            elif kind == "mastertone":
                prepared = cmd[1]
                if prepared.blocksize == self.blocksize:
                    self.rack.master.install_tone(prepared)
            elif kind == "panic":
                pending_audition = None
                fade = int(FADE * self.sr)
                for v in self.voices:
                    v.release_now(fade)
                self.arp_state.held.clear()
                self._arp_samples_until = 0.0
                for voice in self.synth_voices:
                    voice.note_off(0.008)
            elif kind == "play":
                if cmd[1] is not None:
                    self.beat = float(cmd[1])
                self.playing = True
                self._resume_audio = self.mode == "song"
            elif kind == "stopt":
                for voice in self.synth_voices:
                    if not voice.live_trigger:
                        voice.note_off(0.008)
                self.playing = False
                if cmd[1]:
                    self.beat = 0.0
                fade = int(FADE * self.sr)
                for v in self.voices:
                    # Stop pads and playlist audio, but leave the CHOP editor's
                    # dedicated audition voice (-2) alone.
                    if v.pad_index >= -1 and not (v.note is not None and v.live_trigger):
                        v.release_now(fade)
            elif kind == "seek":
                for voice in self.synth_voices:
                    if not voice.live_trigger:
                        voice.note_off(0.008)
                self.beat = cmd[1]
                self._resume_audio = self.mode == "song"
                fade = int(FADE * self.sr)
                for v in self.voices:
                    if (
                        v.pad_index == -1 or (v.note is not None and not v.live_trigger)
                    ) and not v.dead:
                        v.release_now(fade)

        if pending_audition is not None:
            _, sid, start, end, pitch, gain, loop = pending_audition
            tmp = Pad(
                sample_id=sid,
                start=start,
                end=end,
                pitch=pitch,
                gain=gain,
                mode="loop" if loop else "one-shot",
            )
            fade = int(FADE * self.sr)
            for v in self.voices:  # only one preview at a time
                if v.pad_index == AUDITION and not v.dead:
                    v.release_now(fade)
            self._spawn(tmp, AUDITION, 1.0, 0)

    def _render_block(self, outdata, frames, monitor=None):
        proj = self.project
        start_beat = self.beat
        if frames > self._tbuf.shape[1]:  # PortAudio asked for a bigger block
            self._tbuf = np.zeros((NTRACKS, frames, 2), dtype=np.float32)
            self._master = np.zeros((frames, 2), dtype=np.float32)
            self._preview = np.zeros((frames, 2), dtype=np.float32)
            self._send_scratch = np.zeros((frames, 2), dtype=np.float32)
            self._meter_scratch = np.zeros((frames, 2), dtype=np.float32)
        tbuf = self._tbuf[:, :frames]
        tbuf.fill(0.0)
        master = self._master[:frames]
        master.fill(0.0)
        preview_bus = self._preview[:frames]
        preview_bus.fill(0.0)

        # Automation uses the block start, before transport advances.
        automation_beats = self._automation_beats(self.mode, self.beat, frames)

        # 2 ─ transport
        if self.playing:
            bps = (proj.bpm / 60.0) / self.sr
            b0 = self.beat
            b1 = b0 + frames * bps
            notes, audio = self._collect(b0, b1, reuse=True)
            for beat, pad_idx, vel, _gate, sequence_id in notes:
                off = int(max(0.0, (beat - b0) / bps))
                if pad_idx < 0:
                    self._spawn_synth(
                        -pad_idx - 1,
                        vel,
                        min(max(0, round((beat - b0) / bps)), frames - 1),
                        max(1, int(_gate / bps)),
                        live_trigger=False,
                    )
                elif pad_idx >= NPADS:
                    index, pitch = divmod(pad_idx - NPADS, 128)
                    self._spawn(
                        proj.pads[index],
                        index,
                        vel,
                        min(max(0, round((beat - b0) / bps)), frames - 1),
                        max(1, int(_gate / bps)),
                        live_trigger=False,
                        sequence_id=sequence_id,
                        note=pitch,
                    )
                elif 0 <= pad_idx < len(proj.pads):
                    self._spawn(
                        proj.pads[pad_idx],
                        pad_idx,
                        vel,
                        min(off, frames - 1),
                        live_trigger=False,
                        sequence_id=sequence_id,
                    )
            for clip in audio:
                off = int(max(0.0, (clip.start_beat - b0) / bps))
                self._spawn_audio_clip(clip, min(off, frames - 1))
            if self.mode == "song" and self._resume_audio:
                for clip, elapsed in self._audio_overlaps(b0):
                    self._spawn_audio_clip(clip, 0, elapsed)
                self._resume_audio = False
            if self.metronome:
                for b in range(int(np.ceil(b0 - 1e-9)), int(np.ceil(b1 - 1e-9))):
                    self._click(int(max(0.0, (b - b0) / bps)), b % 4 == 0)
            self.beat = b1
        # 3 ─ voices
        self._schedule_arp(frames, start_beat)
        preview = None
        for v in self.voices:
            destination = preview_bus if v.pad_index in (AUDITION, METRONOME) else tbuf[v.track]
            v.render(destination, v.start_offset, self._pad_workspace)
            v.start_offset = 0
            if not v.dead:
                if v.pad_index == AUDITION:
                    preview = v
        for index in range(len(self.voices) - 1, -1, -1):
            if self.voices[index].dead:
                del self.voices[index]
        if preview is None:
            self.audition_time = None
        else:
            travelled = preview.age * preview.rate
            span = max(1, preview.s1 - preview.s0)
            if preview.loop:
                travelled %= span
            self.audition_time = (preview.s0 + travelled) / self.sr
        for voice in self.synth_voices:
            voice.render(tbuf[voice.track], proj.synth)
        for index in range(len(self.synth_voices) - 1, -1, -1):
            if self.synth_voices[index].dead:
                del self.synth_voices[index]

        # 4 ─ inserts → track buses → sends → master
        any_solo = False
        for track in proj.tracks:
            if track.solo:
                any_solo = True
                break
        rack = self.rack
        send_has_input = False
        for i, track in enumerate(proj.tracks):
            if (
                track.fx.sends_active
                and not (track.mute or (any_solo and not track.solo))
                and bool(np.any(tbuf[i]))
            ):
                send_has_input = True
                break
        if send_has_input:
            self._send_tail = int(SEND_TAIL * self.sr)
        else:
            self._send_tail = max(0, self._send_tail - frames)
        # Keep the sends running past the last note so their tails ring out
        # instead of being cut the moment a send knob reaches zero.
        run_sends = (send_has_input or self._send_tail > 0) and (
            proj.delay_fx.enabled or proj.reverb_fx.enabled
        )
        delay_send = reverb_send = None
        if run_sends:
            delay_send, reverb_send = rack.send_buffers(frames)

        if frames > len(self._bus):
            self._bus = np.zeros((frames, 2), dtype=np.float32)
        bus = self._bus[:frames]
        send_scratch = self._send_scratch[:frames]
        meter_scratch = self._meter_scratch[:frames]

        for i in range(NTRACKS):
            t = proj.tracks[i]
            left, right = self._track_controls(i, automation_beats)
            # Faders are post-insert. A zero fader must not reset filter or
            # compressor history before an automated fade opens again.
            g = 0.0 if (t.mute or (any_solo and not t.solo)) else 1.0
            buf = tbuf[i]
            has_input = g > 0.0 and bool(np.any(buf))
            if g <= 0.0:
                # Muting must drain existing DSP state with silence, not keep
                # feeding hidden voices into the effect chain.
                buf.fill(0.0)
            tailing = self._track_tail[i] > 0
            # Stateful DSP consumes a finite run of silent blocks, then sleeps.
            if t.fx.active and (has_input or tailing):
                rack.tracks[i].process(buf, t.fx, prepared_only=True)
                if has_input:
                    self._track_tail[i] = int(TRACK_DSP_TAIL * self.sr)
                else:
                    self._track_tail[i] = max(0, self._track_tail[i] - frames)
            elif not t.fx.active:
                self._track_tail[i] = 0
            if g <= 0.0:
                self.meters[i] = 0.0
                self.peaks[i] = 0.0
                continue
            np.multiply(buf[:, 0], left, out=bus[:, 0])
            np.multiply(buf[:, 1], right, out=bus[:, 1])
            np.square(bus, out=meter_scratch)
            self.meters[i] = float(np.sqrt(np.mean(meter_scratch)))
            np.abs(bus, out=meter_scratch)
            self.peaks[i] = float(np.max(meter_scratch))
            master += bus
            if run_sends and t.fx.sends_active:
                if t.fx.send_delay > 1e-4:
                    np.multiply(bus, np.float32(t.fx.send_delay), out=send_scratch)
                    np.add(delay_send, send_scratch, out=delay_send)
                if t.fx.send_reverb > 1e-4:
                    np.multiply(bus, np.float32(t.fx.send_reverb), out=send_scratch)
                    np.add(reverb_send, send_scratch, out=reverb_send)

        if run_sends:
            if proj.delay_fx.enabled:
                master += rack.delay.process(delay_send, proj.delay_fx, proj.bpm)
            if proj.reverb_fx.enabled:
                master += rack.reverb.process(reverb_send, proj.reverb_fx)

        # Master tone and glue sit ahead of the fader, so riding the fader
        # never changes how hard the bus compressor is working.
        if proj.master_fx.active:
            rack.master.process(master, proj.master_fx, prepared_only=True)
        master_gain = (
            proj.master
            if automation_beats is None
            else automation_values(proj, "master", automation_beats, proj.master)
        )
        master *= master_gain[:, None] if isinstance(master_gain, np.ndarray) else master_gain
        # Browser/CHOP preview is an independent cue bus: Track 1 mute, pan and
        # insert choices must never make a source audition disappear or change.
        np.multiply(preview_bus, np.float32(self.preview_gain), out=send_scratch)
        np.add(master, send_scratch, out=master)
        # The dry microphone cue intentionally bypasses project inserts and
        # the master fader, like a studio interface's monitor path. Consume
        # only the freshest block so recovery from an xrun cannot echo stale
        # speech seconds later.
        if monitor is not None and len(monitor):
            take = min(frames, len(monitor))
            np.add(master[:take], monitor[:take], out=master[:take])
        self.mastering.process(master)

        np.square(master, out=meter_scratch)
        self.master_meter[0] = float(np.sqrt(np.mean(meter_scratch[:, 0])))
        self.master_meter[1] = float(np.sqrt(np.mean(meter_scratch[:, 1])))
        np.abs(master, out=meter_scratch)
        self.master_peak = float(np.max(meter_scratch))
        outdata[:] = master

    # ── offline bounce ───────────────────────────────────────
    def _render_offline_reference(
        self, mode: str = "song", repeats: int = 1, tail: float = 2.5, progress=None
    ) -> np.ndarray:
        """Original whole-session renderer retained for equivalence tests."""
        # Offline rendering is allowed to load assets; the live callback is not.
        self.preload_project_audio()
        proj = self.project
        spb = 60.0 / proj.bpm
        if mode == "song":
            length_beats = proj.song_end()
        else:
            length_beats = proj.pattern().length_beats * max(1, repeats)
        if length_beats <= 0:
            raise ValueError("nothing to render — place some clips or steps first")

        total = int((length_beats * spb + tail) * self.sr)
        out = np.zeros((total, 2), dtype=np.float32)
        tbuf = np.zeros((NTRACKS, total, 2), dtype=np.float32)

        saved_mode, self.mode = self.mode, mode
        try:
            notes, audio = self._collect(0.0, length_beats)
            voices: list[tuple[int, PadVoice]] = []
            for event in notes:
                item = self._offline_pad_event(*event, spb)
                if item is not None:
                    voices.append(item)
            for clip in audio:
                data, s0, s1 = self._audio_clip_source(clip)
                if data is None:
                    continue
                if s1 - s0 < 8:
                    continue
                arranged = max(1, int(clip.length_beats * spb * self.sr))
                voice_length = arranged if clip.loop else min(arranged, s1 - s0)
                pl, pr = _balance_gains(0.0)
                voices.append(
                    (
                        int(clip.start_beat * spb * self.sr),
                        PadVoice(
                            data=data,
                            source_id=clip.ref,
                            s0=s0,
                            s1=s1,
                            rate=1.0,
                            gain=float(clip.gain),
                            pan_l=pl,
                            pan_r=pr,
                            attack=max(1, int(0.003 * self.sr)),
                            release=max(1, int(0.008 * self.sr)),
                            length=voice_length,
                            track=max(0, min(NTRACKS - 1, clip.track)),
                            choke=0,
                            pad_index=-1,
                            loop=bool(clip.loop),
                            loop_crossfade=max(0, int(clip.loop_crossfade * self.sr)),
                            quality="offline",
                        ),
                    )
                )

            # Apply the same choke/source-stealing decisions as the live
            # callback before rendering the voices independently. Without
            # this pass a bounced chop could overlap even though live playback
            # cut correctly.
            voices.sort(key=lambda item: item[0])
            fade = max(1, int(FADE * self.sr))
            for index, (at, voice) in enumerate(voices):
                if not (0 <= voice.pad_index < len(proj.pads)):
                    continue
                pad = proj.pads[voice.pad_index]
                for previous_at, previous in voices[:index]:
                    if self._pad_trigger_cuts(previous, voice, pad, voice.pad_index):
                        elapsed = max(0, at - previous_at)
                        previous.length = min(previous.length, elapsed + fade)
                        previous.release = fade

            # A whole-song PadRenderWorkspace costs 68 bytes per frame. Render
            # voices through one cache-sized workspace instead of allocating
            # gigabytes of temporary arrays for every long clip or loop.
            voice_workspace = PadRenderWorkspace(min(OFFLINE_VOICE_CHUNK, max(1, total)))
            for n, (at, v) in enumerate(voices):
                room = total - at
                if room <= 0:
                    continue
                v.length = min(v.length, room)
                rendered = 0
                while rendered < room and not v.dead:
                    take = min(OFFLINE_VOICE_CHUNK, room - rendered)
                    start = at + rendered
                    v.render(tbuf[v.track][start : start + take], 0, voice_workspace)
                    rendered += take
                if progress and n % 32 == 0:
                    progress(n / max(1, len(voices)))

            # The mix runs block by block through a rack of its own, so an
            # export hears exactly what the live callback hears — same insert
            # chains, same send tails, same glue — without disturbing the
            # effect state the running engine is using.
            any_solo = proj.any_solo()
            gains = []
            for i in range(NTRACKS):
                t = proj.tracks[i]
                g = 0.0 if (t.mute or (any_solo and not t.solo)) else t.gain
                pl, pr = _balance_gains(t.pan)
                gains.append((g * pl, g * pr, g > 0.0))

            rack = MixRack(NTRACKS)
            kernel = MasteringKernel(self.sr, blocksize=self.blocksize)
            run_sends = any(t.fx.sends_active for t in proj.tracks) and (
                proj.delay_fx.enabled or proj.reverb_fx.enabled
            )
            for start in range(0, total, self.blocksize):
                stop = min(total, start + self.blocksize)
                n = stop - start
                block = out[start:stop]
                delay_send = reverb_send = None
                if run_sends:
                    delay_send, reverb_send = rack.send_buffers(n)
                for i in range(NTRACKS):
                    left, right, live = gains[i]
                    if not live:
                        continue
                    buf = tbuf[i][start:stop]
                    fx = proj.tracks[i].fx
                    if fx.active:
                        rack.tracks[i].process(buf, fx)
                    block[:, 0] += buf[:, 0] * left
                    block[:, 1] += buf[:, 1] * right
                    if run_sends and fx.sends_active:
                        panned = np.empty_like(buf)
                        panned[:, 0] = buf[:, 0] * left
                        panned[:, 1] = buf[:, 1] * right
                        if fx.send_delay > 1e-4:
                            delay_send += panned * np.float32(fx.send_delay)
                        if fx.send_reverb > 1e-4:
                            reverb_send += panned * np.float32(fx.send_reverb)
                if run_sends:
                    if proj.delay_fx.enabled:
                        block += rack.delay.process(delay_send, proj.delay_fx, proj.bpm)
                    if proj.reverb_fx.enabled:
                        block += rack.reverb.process(reverb_send, proj.reverb_fx)
                if proj.master_fx.active:
                    rack.master.process(block, proj.master_fx)
                block *= proj.master
                kernel.process(block)
        finally:
            self.mode = saved_mode
        if progress:
            progress(1.0)
        return out

    def _offline_pad_event(self, beat, index, velocity, gate, sequence_id, spb):
        pitch = None
        if index >= NPADS:
            index, pitch = divmod(index - NPADS, 128)
        if not 0 <= index < len(self.project.pads):
            return None
        pad = self.project.pads[index]
        voice = self._voice_for_pad(pad, velocity, pitch)
        if voice is None:
            return None
        voice.pad_index, voice.sequence_id = index, sequence_id
        voice.quality = "offline"
        if pitch is not None and voice.gated:
            voice.length = min(voice.length, max(1, int(gate * spb * self.sr)) + voice.release)
        frame = round(beat * spb * self.sr) if pitch is not None else int(beat * spb * self.sr)
        return frame, voice

    def _offline_frame_count(self, mode: str, repeats: int, tail: float) -> int:
        proj = self.project
        spb = 60.0 / proj.bpm
        length_beats = (
            proj.song_end() if mode == "song" else proj.pattern().length_beats * max(1, repeats)
        )
        if length_beats <= 0:
            raise ValueError("nothing to render — place some clips or steps first")
        return int((length_beats * spb + tail) * self.sr)

    def iter_offline_blocks(
        self, mode: str = "song", repeats: int = 1, tail: float = 2.5, progress=None
    ):
        """Yield a mixdown in bounded float32 blocks.

        The caller must consume or copy each block before requesting the next.
        Each yielded value is already an independent array, so file writers can
        pass it straight to ``SoundFile.write``. Memory is bounded by events,
        active voices and ``NTRACKS * blocksize`` rather than song duration.
        """
        self.preload_project_audio()
        proj = self.project
        spb = 60.0 / proj.bpm
        length_beats = (
            proj.song_end() if mode == "song" else proj.pattern().length_beats * max(1, repeats)
        )
        total = self._offline_frame_count(mode, repeats, tail)

        saved_mode, self.mode = self.mode, mode
        try:
            notes, audio = self._collect(0.0, length_beats)
            synth_events = sorted(
                [
                    (round(beat * spb * self.sr), -idx - 1, vel, max(1, int(gate * spb * self.sr)))
                    for beat, idx, vel, gate, _sequence_id in notes
                    if idx < 0
                ],
                key=lambda event: event[0],
            )
            synth_voices = []
            next_synth = 0
            voices: list[tuple[int, PadVoice]] = []
            for event in notes:
                item = self._offline_pad_event(*event, spb)
                if item is not None:
                    voices.append(item)
            for clip in audio:
                data, s0, s1 = self._audio_clip_source(clip)
                if data is None or s1 - s0 < 8:
                    continue
                arranged = max(1, int(clip.length_beats * spb * self.sr))
                voice_length = arranged if clip.loop else min(arranged, s1 - s0)
                pl, pr = _balance_gains(0.0)
                voices.append(
                    (
                        int(clip.start_beat * spb * self.sr),
                        PadVoice(
                            data=data,
                            source_id=clip.ref,
                            s0=s0,
                            s1=s1,
                            rate=1.0,
                            gain=float(clip.gain),
                            pan_l=pl,
                            pan_r=pr,
                            attack=max(1, int(0.003 * self.sr)),
                            release=max(1, int(0.008 * self.sr)),
                            length=voice_length,
                            track=max(0, min(NTRACKS - 1, clip.track)),
                            choke=0,
                            pad_index=-1,
                            loop=bool(clip.loop),
                            loop_crossfade=max(0, int(clip.loop_crossfade * self.sr)),
                            quality="offline",
                        ),
                    )
                )

            voices.sort(key=lambda item: item[0])
            fade = max(1, int(FADE * self.sr))
            for index, (at, voice) in enumerate(voices):
                if not (0 <= voice.pad_index < len(proj.pads)):
                    continue
                pad = proj.pads[voice.pad_index]
                for previous_index in range(index):
                    previous_at, previous = voices[previous_index]
                    if self._pad_trigger_cuts(previous, voice, pad, voice.pad_index):
                        elapsed = max(0, at - previous_at)
                        previous.length = min(previous.length, elapsed + fade)
                        previous.release = fade

            blocksize = self.blocksize
            tbuf = np.zeros((NTRACKS, blocksize, 2), dtype=np.float32)
            output = np.zeros((blocksize, 2), dtype=np.float32)
            panned = np.zeros((blocksize, 2), dtype=np.float32)
            voice_workspace = PadRenderWorkspace(blocksize)
            active: list[tuple[int, PadVoice]] = []
            next_voice = 0

            any_solo = proj.any_solo()
            rack = MixRack(NTRACKS)
            rack.prepare(blocksize)
            kernel = MasteringKernel(self.sr, blocksize=blocksize)
            run_sends = any(t.fx.sends_active for t in proj.tracks) and (
                proj.delay_fx.enabled or proj.reverb_fx.enabled
            )

            for start in range(0, total, blocksize):
                stop = min(total, start + blocksize)
                n = stop - start
                tracks = tbuf[:, :n]
                tracks.fill(0.0)
                block = output[:n]
                block.fill(0.0)

                while next_voice < len(voices) and voices[next_voice][0] < stop:
                    at, voice = voices[next_voice]
                    voice.length = min(voice.length, total - at)
                    active.append((at, voice))
                    next_voice += 1
                for at, voice in active:
                    offset = max(0, at - start)
                    voice.render(tracks[voice.track], offset, voice_workspace)
                for index in range(len(active) - 1, -1, -1):
                    if active[index][1].dead:
                        del active[index]

                while next_synth < len(synth_events) and synth_events[next_synth][0] < stop:
                    at, pitch, velocity, gate = synth_events[next_synth]
                    self._spawn_synth(
                        pitch,
                        velocity,
                        max(0, at - start),
                        gate,
                        voices=synth_voices,
                        live_trigger=False,
                    )
                    next_synth += 1
                for voice in synth_voices:
                    voice.render(tracks[voice.track], proj.synth)
                synth_voices[:] = [v for v in synth_voices if not v.dead]
                automation_beats = self._automation_beats(mode, start / (spb * self.sr), n)
                delay_send = reverb_send = None
                if run_sends:
                    delay_send, reverb_send = rack.send_buffers(n)
                for i in range(NTRACKS):
                    track = proj.tracks[i]
                    left, right = self._track_controls(i, automation_beats)
                    if track.mute or (any_solo and not track.solo):
                        continue
                    buf = tracks[i]
                    fx = proj.tracks[i].fx
                    if fx.active:
                        rack.tracks[i].process(buf, fx)
                    np.multiply(buf[:, 0], left, out=panned[:n, 0])
                    np.multiply(buf[:, 1], right, out=panned[:n, 1])
                    np.add(block, panned[:n], out=block)
                    if run_sends and fx.sends_active:
                        if fx.send_delay > 1e-4:
                            delay_send += panned[:n] * np.float32(fx.send_delay)
                        if fx.send_reverb > 1e-4:
                            reverb_send += panned[:n] * np.float32(fx.send_reverb)
                if run_sends:
                    if proj.delay_fx.enabled:
                        block += rack.delay.process(delay_send, proj.delay_fx, proj.bpm)
                    if proj.reverb_fx.enabled:
                        block += rack.reverb.process(reverb_send, proj.reverb_fx)
                if proj.master_fx.active:
                    rack.master.process(block, proj.master_fx)
                gain = (
                    proj.master
                    if automation_beats is None
                    else automation_values(proj, "master", automation_beats, proj.master)
                )
                block *= gain[:, None] if isinstance(gain, np.ndarray) else gain
                kernel.process(block)
                if progress:
                    progress(stop / max(1, total))
                yield block.copy()
        finally:
            self.mode = saved_mode
        if progress:
            progress(1.0)

    def render_offline(
        self, mode: str = "song", repeats: int = 1, tail: float = 2.5, progress=None
    ) -> np.ndarray:
        """Render to memory for API callers; file export uses the block iterator."""
        blocks = list(
            self.iter_offline_blocks(mode=mode, repeats=repeats, tail=tail, progress=progress)
        )
        return np.concatenate(blocks, axis=0)
