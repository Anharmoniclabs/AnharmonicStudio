"""Shared realtime engine coordinator and compatibility API.

A render worker owns playback state and calls portable C++ DSP kernels. The
production PortAudio callback consumes a bounded native FIFO without entering
Python. Shared modules preserve event scheduling, mixing and offline export;
this coordinator owns commands, devices and transport boundaries.

The GUI posts voice commands to a queue. Direct/offline _callback callers retain
the synchronous reference contract used by the regression and benchmark tools.
"""

from __future__ import annotations

import queue
import time
from typing import Any

import numpy as np

from . import engine_mixing, engine_offline, engine_scheduling

from .audio_kernel import (
    AUDIO_SAMPLE_RATE,
    DEFAULT_BLOCKSIZE,
    ULTRA_LOW_LATENCY_BLOCKSIZE,
    MasteringKernel,
)
from .fx import MasterChain, MixRack, TrackChain
from .linux_audio import LinuxAudioRuntime
from .model import Project, Pad, NPADS
from .music import Note
from .synth import ArpState, SynthVoice
from .orchestra import prepare_patch
from .external_dsp import ExternalDSP
from .native_dsp import NATIVE
from .native_output import NativeOutputStream

# Preserve imports used by extensions and tests during the modular transition.
from .sample_voice import (
    PadRenderWorkspace as PadRenderWorkspace,
    PadVoice as PadVoice,
    _pan_gains as _pan_gains,
    _balance_gains as _balance_gains,
)

from .engine_constants import (
    FADE as FADE,
    MAX_SYNTH_VOICES as MAX_SYNTH_VOICES,
    MAX_PAD_VOICES as MAX_PAD_VOICES,
    AUDITION as AUDITION,
    METRONOME as METRONOME,
    CALLBACK_HISTORY as CALLBACK_HISTORY,
    SEND_TAIL as SEND_TAIL,
    TRACK_DSP_TAIL as TRACK_DSP_TAIL,
    OFFLINE_VOICE_CHUNK as OFFLINE_VOICE_CHUNK,
)

