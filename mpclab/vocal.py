"""Microphone capture and offline vocal pitch correction.

The live callback only copies input into bounded chunks and updates meters.
Pitch analysis and rendering are intentionally offline: a corrected take is a
new library item while the original recording remains available at all times.
"""

from __future__ import annotations

import json
import os
import queue
import threading
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import numpy as np
import soundfile as sf

from .audio_kernel import AUDIO_SAMPLE_RATE
from .audio_storage import read_stereo
from .fx import BlockConvolver, _biquad, cascade_ir
from .model import VocalSettings

NOTE_NAMES = ("C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B")
SCALES = {
    "chromatic": tuple(range(12)),
    "major": (0, 2, 4, 5, 7, 9, 11),
    "minor": (0, 2, 3, 5, 7, 8, 10),
    "pentatonic": (0, 2, 4, 7, 9),
}


def input_device_inventory() -> tuple[list[dict], int | None]:
    """Return usable PortAudio inputs and the current default input index."""
    import sounddevice as sd

    devices = sd.query_devices()
    hostapis = sd.query_hostapis()
    raw_default = sd.default.device
    try:
        default_input = int(raw_default[0])
    except (IndexError, TypeError):
        try:
            default_input = int(raw_default)
        except (TypeError, ValueError):
            default_input = None
    if default_input is not None and default_input < 0:
        default_input = None

    inputs = []
    for index, device in enumerate(devices):
        channels = int(device.get("max_input_channels", 0) or 0)
        if channels < 1:
            continue
        host_index = int(device.get("hostapi", -1) or 0)
        try:
            host = str(hostapis[host_index].get("name", "PortAudio"))
        except (IndexError, TypeError):
            host = "PortAudio"
        name = str(device.get("name", f"Input {index}"))
        inputs.append(
            {
                "index": index,
                "name": name,
                "host": host,
                "channels": channels,
                "label": f"{name}  ·  {host}",
                "key": json.dumps([host, name], ensure_ascii=False),
            }
        )
    return inputs, default_input


