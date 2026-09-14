"""Bounded, read-only sample-domain analysis of an existing rendered audio file.

This module intentionally does not report LUFS or dBTP. RMS is not perceptual
loudness, and a stored-sample peak is not a reconstructed/inter-sample peak.
The source is streamed through libsndfile; no playback device is opened.
"""

from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
import json
import math
import os
from pathlib import Path
import tempfile
import threading
import time
from typing import Callable

import numpy as np
import soundfile as sf


REPORT_VERSION = 1
DEFAULT_BLOCK_FRAMES = 16_384
MAX_BLOCK_BYTES = 8 * 1024 * 1024
MAX_CHANNELS = 64
MAX_SAMPLE_RATE = 768_000
MAX_DURATION_SECONDS = 24 * 60 * 60
MAX_SAMPLE_MAGNITUDE = 1_000_000.0
LIMITATIONS = (
    "Sample peaks measure stored samples; true peak / dBTP is not measured.",
    "RMS is an electrical level, not LUFS. Integrated, short-term and momentary "
    "loudness and loudness range are not measured.",
    "Full-scale samples can indicate clipping, but do not prove that the source was clipped.",
    "Stereo correlation is DC-removed Pearson correlation and applies only to two-channel files.",
)


class AnalysisCancelled(Exception):
    """An explicit cancellation completed without publishing a partial report."""


class AudioAnalysisError(ValueError):
    """The input cannot be analyzed safely or consistently."""


def _dbfs(amplitude: float) -> float | None:
    return 20.0 * math.log10(amplitude) if amplitude > 0 else None


@dataclass(frozen=True)
class ChannelAnalysis:
    channel: int
    valid_samples: int
    nonfinite_samples: int
    first_nonfinite_frame: int | None
    sample_peak: float
    sample_peak_dbfs: float | None
    peak_frame: int | None
    rms: float
    rms_dbfs: float | None
    dc_offset: float | None
    dc_offset_dbfs: float | None
    full_scale_samples: int
    first_full_scale_frame: int | None
    longest_full_scale_run: int
    overload_samples: int
    first_overload_frame: int | None


@dataclass(frozen=True)
class StereoAnalysis:
    valid_frames: int
    correlation: float | None
    mid_rms_dbfs: float | None
    side_rms_dbfs: float | None
    side_energy_percent: float | None
    mono_sum_rms_dbfs: float | None


@dataclass(frozen=True)
class AnalysisFinding:
    code: str
    severity: str
    message: str


@dataclass(frozen=True)
class AudioAnalysisReport:
    source: str
    source_size_bytes: int
    source_modified_ns: int
    format: str
    subtype: str
    sample_rate: int
    frames: int
    duration_seconds: float
    channel_count: int
    sample_peak_dbfs: float | None
    rms_dbfs: float | None
    positive_full_scale: float
    channels: tuple[ChannelAnalysis, ...]
    stereo: StereoAnalysis | None
    findings: tuple[AnalysisFinding, ...]
    limitations: tuple[str, ...] = LIMITATIONS
    schema_version: int = REPORT_VERSION

    def to_dict(self) -> dict:
        return asdict(self)

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), indent=2, ensure_ascii=False, allow_nan=False) + "\n"

    def to_text(self) -> str:
        def level(value, has_samples=True):
            if not has_samples:
                return "unavailable (no finite samples)"
            return "−∞ dBFS (silence)" if value is None else f"{value:.3f} dBFS"

        def first_position(frame):
            return (
                ""
                if frame is None
                else f" (first at {frame / self.sample_rate:.6f} s, frame {frame})"
            )

        has_samples = any(channel.valid_samples for channel in self.channels)
        lines = [
            "ANHARMONIC AUDIO ANALYSIS — SAMPLE DOMAIN",
            self.source,
            f"{self.format} / {self.subtype} · {self.sample_rate:,} Hz · "
            f"{self.channel_count} channel(s)",
            f"Duration: {self.duration_seconds:.6f} s · {self.frames:,} frames · "
            f"{self.source_size_bytes:,} bytes",
            f"Highest stored-sample peak: {level(self.sample_peak_dbfs, has_samples)}",
            f"Overall RMS: {level(self.rms_dbfs, has_samples)}",
            "",
        ]
        for channel in self.channels:
            dc = "unavailable" if channel.dc_offset is None else f"{channel.dc_offset:+.8f} FS"
            peak_time = (
                ""
                if channel.peak_frame is None
                else f" at {channel.peak_frame / self.sample_rate:.6f} s"
            )
            lines.extend(
                [
                    f"Channel {channel.channel}: peak "
                    f"{level(channel.sample_peak_dbfs, channel.valid_samples)}{peak_time}",
                    f"  RMS {level(channel.rms_dbfs, channel.valid_samples)} · DC offset {dc}",
                    f"  Full-scale samples: {channel.full_scale_samples:,}"
                    f"{first_position(channel.first_full_scale_frame)}; "
                    f"longest consecutive run: {channel.longest_full_scale_run:,} frames",
                    f"  Samples beyond ±1 FS: {channel.overload_samples:,}"
                    f"{first_position(channel.first_overload_frame)}",
                    f"  Invalid/non-finite samples: {channel.nonfinite_samples:,}"
                    f"{first_position(channel.first_nonfinite_frame)}",
                ]
            )
        if self.stereo is not None:
            stereo = self.stereo
            correlation = (
                "undefined (silent or constant channel)"
                if stereo.correlation is None
                else f"{stereo.correlation:+.6f} (−1 opposite / 0 uncorrelated / +1 same)"
            )
            width = (
                "undefined (silence)"
                if stereo.side_energy_percent is None
                else f"{stereo.side_energy_percent:.3f}%"
            )
            lines.extend(
                [
                    "",
                    f"AC stereo correlation: {correlation}",
                    f"Mid RMS: {level(stereo.mid_rms_dbfs, stereo.valid_frames)} · "
                    f"Side RMS: {level(stereo.side_rms_dbfs, stereo.valid_frames)}",
                    f"Side energy share: {width}",
                    f"Mono average (L+R)/2 RMS: "
                    f"{level(stereo.mono_sum_rms_dbfs, stereo.valid_frames)}",
                ]
            )
        lines.extend(["", "DELIVERY OBSERVATIONS"])
        lines.extend(f"{finding.severity.upper()}: {finding.message}" for finding in self.findings)
        if not self.findings:
            lines.append("No sample-domain clipping, invalid-sample, DC or phase flags found.")
        lines.extend(["", "MEASUREMENT LIMITS", *self.limitations])
        return "\n".join(lines) + "\n"