_CONFIGURED_DEVICE = object()


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
        self._native_output_active = False
        self.cmds: "queue.SimpleQueue[tuple]" = queue.SimpleQueue()
        self.voices: list[PadVoice] = []
        self.synth_voices: list[SynthVoice] = []
        self._synth_variants = {}
        self._variant_patch = None
        self.arp_state = ArpState()
        self._arp_samples_until = 0.0
        # Song note takes receive the same generated notes as the live arp.
        self.arp_note_capture: tuple[list[Note], float] | None = None
        self.external = ExternalDSP()

        # transport
        self.playing = False
        self.mode = "pattern"  # pattern | song
        self.beat = 0.0
        self.metronome = False
        self.recording = False
        self.loop_song = False
        self._resume_audio = True

        # feedback for the GUI (written by the audio thread, read by the GUI)
        self.meters = np.zeros(len(self.project.tracks), dtype=np.float32)
        self.peaks = np.zeros(len(self.project.tracks), dtype=np.float32)
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

        self._tbuf = np.zeros((len(self.project.tracks), blocksize, 2), dtype=np.float32)
        self._master = np.zeros((blocksize, 2), dtype=np.float32)
        self._bus = np.zeros((blocksize, 2), dtype=np.float32)
        self._preview = np.zeros((blocksize, 2), dtype=np.float32)
        self._send_scratch = np.zeros((blocksize, 2), dtype=np.float32)
        self._meter_scratch = np.zeros((blocksize, 2), dtype=np.float32)
        self._native_meter = np.zeros(2, dtype=np.float64)
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
        self.rack = MixRack(len(self.project.tracks))
        self.rack.prepare(blocksize)
        # GUI-side request keys suppress duplicate tone builds when a non-EQ
        # control moves. Prepared kernels themselves cross to the callback via
        # ``cmds``; GUI code never mutates an installed convolver.
        self._track_tone_requests: list[tuple | None] = [None] * len(self.project.tracks)
        self._master_tone_request: tuple | None = None
        self._send_tail = 0
        self._track_tail = np.zeros(len(self.project.tracks), dtype=np.int64)
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
        if not 0 <= index < len(self.rack.tracks):
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

    def configure_tracks(self, project: Project | None = None) -> None:
        """Prepare a changed mixer layout while this engine's stream is stopped.

        Callers publish the project and call prepare_fx before restarting audio.
        No callback grows mixer arrays or silently maps a new track to slot eight.
        """
        proj = project or self.project
        proj._validate_track_ids()
        proj._validate_track_references()
        count = len(proj.tracks)
        if count == len(self.rack.tracks):
            return
        if self.stream is not None or self._live_starting:
            raise RuntimeError("stop audio before changing the mixer track count")
        # Prepare complete replacement storage before replacing engine state.
        buffers = np.zeros((count, self.blocksize, 2), dtype=np.float32)
        meters = np.zeros(count, dtype=np.float32)
        peaks = np.zeros(count, dtype=np.float32)
        tails = np.zeros(count, dtype=np.int64)
        rack = MixRack(count)
        rack.prepare(self.blocksize)
        self.voices.clear()
        self.synth_voices.clear()
        self._tbuf, self.meters, self.peaks = buffers, meters, peaks
        self._track_tail, self.rack = tails, rack
        self._track_tone_requests = [None] * count
        self._master_tone_request = None
        self._send_tail = 0
        self._lock_realtime_working_set()

    def prepare_fx(self, project: Project | None = None) -> None:
        """Prepare every project tone kernel before live processing needs it."""
        proj = project or self.project
        self.configure_tracks(proj)
        for index, track in enumerate(proj.tracks):
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
        native_output = NATIVE is not None and hasattr(sd, "_StreamBase")
        stream_factory = (
            (lambda **options: NativeOutputStream(sd, NATIVE, **options))
            if native_output
            else sd.OutputStream
        )
        stream = stream_factory(
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
        self._native_output_active = native_output
        try:
            self._live_starting = True
            stream.start()
        except Exception:
            try:
                stream.close()
            except Exception:
                pass
            self._native_output_active = False
            raise
        finally:
            self._live_starting = False
        self.stream = stream
        self.output_device = output_device
        if native_output:
            self.linux_audio.status.scheduler = "host-managed C++ callback"

    def stop(self) -> None:
        if self.stream is not None:
            stream, self.stream = self.stream, None
            try:
                stream.stop()
            finally:
                try:
                    stream.close()
                finally:
                    self._native_output_active = False

    def configure_blocksize(self, frames: int) -> None:
        """Prepare a fixed callback size while the stream is stopped."""
        frames = int(frames)
        if frames < 64 or frames > 4096 or frames & (frames - 1):
            raise ValueError("audio buffer must be a power of two from 64 to 4096")
        if self.stream is not None:
            raise RuntimeError("stop audio before changing its buffer")
        self.configure_tracks(self.project)
        self.blocksize = frames
        self.voices.clear()
        self.synth_voices.clear()
        self.arp_state.held.clear()
        self._tbuf = np.zeros((len(self.project.tracks), frames, 2), dtype=np.float32)
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
        self._track_tone_requests = [None] * len(self.project.tracks)
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
        xruns = self.underruns + getattr(self.stream, "underruns", 0)
        n = self._cb_filled
        if not n:
            return {
                "p50": 0.0,
                "p99": 0.0,
                "max": 0.0,
                "period": period,
                "headroom": 1.0,
                "xruns": xruns,
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
            "xruns": xruns,
            "blocks": n,
        }

    def reset_timing(self) -> None:
        """Forget the timing window and the xrun count — call after changing
        block size, so the new setting is judged on its own numbers."""
        self._cb_i = 0
        self._cb_filled = 0
        self._cb_times.fill(0.0)
        self.underruns = 0
        if getattr(self.stream, "native_callback", False):
            self.stream.reset_timing()

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
            track=self.project.validate_track_index(pad.track, "pad output"),
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
        if voices is self.synth_voices and self.external.instrument is not None:
            self.external.note_on(note, velocity, offset, gate_frames, live_trigger)
            return
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
                track=self.project.validate_track_index(patch.track, "synth output"),
                start_offset=max(0, offset),
                gate_frames=gate_frames,
                variant=variant,
                live_trigger=live_trigger,
            )
        )

    def _release_synth(self, note: int) -> None:
        if self.external.instrument is not None:
            self.external.note_off(note)
        for voice in self.synth_voices:
            if voice.note == note and voice.live_trigger and not voice.dead:
                voice.note_off(self.project.synth.release)

    def _schedule_arp(self, frames: int, start_beat: float) -> None:
        return engine_scheduling.schedule_arp(self, frames, start_beat)

    # ── sequencing ───────────────────────────────────────────
    def _swing_offset(self, pat, step: int) -> float:
        return engine_scheduling.swing_offset(self, pat, step)

    def _pattern_events(
        self, pat, b0: float, b1: float, origin: float, limit: float, out: list, sequence_id: str
    ) -> None:
        return engine_scheduling.pattern_events(self, pat, b0, b1, origin, limit, out, sequence_id)

    def _collect(self, b0: float, b1: float, *, reuse: bool = False) -> tuple[list, list]:
        return engine_scheduling.collect(self, b0, b1, reuse=reuse)

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
                track=self.project.validate_track_index(clip.track, "clip output"),
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
        yield from engine_scheduling.audio_overlaps(self, beat)

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
        return engine_scheduling.record(self, pad_index, velocity)

    # ── the callback ─────────────────────────────────────────
    def _automation_beats(self, mode, start, frames):
        return engine_mixing.automation_beats(self, mode, start, frames)

    def _track_controls(self, index, beats):
        return engine_mixing.track_controls(self, index, beats)

    def _callback(self, outdata, frames, time_info, status) -> None:
        t_start = time.perf_counter()
        if not self._native_output_active and (self._live_starting or self.stream is not None):
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
            if kind in ("panic", "synthpanic", "stopt", "seek"):
                self.external.panic()
            if kind == "midiexpression":
                if self.external.instrument is not None:
                    self.external.events.append((cmd[1], 0))
            elif kind == "pad":
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
        return engine_mixing.render_block(self, outdata, frames, monitor)

    # ── offline bounce ───────────────────────────────────────
    def _render_offline_reference(
        self, mode: str = "song", repeats: int = 1, tail: float = 2.5, progress=None
    ) -> np.ndarray:
        """Original whole-session renderer retained for equivalence tests."""
        return engine_offline.render_offline_reference(
            self, mode, repeats, tail, progress, voice_chunk=OFFLINE_VOICE_CHUNK
        )

    def _offline_pad_event(self, beat, index, velocity, gate, sequence_id, spb):
        return engine_offline.offline_pad_event(self, beat, index, velocity, gate, sequence_id, spb)

    def _offline_frame_count(self, mode: str, repeats: int, tail: float) -> int:
        return engine_offline.offline_frame_count(self, mode, repeats, tail)

    def iter_offline_blocks(
        self, mode: str = "song", repeats: int = 1, tail: float = 2.5, progress=None
    ):
        """Yield a mixdown in bounded float32 blocks.

        The caller must consume or copy each block before requesting the next.
        Each yielded value is already an independent array, so file writers can
        pass it straight to ``SoundFile.write``. Memory is bounded by events,
        active voices and ``len(project.tracks) * blocksize`` rather than song duration."""
        yield from engine_offline.iter_offline_blocks(self, mode, repeats, tail, progress)

    def render_offline(
        self, mode: str = "song", repeats: int = 1, tail: float = 2.5, progress=None
    ) -> np.ndarray:
        """Render to memory for API callers; file export uses the block iterator."""
        return engine_offline.render_offline(self, mode, repeats, tail, progress)