class VocalRecorder:
    """Stream microphone blocks to a bounded, temporary 24-bit WAV.

    PortAudio's callback never performs filesystem I/O and capture memory is
    bounded by ``queue_blocks``. A worker drains the queue into a temporary
    WAV; :meth:`stop` decodes the take with bounded heap storage. The original
    spool is kept until the caller explicitly commits or discards the take.
    """

    def __init__(
        self,
        sample_rate: int = AUDIO_SAMPLE_RATE,
        blocksize: int = 256,
        queue_blocks: int = 256,
        temp_dir: str | Path | None = None,
    ):
        self.sample_rate = int(sample_rate)
        self.blocksize = int(blocksize)
        self.queue_blocks = max(2, int(queue_blocks))
        self.temp_dir = Path(temp_dir) if temp_dir is not None else None
        self.stream = None
        self._queue: queue.Queue[tuple[int, np.ndarray]] | None = None
        self._writer: threading.Thread | None = None
        self._writer_stop = threading.Event()
        self._writer_error: BaseException | None = None
        self._temp_path: Path | None = None
        self._captured_frames = 0
        self.input_rms = 0.0
        self.input_peak = 0.0
        self.overruns = 0
        self.paused = False
        self.dropped_frames = 0
        self.monitor_errors = 0
        self.monitor_callback: Callable[[np.ndarray], None] | None = None
        self._gain = 1.0

    @property
    def recording(self) -> bool:
        return self.stream is not None

    @property
    def elapsed(self) -> float:
        return self._captured_frames / self.sample_rate

    @property
    def temporary_path(self) -> Path | None:
        """Current spool path, exposed for diagnostics and cleanup tests."""
        return self._temp_path

    def _make_temp_path(self) -> Path:
        directory = str(self.temp_dir) if self.temp_dir is not None else None
        if self.temp_dir is not None:
            self.temp_dir.mkdir(parents=True, exist_ok=True)
        fd, raw_path = tempfile.mkstemp(prefix="mpclab-vocal-", suffix=".wav", dir=directory)
        os.close(fd)
        return Path(raw_path)

    def _write_capture(self) -> None:
        pending = self._queue
        path = self._temp_path
        if pending is None or path is None:
            return
        try:
            with sf.SoundFile(
                str(path), mode="w", samplerate=self.sample_rate, channels=2, subtype="PCM_24"
            ) as output:
                written = 0
                silence = np.zeros((max(1, self.blocksize), 2), dtype=np.float32)

                def fill_gap(end):
                    nonlocal written
                    while written < end:
                        count = min(len(silence), end - written)
                        output.write(silence[:count])
                        written += count

                while not self._writer_stop.is_set() or not pending.empty():
                    try:
                        position, block = pending.get(timeout=0.05)
                    except queue.Empty:
                        continue
                    try:
                        fill_gap(position)
                        output.write(block)
                        written += len(block)
                    finally:
                        pending.task_done()
                fill_gap(self._captured_frames)
                output.flush()
            with path.open("rb") as handle:
                os.fsync(handle.fileno())
        except Exception as exc:
            self._writer_error = exc

    def _finish_writer(self) -> None:
        self._writer_stop.set()
        writer = self._writer
        if writer is not None:
            writer.join(timeout=2.0)
            if writer.is_alive():
                raise RuntimeError(
                    f"Capture writer is still finishing; retry save. Recovery file: {self._temp_path}"
                )
        self._writer = None

    def _remove_temp(self) -> None:
        path, self._temp_path = self._temp_path, None
        if path is not None:
            path.unlink(missing_ok=True)

    def start(
        self,
        device: int | str | None = None,
        gain_db: float = 0.0,
        monitor_callback: Callable[[np.ndarray], None] | None = None,
    ) -> None:
        if self.recording:
            raise RuntimeError("a vocal take is already recording")
        if self._temp_path is not None:
            raise RuntimeError(f"Save or discard the previous take first: {self._temp_path}")
        import sounddevice as sd

        self._queue = queue.Queue(maxsize=self.queue_blocks)
        self._writer_stop.clear()
        self._writer_error = None
        self._captured_frames = 0
        self.overruns = 0
        self.dropped_frames = 0
        self.monitor_errors = 0
        self.input_rms = self.input_peak = 0.0
        self.paused = False
        self._gain = float(10.0 ** (float(gain_db) / 20.0))
        self.monitor_callback = monitor_callback
        self._temp_path = self._make_temp_path()
        self._writer = threading.Thread(
            target=self._write_capture, name="mpclab-vocal-writer", daemon=True
        )
        self._writer.start()

        def callback(indata, _frames, _time_info, status):
            if bool(status) and getattr(status, "input_overflow", True):
                self.overruns += 1
            mono = np.asarray(indata[:, 0], dtype=np.float32) * self._gain
            peak = float(np.max(np.abs(mono))) if len(mono) else 0.0
            rms = float(np.sqrt(np.mean(mono * mono))) if len(mono) else 0.0
            self.input_peak = max(peak, self.input_peak * 0.92)
            self.input_rms = rms
            stereo = np.ascontiguousarray(np.column_stack((mono, mono)), dtype=np.float32)
            if not self.paused:
                position = self._captured_frames
                self._captured_frames += len(stereo)
                try:
                    assert self._queue is not None
                    self._queue.put_nowait((position, stereo))
                except queue.Full:
                    # The writer fills this missing timeline range with silence.
                    self.overruns += 1
                    self.dropped_frames += len(stereo)
            if self.monitor_callback is not None:
                try:
                    self.monitor_callback(stereo)
                except Exception:
                    # A broken monitoring route must not terminate dry capture.
                    self.monitor_errors += 1

        try:
            stream = sd.InputStream(
                samplerate=self.sample_rate,
                blocksize=self.blocksize,
                channels=1,
                dtype="float32",
                device=device,
                latency="low",
                callback=callback,
            )
            stream.start()
        except Exception:
            try:
                if "stream" in locals():
                    stream.close()
            except Exception:
                pass
            self._finish_writer()
            self._queue = None
            self.monitor_callback = None
            if not self._captured_frames:
                self._remove_temp()
            raise
        self.stream = stream

    def stop(self) -> np.ndarray:
        return self._finalize(load_audio=True)

    def _finalize(self, load_audio: bool) -> np.ndarray:
        stream, self.stream = self.stream, None
        if stream is not None:
            try:
                stream.stop()
            except Exception:
                # A hot-unplug often makes PortAudio raise on stop. The audio
                # already captured in memory is still a valid take.
                pass
            try:
                stream.close()
            except Exception:
                pass
        self._finish_writer()
        path = self._temp_path
        writer_error = self._writer_error
        self._queue = None
        self.monitor_callback = None
        self.input_rms = 0.0
        if not load_audio:
            self._remove_temp()
            return np.zeros((0, 2), dtype=np.float32)
        if writer_error is not None:
            raise RuntimeError(
                f"temporary vocal capture failed; recovery file: {path}"
            ) from writer_error
        if path is None or self._captured_frames == 0:
            self._remove_temp()
            return np.zeros((0, 2), dtype=np.float32)
        try:
            audio, sample_rate = read_stereo(path, 16 * 1024 * 1024, path.parent)
            if sample_rate != self.sample_rate:
                raise RuntimeError(f"captured vocal sample rate changed to {sample_rate}")
            return audio
        except Exception as exc:
            raise RuntimeError(f"Could not read take; recovery file: {path}") from exc

    def commit(self) -> None:
        """Release the recovery WAV only after the library save succeeds."""
        if self.recording:
            raise RuntimeError("Cannot commit an active recording")
        self._remove_temp()

    def discard(self) -> None:
        self._finalize(load_audio=False)


