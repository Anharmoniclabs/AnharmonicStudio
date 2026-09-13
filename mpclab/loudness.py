"""Offline EBU R128/BS.1770 measurements using the shipped FFmpeg scanner.

References (accessed 2026-09-10):
https://ffmpeg.org/ffmpeg-filters.html#ebur128
https://tech.ebu.ch/docs/tech/tech3341.pdf

The unpadded loudness branch preserves incomplete-block gating semantics. A
separate, zero-extended true-peak branch flushes interpolation at the file end.
This is a file meter, not a live engine meter or an EBU certification claim.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from functools import lru_cache
import math
from pathlib import Path
import queue
import re
import subprocess
import threading
import time

from .audio_analysis import AnalysisCancelled, AudioAnalysisError, analyze_audio_file
from .runtime_paths import external_environment, media_tool, subprocess_options


MAX_HISTORY_POINTS = 10_000
MAX_LINE_BYTES = 16_384
MAX_DIAGNOSTIC_CHARS = 65_536
SUPPORTED_RATES = (44_100, 48_000, 88_200, 96_000)
MEASUREMENT_METHOD = "FFmpeg ebur128 / EBU R128 (BS.1770 K-weighting and gating)"
NUMBER = r"[+-]?(?:\d+(?:\.\d*)?|\.\d+|inf)"


def check_cancel(cancel):
    if cancel is not None and cancel.is_set():
        raise AnalysisCancelled()


def source_signature(path):
    info = Path(path).stat()
    return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns


def require_unchanged(path, signature):
    if source_signature(path) != signature:
        raise AudioAnalysisError("The source changed during processing; no output was published.")


def ffmpeg_path():
    executable = media_tool("ffmpeg")
    if not executable:
        raise AudioAnalysisError("FFmpeg is unavailable; loudness and true peak were not measured.")
    return executable


def run_ffmpeg(arguments, *, cancel=None, on_line=None, timeout=3600, executable=None):
    """Drain one bounded pipe, preserving cancellation even when a helper stalls.

    Only the precisely owned subprocess is terminated on failure/cancellation.
    No shell, network input, GUI, console window or playback device is used.
    """
    check_cancel(cancel)
    executable = executable or ffmpeg_path()
    command = [
        executable,
        "-hide_banner",
        "-nostdin",
        "-nostats",
        "-loglevel",
        "info",
        "-threads",
        "1",
        "-filter_threads",
        "1",
        "-filter_complex_threads",
        "1",
        *map(str, arguments),
    ]
    messages = queue.Queue(maxsize=64)
    stopped = threading.Event()

    def enqueue(item):
        while not stopped.is_set():
            try:
                messages.put(item, timeout=0.05)
                return
            except queue.Full:
                continue

    def read_pipe(pipe):
        try:
            while not stopped.is_set():
                raw = pipe.readline(MAX_LINE_BYTES + 1)
                if not raw:
                    break
                if len(raw) > MAX_LINE_BYTES:
                    enqueue(AudioAnalysisError("FFmpeg emitted an oversized diagnostic line."))
                    return
                enqueue(raw.decode("utf-8", errors="replace").rstrip("\r\n"))
        except (OSError, ValueError) as exc:
            if not stopped.is_set():
                enqueue(exc)
        finally:
            enqueue(None)

    tail = ""
    process = subprocess.Popen(
        command,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        env=external_environment(executable),
        **subprocess_options(),
    )
    reader = threading.Thread(target=read_pipe, args=(process.stdout,), daemon=True)
    deadline = time.monotonic() + timeout
    reader.start()
    try:
        ended = False
        while not ended:
            check_cancel(cancel)
            if time.monotonic() > deadline:
                raise AudioAnalysisError("FFmpeg processing timed out; no output was published.")
            try:
                line = messages.get(timeout=0.05)
            except queue.Empty:
                continue
            if line is None:
                ended = True
            elif isinstance(line, Exception):
                raise AudioAnalysisError(str(line)) from line
            else:
                tail = (tail + line + "\n")[-MAX_DIAGNOSTIC_CHARS:]
                if on_line:
                    on_line(line)
        while process.poll() is None:
            check_cancel(cancel)
            if time.monotonic() > deadline:
                raise AudioAnalysisError("FFmpeg processing timed out; no output was published.")
            stopped.wait(0.02)
        if process.returncode:
            raise AudioAnalysisError(
                f"FFmpeg processing failed ({process.returncode}): {tail[-2500:]}"
            )
        check_cancel(cancel)
        return tail
    finally:
        stopped.set()
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=2)
        reader.join(timeout=2)
        process.stdout.close()


@lru_cache(maxsize=4)
def _version(executable, modified_ns):
    del modified_ns  # Cache key invalidates when the bundled tool is updated.
    lines = run_ffmpeg(["-version"], executable=executable, timeout=10).splitlines()
    return next(
        (line for line in lines if line.startswith("ffmpeg version")),
        "FFmpeg (version unavailable)",
    )


@dataclass(frozen=True)
class LoudnessPoint:
    end_seconds: float
    momentary_lufs: float | None
    short_term_lufs: float | None
    integrated_lufs: float | None
    loudness_range_lu: float | None


@dataclass(frozen=True)
class LoudnessReport:
    method: str
    ffmpeg_version: str
    integrated_lufs: float | None
    loudness_range_lu: float | None
    lra_stable: bool
    momentary_max_lufs: float | None
    short_term_max_lufs: float | None
    true_peak_dbtp: float | None
    true_peak_over_windows: int
    first_true_peak_over_seconds: float | None
    history: tuple[LoudnessPoint, ...]
    history_step_seconds: float
    measured_windows: int
    notes: tuple[str, ...]
    true_peak_resolution_db: float = 0.1
    dual_mono: bool = False

    def to_dict(self):
        return asdict(self)

    def to_text(self):
        def reading(value, unit):
            return (
                "unavailable / below measurement gate" if value is None else f"{value:.1f} {unit}"
            )

        stable = "" if self.lra_stable else " (not yet stable: less than 60 s)"
        return (
            "\n".join(
                [
                    "LOUDNESS AND TRUE PEAK — OFFLINE FILE MEASUREMENT",
                    self.method,
                    f"Integrated: {reading(self.integrated_lufs, 'LUFS')}",
                    f"Loudness range: {reading(self.loudness_range_lu, 'LU')}{stable}",
                    f"Maximum observed momentary (400 ms): {reading(self.momentary_max_lufs, 'LUFS')}",
                    f"Maximum observed short-term (3 s): {reading(self.short_term_max_lufs, 'LUFS')}",
                    f"Maximum reconstructed true peak: {reading(self.true_peak_dbtp, 'dBTP')}",
                    f"100 ms true-peak windows above 0.0 dBTP: {self.true_peak_over_windows}",
                    f"History: {len(self.history):,} points / {self.measured_windows:,} measurements; "
                    f"stored interval {self.history_step_seconds:g} s",
                    *self.notes,
                ]
            )
            + "\n"
        )


def _finite_reading(value):
    result = float(value)
    return result if math.isfinite(result) else None


class _Scanner:
    """Streaming parser; history is uniformly decimated, maxima never are."""

    def __init__(self, duration, rate, progress=None):
        self.duration = duration
        self.rate = rate
        self.progress = progress
        self.last_progress = time.monotonic()
        self.values = {}
        self.frame_start = None
        self.history = []
        self.stride = 1
        self.count = 0
        self.last_point = None
        self.maximum_m = None
        self.maximum_s = None
        self.summary = {}
        self.summary_section = None
        self.over_windows = 0
        self.first_over = None
        self.true_peak_frames = 0

    def line(self, line):
        if "Summary:" in line:
            if "ebur128@truepeak" in line:
                self.summary_section = "truepeak"
            elif "ebur128@loudness" in line:
                self.summary_section = "loudness"
            else:
                self.summary_section = None
        if self.summary_section == "truepeak":
            match = re.search(rf"^\s+Peak:\s*({NUMBER})\s+dBFS", line)
            if match:
                self.summary["TP"] = _finite_reading(match[1])
        elif self.summary_section == "loudness":
            match = re.search(rf"^\s+(I|LRA):\s*({NUMBER})\s+LU", line)
            if match:
                self.summary[match[1]] = _finite_reading(match[2])
        if "ebur128@truepeak" in line and "FTPK:" in line:
            match = re.search(rf"\bt:\s*({NUMBER}).*FTPK:\s*(.*?)\s+dBFS", line)
            if match:
                self.true_peak_frames += 1
                values = [_finite_reading(part) for part in match[2].split()]
                if any(value is not None and value > 0 for value in values):
                    self.over_windows += 1
                    if self.first_over is None:
                        self.first_over = max(0.0, float(match[1]) - 0.1)
        if line.startswith("frame:"):
            self.flush()
            match = re.search(r"pts_time:([0-9.eE+-]+)", line)
            if not match:
                raise AudioAnalysisError("FFmpeg supplied invalid loudness timestamps.")
            self.frame_start = float(match[1])
            if not math.isfinite(self.frame_start) or self.frame_start < 0:
                raise AudioAnalysisError("FFmpeg supplied invalid loudness timestamps.")
            self.values = {}
        elif line.startswith("lavfi.r128."):
            key, separator, value = line.removeprefix("lavfi.r128.").partition("=")
            if separator and key in ("M", "S", "I", "LRA"):
                self.values[key] = _finite_reading(value)

    def flush(self):
        if self.frame_start is None:
            return
        if set(self.values) != {"M", "S", "I", "LRA"}:
            raise AudioAnalysisError("FFmpeg returned incomplete loudness metadata.")
        end = min(self.duration, self.frame_start + 0.1)

        def window_value(key, minimum):
            value = self.values[key]
            return value if end + 1e-7 >= minimum and value is not None and value > -120 else None

        m = window_value("M", 0.4)
        s = window_value("S", 3)
        integrated = self.values["I"]
        if integrated is None or integrated <= -70:
            integrated = None
        lra = self.values["LRA"] if s is not None else None
        point = LoudnessPoint(end, m, s, integrated, lra)
        self.last_point = point
        if m is not None:
            self.maximum_m = m if self.maximum_m is None else max(m, self.maximum_m)
        if s is not None:
            self.maximum_s = s if self.maximum_s is None else max(s, self.maximum_s)
        if self.count % self.stride == 0:
            if len(self.history) >= MAX_HISTORY_POINTS:
                self.history = self.history[::2]
                self.stride *= 2
            if self.count % self.stride == 0:
                self.history.append(point)
        self.count += 1
        now = time.monotonic()
        if self.progress and now - self.last_progress >= 0.1:
            self.progress(round(end * self.rate), round(self.duration * self.rate))
            self.last_progress = now
        self.frame_start = None


def measure_loudness(path, *, sample_report=None, cancel=None, progress=None):
    """Measure mono/stereo files at supported delivery rates; reset per call."""
    check_cancel(cancel)
    source = Path(path).expanduser().resolve(strict=True)
    signature = source_signature(source)
    if sample_report is None:
        sample_report = analyze_audio_file(source, cancel=cancel)
    if (
        Path(sample_report.source) != source
        or sample_report.source_size_bytes != signature[2]
        or sample_report.source_modified_ns != signature[3]
    ):
        raise AudioAnalysisError("The sample analysis does not match this source file.")
    if sample_report.channel_count not in (1, 2):
        raise AudioAnalysisError(
            "Loudness analysis currently supports mono/stereo, not unknown surround layouts."
        )
    if sample_report.sample_rate not in SUPPORTED_RATES:
        raise AudioAnalysisError(
            "Loudness analysis supports 44.1, 48, 88.2 and 96 kHz delivery files."
        )
    if any(channel.nonfinite_samples for channel in sample_report.channels):
        raise AudioAnalysisError(
            "Loudness analysis rejects non-finite samples; repair the source first."
        )
    if not sample_report.frames:
        raise AudioAnalysisError("The file contains no samples to measure.")
    executable = ffmpeg_path()
    version = _version(executable, Path(executable).stat().st_mtime_ns)
    scanner = _Scanner(sample_report.duration_seconds, sample_report.sample_rate, progress)
    if progress:
        progress(0, sample_report.frames)
    # The loudness measurement must NOT be padded: a trailing partial gating
    # block is excluded. The independent true-peak path needs silence to flush
    # the reconstruction filter's delay, including peaks at the last sample.
    graph = (
        "[0:a:0]asplit=2[lu][tp];"
        "[lu]ebur128@loudness=metadata=1,ametadata=print:file=-:direct=1[lo];"
        "[tp]apad=pad_dur=0.1,ebur128@truepeak=peak=true:framelog=info[po]"
    )
    run_ffmpeg(
        [
            "-protocol_whitelist",
            "file,pipe",
            "-i",
            source,
            "-filter_complex",
            graph,
            "-map",
            "[lo]",
            "-map",
            "[po]",
            "-f",
            "null",
            "-",
        ],
        cancel=cancel,
        on_line=scanner.line,
        timeout=max(60, sample_report.duration_seconds * 3 + 30),
        executable=executable,
    )
    scanner.flush()
    require_unchanged(source, signature)
    if set(scanner.summary) != {"I", "LRA", "TP"} or not scanner.true_peak_frames:
        raise AudioAnalysisError("FFmpeg did not provide complete loudness/true-peak measurements.")
    if scanner.last_point is not None and scanner.history[-1] != scanner.last_point:
        if len(scanner.history) >= MAX_HISTORY_POINTS:
            scanner.history.pop()
        scanner.history.append(scanner.last_point)
    integrated = scanner.summary["I"]
    if integrated is None or integrated <= -70 or sample_report.duration_seconds < 0.4:
        integrated = None
    notes = [
        "Offline file measurement; mono is measured as mono, not dual-mono.",
        "Momentary/short-term maxima are observed at 100 ms intervals, not sample-by-sample maxima.",
        "True-peak values and over-window decisions use FFmpeg's 0.1 dB reporting resolution; "
        "over-window counts are not clipped-sample counts.",
        "The final true-peak interpolation tail is included using zero extension; loudness is not padded.",
    ]
    if scanner.stride > 1:
        notes.append("Stored history is uniformly decimated; maxima include every measured window.")
    if integrated is None:
        notes.append(
            "Integrated loudness is unavailable for silence, sub-gate material, or files shorter than 400 ms."
        )
    if sample_report.duration_seconds < 60:
        notes.append("LRA is not considered stable during the first 60 seconds (EBU Tech 3341).")
    report = LoudnessReport(
        MEASUREMENT_METHOD,
        version,
        integrated,
        scanner.summary["LRA"] if scanner.maximum_s is not None else None,
        sample_report.duration_seconds >= 60,
        scanner.maximum_m,
        scanner.maximum_s,
        scanner.summary["TP"],
        scanner.over_windows,
        scanner.first_over,
        tuple(scanner.history),
        scanner.stride * 0.1,
        scanner.count,
        tuple(notes),
    )
    if progress:
        progress(sample_report.frames, sample_report.frames)
    check_cancel(cancel)
    return report