class _StereoAccumulator:
    def __init__(self):
        self.count = 0
        self.mean = np.zeros(2, dtype=np.float64)
        self.variance_sum = np.zeros(2, dtype=np.float64)
        self.covariance_sum = 0.0
        self.minimum = np.full(2, np.inf)
        self.maximum = np.full(2, -np.inf)
        self.mid_energy = 0.0
        self.side_energy = 0.0

    def add(self, values: np.ndarray) -> None:
        count = len(values)
        if not count:
            return
        self.minimum = np.minimum(self.minimum, values.min(axis=0))
        self.maximum = np.maximum(self.maximum, values.max(axis=0))
        mean = values.mean(axis=0)
        centered = values - mean
        variance = np.sum(centered * centered, axis=0)
        covariance = float(np.dot(centered[:, 0], centered[:, 1]))
        total = self.count + count
        delta = mean - self.mean
        weight = self.count * (count / total)
        self.variance_sum += variance + delta * delta * weight
        self.covariance_sum += covariance + float(delta[0] * delta[1]) * weight
        self.mean += delta * (count / total)
        self.count = total
        # Orthonormal M/S preserves the total stereo energy.
        mid = (values[:, 0] + values[:, 1]) / math.sqrt(2)
        side = (values[:, 0] - values[:, 1]) / math.sqrt(2)
        self.mid_energy += float(np.dot(mid, mid))
        self.side_energy += float(np.dot(side, side))

    def finish(self) -> StereoAnalysis:
        denominator = float(np.sqrt(self.variance_sum[0] * self.variance_sum[1]))
        correlation = (
            float(np.clip(self.covariance_sum / denominator, -1, 1))
            if denominator > 0 and np.all(self.maximum > self.minimum)
            else None
        )
        total_energy = self.mid_energy + self.side_energy
        mid = math.sqrt(self.mid_energy / self.count) if self.count else 0.0
        side = math.sqrt(self.side_energy / self.count) if self.count else 0.0
        return StereoAnalysis(
            self.count,
            correlation,
            _dbfs(mid),
            _dbfs(side),
            self.side_energy / total_energy * 100 if total_energy > 0 else None,
            _dbfs(mid / math.sqrt(2)),
        )


def _positive_full_scale(subtype: str) -> float:
    bits = {
        "PCM_U8": 8,
        "PCM_S8": 8,
        "PCM_16": 16,
        "PCM_24": 24,
        "PCM_32": 32,
        "ALAC_16": 16,
        "ALAC_20": 20,
        "ALAC_24": 24,
        "ALAC_32": 32,
    }.get(subtype)
    return 1.0 - 2.0 ** (1 - bits) if bits else 1.0