class ProcessingCancelled(RuntimeError):
    """Raised cooperatively when an offline vocal job is cancelled."""


def _check_cancel(cancelled: Callable[[], bool] | None) -> None:
    if cancelled is not None and cancelled():
        raise ProcessingCancelled("vocal processing cancelled")


@dataclass
class PitchAnalysis:
    times: np.ndarray
    detected_hz: np.ndarray
    detected_midi: np.ndarray
    target_midi: np.ndarray
    confidence: np.ndarray

    @property
    def voiced_fraction(self) -> float:
        return float(np.mean(self.confidence > 0.35)) if len(self.confidence) else 0.0


def note_name(midi: float) -> str:
    if not np.isfinite(midi):
        return "—"
    note = int(round(float(midi)))
    return f"{NOTE_NAMES[note % 12]}{note // 12 - 1}"


def allowed_notes(settings: VocalSettings) -> np.ndarray:
    root = NOTE_NAMES.index(settings.key) if settings.key in NOTE_NAMES else 0
    intervals = SCALES.get(settings.scale, SCALES["chromatic"])
    notes = [
        midi
        for midi in range(int(settings.low_note), int(settings.high_note) + 1)
        if (midi - root) % 12 in intervals
    ]
    return np.asarray(notes or [60], dtype=np.float32)


def _pitch_frame(frame: np.ndarray, sr: int, low_hz: float, high_hz: float) -> tuple[float, float]:
    frame = np.asarray(frame, dtype=np.float32)
    frame = frame - float(np.mean(frame))
    rms = float(np.sqrt(np.mean(frame * frame)))
    if rms < 2e-4:
        return 0.0, 0.0
    frame *= np.hanning(len(frame)).astype(np.float32)
    size = 1 << int(np.ceil(np.log2(max(2, len(frame) * 2 - 1))))
    spec = np.fft.rfft(frame, size)
    ac = np.fft.irfft(spec * np.conj(spec), size)[: len(frame)].real
    if ac[0] <= 1e-10:
        return 0.0, 0.0
    lo = max(1, int(sr / max(high_hz, 1.0)))
    hi = min(len(ac) - 2, int(sr / max(low_hz, 1.0)))
    if hi <= lo:
        return 0.0, 0.0
    # Prefer the first strong periodic peak, avoiding common octave-down picks.
    band = ac[lo : hi + 1] / max(float(ac[0]), 1e-12)
    peaks = np.flatnonzero((band[1:-1] > band[:-2]) & (band[1:-1] >= band[2:])) + 1
    if not len(peaks):
        lag = lo + int(np.argmax(band))
    else:
        strengths = band[peaks]
        best = float(np.max(strengths))
        eligible = peaks[strengths >= max(0.28, best * 0.82)]
        lag = lo + int(eligible[0] if len(eligible) else peaks[np.argmax(strengths)])
    confidence = float(np.clip(ac[lag] / ac[0], 0.0, 1.0))
    if confidence < 0.18:
        return 0.0, confidence
    denom = ac[lag - 1] - 2.0 * ac[lag] + ac[lag + 1]
    delta = 0.5 * (ac[lag - 1] - ac[lag + 1]) / denom if abs(denom) > 1e-12 else 0.0
    return float(sr / (lag + np.clip(delta, -0.5, 0.5))), confidence