def _run_lengths(mask: np.ndarray, previous: int, longest: int) -> tuple[int, int]:
    transitions = np.diff(np.pad(mask.astype(np.int8), (1, 1)))
    starts = np.flatnonzero(transitions == 1)
    ends = np.flatnonzero(transitions == -1)
    if not len(starts):
        return 0, longest
    lengths = ends - starts
    if starts[0] == 0:
        lengths[0] += previous
    longest = max(longest, int(lengths.max()))
    trailing = int(lengths[-1]) if ends[-1] == len(mask) else 0
    return trailing, longest


def analyze_audio_file(
    path: str | Path,
    *,
    block_frames: int = DEFAULT_BLOCK_FRAMES,
    cancel: threading.Event | None = None,
    progress: Callable[[int, int], None] | None = None,
) -> AudioAnalysisReport:
    """Analyze a regular file with bounded blocks; never modify the input.

    Non-finite samples are counted and excluded from statistics. Stereo
    statistics use only frames where both channels are finite. Positive integer
    full-scale detection accounts for the source word length's asymmetric range.
    """
    if type(block_frames) is not int or not 1 <= block_frames <= 1_048_576:
        raise AudioAnalysisError("Analysis block size must be an integer from 1 to 1048576.")

    def check_cancel():
        if cancel is not None and cancel.is_set():
            raise AnalysisCancelled()

    check_cancel()
    source = Path(path).expanduser().resolve(strict=True)
    before = source.stat()
    if not source.is_file():
        raise AudioAnalysisError(
            "Choose a regular audio file; devices and directories are rejected."
        )
    with sf.SoundFile(source, "r") as handle:
        channels, rate, expected_frames = handle.channels, handle.samplerate, len(handle)
        if not 1 <= channels <= MAX_CHANNELS or not 1 <= rate <= MAX_SAMPLE_RATE:
            raise AudioAnalysisError("Analysis supports 1–64 channels and rates up to 768 kHz.")
        if expected_frames / rate > MAX_DURATION_SECONDS:
            raise AudioAnalysisError("Analysis supports files up to 24 hours long.")
        chunk_frames = min(block_frames, MAX_BLOCK_BYTES // (channels * 8))
        subtype, audio_format = handle.subtype, handle.format
        positive_ceiling = _positive_full_scale(subtype)
        counts = np.zeros(channels, dtype=np.int64)
        invalid = np.zeros(channels, dtype=np.int64)
        sums = np.zeros(channels, dtype=np.float64)
        squares = np.zeros(channels, dtype=np.float64)
        peaks = np.zeros(channels, dtype=np.float64)
        full_scale = np.zeros(channels, dtype=np.int64)
        overloads = np.zeros(channels, dtype=np.int64)
        first_invalid = [None] * channels
        first_full = [None] * channels
        first_overload = [None] * channels
        peak_frames = [None] * channels
        trailing = [0] * channels
        longest = [0] * channels
        stereo = _StereoAccumulator() if channels == 2 else None
        frames = 0
        last_progress = time.monotonic()
        if progress:
            progress(0, expected_frames)
        while True:
            check_cancel()
            block = handle.read(chunk_frames, dtype="float64", always_2d=True)
            if not len(block):
                break
            finite = np.isfinite(block)
            valid = np.count_nonzero(finite, axis=0)
            invalid += len(block) - valid
            counts += valid
            np.copyto(block, 0, where=~finite)
            absolute = np.abs(block)
            if np.any(absolute > MAX_SAMPLE_MAGNITUDE):
                raise AudioAnalysisError(
                    "Sample magnitude exceeds the supported ±1000000 FS range."
                )
            sums += block.sum(axis=0)
            squares += np.sum(block * block, axis=0)
            full = finite & ((block >= positive_ceiling) | (block <= -1.0))
            over = finite & (absolute > 1.0)
            full_scale += full.sum(axis=0)
            overloads += over.sum(axis=0)
            for channel in range(channels):
                local_peak = int(absolute[:, channel].argmax())
                if absolute[local_peak, channel] > peaks[channel]:
                    peaks[channel] = absolute[local_peak, channel]
                    peak_frames[channel] = frames + local_peak
                for mask, first in (
                    (~finite[:, channel], first_invalid),
                    (full[:, channel], first_full),
                    (over[:, channel], first_overload),
                ):
                    if first[channel] is None and mask.any():
                        first[channel] = frames + int(mask.argmax())
                trailing[channel], longest[channel] = _run_lengths(
                    full[:, channel], trailing[channel], longest[channel]
                )
            if stereo is not None:
                stereo.add(block[np.all(finite, axis=1)])
            frames += len(block)
            now = time.monotonic()
            if progress and now - last_progress >= 0.1:
                progress(frames, expected_frames)
                last_progress = now
        check_cancel()
        if frames != expected_frames:
            raise AudioAnalysisError(
                "Audio ended before its declared frame count; no report saved."
            )
    after = source.stat()
    if (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) != (
        after.st_dev,
        after.st_ino,
        after.st_size,
        after.st_mtime_ns,
    ):
        raise AudioAnalysisError("The source changed during analysis; run analysis again.")
    check_cancel()
    measurements = []
    for index in range(channels):
        rms = math.sqrt(float(squares[index]) / counts[index]) if counts[index] else 0.0
        dc = float(sums[index] / counts[index]) if counts[index] else None
        measurements.append(
            ChannelAnalysis(
                index + 1,
                int(counts[index]),
                int(invalid[index]),
                first_invalid[index],
                float(peaks[index]),
                _dbfs(float(peaks[index])),
                peak_frames[index],
                rms,
                _dbfs(rms),
                dc,
                _dbfs(abs(dc)) if dc is not None else None,
                int(full_scale[index]),
                first_full[index],
                longest[index],
                int(overloads[index]),
                first_overload[index],
            )
        )
    stereo_result = stereo.finish() if stereo is not None else None
    findings = []
    if not frames:
        findings.append(AnalysisFinding("empty", "error", "The file contains no audio frames."))
    if invalid.sum():
        findings.append(
            AnalysisFinding(
                "nonfinite",
                "error",
                f"{int(invalid.sum()):,} non-finite samples were excluded from the measurements.",
            )
        )
    if overloads.sum():
        findings.append(
            AnalysisFinding("overload", "error", f"{int(overloads.sum()):,} samples exceed ±1 FS.")
        )
    if full_scale.sum():
        findings.append(
            AnalysisFinding(
                "full_scale",
                "warning",
                f"{int(full_scale.sum()):,} samples reach the source's digital ceiling; "
                "inspect for clipping.",
            )
        )
    dc_channels = [item.channel for item in measurements if abs(item.dc_offset or 0) >= 0.01]
    if dc_channels:
        findings.append(
            AnalysisFinding(
                "dc_offset",
                "warning",
                f"DC offset reaches 1% FS or more in channel(s) {', '.join(map(str, dc_channels))}.",
            )
        )
    if (
        stereo_result
        and stereo_result.correlation is not None
        and stereo_result.correlation < -0.25
    ):
        findings.append(
            AnalysisFinding(
                "negative_correlation",
                "warning",
                "Negative stereo correlation may cause cancellation in mono; audition the mono sum.",
            )
        )
    count = int(counts.sum())
    report = AudioAnalysisReport(
        str(source),
        before.st_size,
        before.st_mtime_ns,
        audio_format,
        subtype,
        rate,
        frames,
        frames / rate,
        channels,
        _dbfs(float(peaks.max())),
        _dbfs(math.sqrt(float(squares.sum()) / count)) if count else None,
        positive_ceiling,
        tuple(measurements),
        stereo_result,
        tuple(findings),
    )
    if progress:
        progress(frames, expected_frames)
    check_cancel()
    return report


def save_analysis_report(
    report: AudioAnalysisReport,
    destination: str | Path,
    *,
    report_format: str = "json",
    overwrite: bool = False,
) -> Path:
    """Atomically publish a completed report, never replacing its source audio."""
    if report_format not in ("json", "text"):
        raise ValueError("Report format must be json or text.")
    destination = Path(destination).expanduser().resolve()
    source = Path(report.source)
    if destination == source or (destination.exists() and os.path.samefile(destination, source)):
        raise AudioAnalysisError("The analysis report cannot replace its source audio.")
    if destination.exists() and not overwrite:
        raise FileExistsError(destination)
    payload = report.to_json() if report_format == "json" else report.to_text()
    fd, temporary = tempfile.mkstemp(prefix=".audio-analysis-", dir=destination.parent)
    temporary = Path(temporary)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        if overwrite:
            os.replace(temporary, destination)
        else:
            # Link publication refuses a destination created since the check.
            os.link(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)
    return destination


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("--json", type=Path, dest="json_path", help="Save a JSON analysis report.")
    parser.add_argument(
        "--text", type=Path, dest="text_path", help="Save a readable analysis report."
    )
    parser.add_argument("--overwrite", action="store_true", help="Replace an existing report.")
    args = parser.parse_args(argv)
    try:
        report = analyze_audio_file(args.source)
        for target, kind in ((args.json_path, "json"), (args.text_path, "text")):
            if target:
                save_analysis_report(report, target, report_format=kind, overwrite=args.overwrite)
    except (OSError, ValueError, sf.LibsndfileError) as exc:
        parser.exit(2, f"Analysis failed: {exc}\n")
    print(report.to_text(), end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