def analyze_pitch(
    audio: np.ndarray,
    settings: VocalSettings,
    sr: int = AUDIO_SAMPLE_RATE,
    frame_size: int = 2048,
    hop: int = 512,
    progress: Callable[[float], None] | None = None,
    cancelled: Callable[[], bool] | None = None,
) -> PitchAnalysis:
    """Track a monophonic vocal and quantize voiced frames to the chosen scale."""
    data = np.asarray(audio, dtype=np.float32)
    mono = data.mean(axis=1) if data.ndim == 2 else data.reshape(-1)
    if len(mono) < frame_size:
        mono = np.pad(mono, (0, frame_size - len(mono)))
    count = 1 + max(0, (len(mono) - frame_size) // hop)
    hz = np.zeros(count, dtype=np.float32)
    confidence = np.zeros(count, dtype=np.float32)
    low_hz = 440.0 * 2.0 ** ((settings.low_note - 69) / 12.0)
    high_hz = 440.0 * 2.0 ** ((settings.high_note - 69) / 12.0)
    for index in range(count):
        if index % 8 == 0:
            _check_cancel(cancelled)
            if progress:
                progress(index / max(1, count))
        start = index * hop
        hz[index], confidence[index] = _pitch_frame(
            mono[start : start + frame_size], sr, low_hz, high_hz
        )
    midi = np.full(count, np.nan, dtype=np.float32)
    voiced = hz > 0.0
    midi[voiced] = 69.0 + 12.0 * np.log2(hz[voiced] / 440.0)
    targets = midi.copy()
    choices = allowed_notes(settings)
    for index in np.flatnonzero(voiced):
        _check_cancel(cancelled)
        targets[index] = choices[int(np.argmin(np.abs(choices - midi[index])))] + int(
            settings.transpose
        )
    times = (np.arange(count, dtype=np.float32) * hop + frame_size * 0.5) / sr
    if progress:
        progress(1.0)
    return PitchAnalysis(times, hz, midi, targets, confidence)


def detect_key(analysis: PitchAnalysis) -> tuple[str, str, float]:
    """Suggest the major/minor key that best contains confident vocal notes."""
    voiced = np.isfinite(analysis.detected_midi) & (analysis.confidence >= 0.28)
    if not np.any(voiced):
        return "C", "major", 0.0
    pitch_classes = np.mod(np.rint(analysis.detected_midi[voiced]).astype(int), 12)
    weights = analysis.confidence[voiced]
    scores = []
    for scale_name in ("major", "minor"):
        intervals = set(SCALES[scale_name])
        for root in range(12):
            inside = np.asarray([((pc - root) % 12) in intervals for pc in pitch_classes])
            score = float(np.sum(weights[inside]) / max(np.sum(weights), 1e-9))
            # Tonic and fifth evidence break ties between relative keys.
            tonic = float(np.sum(weights[pitch_classes == root]))
            fifth = float(np.sum(weights[pitch_classes == (root + 7) % 12]))
            scores.append((score + 0.06 * tonic + 0.025 * fifth, root, scale_name))
    score, root, scale_name = max(scores)
    return NOTE_NAMES[root], scale_name, float(min(1.0, score))


def _tone_filter(
    audio: np.ndarray,
    settings: VocalSettings,
    blocksize: int = 2048,
    progress: Callable[[float], None] | None = None,
    cancelled: Callable[[], bool] | None = None,
) -> np.ndarray:
    sections = []
    if settings.highpass_hz > 22.0:
        sections.append(_biquad("highpass", settings.highpass_hz, 0.707))
    if settings.presence_db:
        sections.append(_biquad("peak", 4200.0, 0.85, settings.presence_db))
    if settings.deesser > 0.001:
        sections.append(_biquad("highshelf", 6200.0, 0.7, -10.0 * settings.deesser))
    if not sections:
        _check_cancel(cancelled)
        if progress:
            progress(1.0)
        return audio.copy()
    convolver = BlockConvolver(cascade_ir(sections, taps=768), 2)
    out = np.empty_like(audio)
    starts = range(0, len(audio), blocksize)
    total = max(1, (len(audio) + blocksize - 1) // blocksize)
    for index, start in enumerate(starts):
        _check_cancel(cancelled)
        block = audio[start : start + blocksize].copy()
        convolver.process(block)
        out[start : start + len(block)] = block
        if progress:
            progress((index + 1) / total)
    return out


def _dynamics(
    audio: np.ndarray,
    settings: VocalSettings,
    sr: int,
    progress: Callable[[float], None] | None = None,
    cancelled: Callable[[], bool] | None = None,
) -> np.ndarray:
    """Apply a transparent block-envelope gate and vocal compressor."""
    if not len(audio):
        if progress:
            progress(1.0)
        return audio.copy()
    block = max(64, int(sr * 0.01))
    starts = np.arange(0, len(audio), block)
    levels = np.empty(len(starts), dtype=np.float32)
    for index, start in enumerate(starts):
        if index % 8 == 0:
            _check_cancel(cancelled)
            if progress:
                progress(0.6 * index / max(1, len(starts)))
        levels[index] = np.sqrt(np.mean(audio[start : min(len(audio), start + block)] ** 2) + 1e-12)
    db = 20.0 * np.log10(np.maximum(levels, 1e-7))
    gate = np.clip((db - settings.gate_db) / 8.0, 0.0, 1.0)
    amount = float(np.clip(settings.compression, 0.0, 1.0))
    threshold = -12.0 - amount * 18.0
    ratio = 1.0 + amount * 5.0
    above = np.maximum(db - threshold, 0.0)
    gain_db = -above * (1.0 - 1.0 / ratio)
    control = gate * (10.0 ** (gain_db / 20.0))
    x = np.append(starts, len(audio) - 1)
    y = np.append(control, control[-1])
    envelope = np.interp(np.arange(len(audio)), x, y).astype(np.float32)
    _check_cancel(cancelled)
    if progress:
        progress(1.0)
    return audio * envelope[:, None]


def render_autotune(
    audio: np.ndarray,
    settings: VocalSettings,
    sr: int = AUDIO_SAMPLE_RATE,
    progress: Callable[[float], None] | None = None,
    cancelled: Callable[[], bool] | None = None,
) -> tuple[np.ndarray, PitchAnalysis]:
    """Render correction and cleanup, preserving length and stereo layout."""
    source = np.asarray(audio, dtype=np.float32)
    if source.ndim == 1:
        source = np.column_stack((source, source))
    if source.ndim != 2 or source.shape[1] not in (1, 2):
        raise ValueError("vocal audio must be mono or stereo")
    if source.shape[1] == 1:
        source = np.repeat(source, 2, axis=1)
    source = np.nan_to_num(source, copy=True)
    if not len(source):
        raise ValueError("vocal audio is empty")
    _check_cancel(cancelled)
    if progress:
        progress(0.0)
    analysis = analyze_pitch(
        source,
        settings,
        sr,
        progress=(lambda value: progress(0.32 * value)) if progress else None,
        cancelled=cancelled,
    )
    if progress:
        progress(0.32)

    # Long, overlapping grains are important here. Very short grains reset
    # their phase so often that a gentle one-semitone correction can average
    # back toward the original pitch; 170 ms grains retain vocal identity and
    # still update their target every 42 ms.
    frame_size, hop = 8192, 2048
    window = np.hanning(frame_size).astype(np.float32)
    corrected = np.zeros_like(source)
    weight = np.zeros(len(source), dtype=np.float32)
    ratio_state = 1.0
    retune_s = max(0.0, float(settings.retune_ms) / 1000.0)
    smoothing = 1.0 if retune_s <= 0 else 1.0 - np.exp(-(hop / sr) / retune_s)
    rel = np.arange(frame_size, dtype=np.float32) - frame_size * 0.5
    starts = np.arange(0, len(source), hop, dtype=np.int64)
    centers = starts + frame_size // 2
    padded = np.pad(source, ((frame_size * 2, frame_size * 2), (0, 0)), mode="reflect")
    for index, center in enumerate(centers):
        if index % 8 == 0:
            _check_cancel(cancelled)
            if progress:
                progress(0.32 + 0.48 * index / max(1, len(centers)))
        out_start = int(starts[index])
        analysis_index = int(
            np.clip(np.searchsorted(analysis.times, center / sr), 0, len(analysis.times) - 1)
        )
        if (
            settings.enabled
            and np.isfinite(analysis.detected_midi[analysis_index])
            and analysis.confidence[analysis_index] >= 0.18
        ):
            error = float(
                analysis.target_midi[analysis_index] - analysis.detected_midi[analysis_index]
            )
            # Humanize lets a stable held note keep some natural movement.
            depth = float(settings.strength) * (
                1.0
                - float(settings.humanize)
                * float(np.clip(analysis.confidence[analysis_index], 0.0, 1.0))
            )
            desired = 2.0 ** (error * depth / 12.0)
        else:
            desired = 1.0
        ratio_state += (desired - ratio_state) * smoothing
        ratio = float(np.clip(ratio_state, 0.49, 2.04))
        read = center + rel * ratio + frame_size * 2
        lo = np.floor(read).astype(np.int64)
        frac = (read - lo).astype(np.float32)
        frame = padded[lo] * (1.0 - frac[:, None]) + padded[lo + 1] * frac[:, None]
        take = min(frame_size, len(source) - out_start)
        if take <= 0:
            continue
        corrected[out_start : out_start + take] += frame[:take] * window[:take, None]
        weight[out_start : out_start + take] += window[:take]
    corrected /= np.maximum(weight[:, None], 1e-5)
    _check_cancel(cancelled)
    if progress:
        progress(0.80)

    # Preserve some of the original vocal body before the explicit wet/dry mix.
    body = float(np.clip(settings.formant, 0.0, 1.0)) * 0.16
    corrected = corrected * (1.0 - body) + source * body
    wet = float(np.clip(settings.mix, 0.0, 1.0))
    rendered = source * (1.0 - wet) + corrected * wet
    rendered = _tone_filter(
        rendered,
        settings,
        progress=(lambda value: progress(0.80 + 0.10 * value)) if progress else None,
        cancelled=cancelled,
    )
    _check_cancel(cancelled)
    if progress:
        progress(0.90)
    rendered = _dynamics(
        rendered,
        settings,
        sr,
        progress=(lambda value: progress(0.90 + 0.08 * value)) if progress else None,
        cancelled=cancelled,
    )
    rendered *= np.float32(10.0 ** (float(settings.output_db) / 20.0))
    peak = float(np.max(np.abs(rendered)))
    if peak > 0.98:
        rendered *= np.float32(0.98 / peak)
    _check_cancel(cancelled)
    if progress:
        progress(1.0)
    return np.ascontiguousarray(rendered, dtype=np.float32), analysis
