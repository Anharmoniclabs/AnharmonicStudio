"""Two-pass loudness-normalized file export, verified before atomic publication.

Uses FFmpeg loudnorm as documented at
https://ffmpeg.org/ffmpeg-filters.html#loudnorm (accessed 2026-09-10).
The original render is retained. The output is float32 WAV, at the original
sample rate/channel count, plus a measured JSON delivery report. Existing files
are never replaced. This intentionally does not claim a new dither workflow.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, replace
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shutil
import tempfile

from .audio_analysis import AudioAnalysisError, analyze_audio_file
from .loudness import (
    SUPPORTED_RATES,
    check_cancel,
    measure_loudness,
    require_unchanged,
    run_ffmpeg,
    source_signature,
)


MAX_OUTPUT_BYTES = 4 * 1024**3
LOUDNESS_TOLERANCE_LU = 0.2
TRUE_PEAK_GUARD_DB = 0.1
TRUE_PEAK_FILTER_MARGIN_DB = 0.2


@dataclass(frozen=True)
class NormalizationReport:
    source: str
    destination: str
    report_path: str
    target_lufs: float
    true_peak_ceiling_dbtp: float
    normalization_mode: str
    input_analysis: dict
    output_analysis: dict
    output_sha256: str
    verified: bool = True
    loudness_tolerance_lu: float = LOUDNESS_TOLERANCE_LU
    true_peak_guard_db: float = TRUE_PEAK_GUARD_DB
    schema_version: int = 1

    def to_dict(self):
        return asdict(self)

    def to_json(self):
        return json.dumps(self.to_dict(), indent=2, ensure_ascii=False, allow_nan=False) + "\n"

    def to_text(self):
        output = self.output_analysis
        measured = output["loudness"]
        return (
            "\n".join(
                [
                    "VERIFIED LOUDNESS-NORMALIZED FILE EXPORT",
                    f"Source retained: {self.source}",
                    f"Audio: {self.destination}",
                    f"Measurement report: {self.report_path}",
                    f"Target: {self.target_lufs:.1f} LUFS; ceiling {self.true_peak_ceiling_dbtp:.1f} dBTP",
                    f"Measured output: {measured['integrated_lufs']:.1f} LUFS / "
                    f"{measured['true_peak_dbtp']:.1f} dBTP",
                    f"Normalization: {self.normalization_mode}; FLOAT WAV, "
                    f"{output['sample_rate']:,} Hz, {output['channel_count']} channel(s), "
                    f"{output['duration_seconds']:.6f} s",
                    f"Verification tolerance: ±{self.loudness_tolerance_lu:g} LU; "
                    f"reported true peak at least {self.true_peak_guard_db:g} dB below the selected ceiling.",
                    f"SHA-256: {self.output_sha256}",
                    "Dynamic mode can change the programme dynamics to meet the selected ceiling.",
                    "Output and JSON are published individually and atomically; existing files are never replaced.",
                ]
            )
            + "\n"
        )


def _target(value, minimum, maximum, name):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise AudioAnalysisError(f"{name} must be a finite number.")
    if not minimum <= value <= maximum:
        raise AudioAnalysisError(f"{name} must be from {minimum:g} to {maximum:g}.")
    return float(value)


def _statistics(text):
    for match in reversed(list(re.finditer(r"\{[^{}]*\}", text, flags=re.S))):
        try:
            values = json.loads(match[0])
        except ValueError:
            continue
        if isinstance(values, dict) and "input_i" in values:
            for key, minimum, maximum in (
                ("input_i", -99, 0),
                ("input_tp", -99, 99),
                ("input_lra", 0, 99),
                ("input_thresh", -99, 0),
                ("target_offset", -99, 99),
            ):
                try:
                    values[key] = _target(float(values[key]), minimum, maximum, key)
                except (KeyError, TypeError, ValueError) as exc:
                    raise AudioAnalysisError(
                        f"FFmpeg returned unusable normalization statistics: {key}"
                    ) from exc
            return values
    raise AudioAnalysisError("FFmpeg did not provide complete two-pass normalization statistics.")


def _sync_file(path):
    with Path(path).open("rb") as handle:
        os.fsync(handle.fileno())


def _publish_pair(audio, report, destination, report_destination):
    """No-overwrite publication with rollback of our own just-linked report."""
    report_signature = source_signature(report)
    os.link(report, report_destination)
    try:
        os.link(audio, destination)
    except BaseException:
        # Do not delete a report replaced by someone else during publication.
        if report_destination.exists() and source_signature(report_destination) == report_signature:
            report_destination.unlink()
        raise


def normalize_audio_file(
    source,
    destination,
    *,
    target_lufs=-14.0,
    true_peak_ceiling=-1.0,
    cancel=None,
    progress=None,
):
    """Export a measured, verified new WAV/JSON pair without altering the source.

    Targets deliberately leave headroom inside loudnorm's supported parameter
    range. Material that cannot meet both targets within tolerance is rejected,
    never published under a misleading success label.
    """
    target_lufs = _target(target_lufs, -36, -5, "Integrated loudness target (LUFS)")
    true_peak_ceiling = _target(true_peak_ceiling, -8, 0, "True-peak ceiling (dBTP)")
    check_cancel(cancel)
    source = Path(source).expanduser().resolve(strict=True)
    destination = Path(destination).expanduser().resolve()
    report_destination = destination.with_suffix(destination.suffix + ".analysis.json")
    if destination.suffix.casefold() != ".wav":
        raise AudioAnalysisError("Normalized export currently writes float32 WAV files (.wav).")
    for target in (destination, report_destination):
        if target == source or (target.exists() and os.path.samefile(target, source)):
            raise AudioAnalysisError("Normalized export cannot replace its source audio.")
        if target.exists():
            raise FileExistsError(
                f"Choose an unused output name; this file already exists: {target}"
            )
    if not destination.parent.is_dir():
        raise AudioAnalysisError("Choose an existing output directory.")
    signature = source_signature(source)

    def phase(start, width):
        def report_progress(done, total):
            if progress:
                progress(start + round(width * done / total) if total else start, 1000)

        return report_progress

    input_samples = analyze_audio_file(source, cancel=cancel, progress=phase(0, 100))
    if (
        input_samples.channel_count not in (1, 2)
        or input_samples.sample_rate not in SUPPORTED_RATES
    ):
        raise AudioAnalysisError("Normalization supports mono/stereo at 44.1, 48, 88.2 or 96 kHz.")
    estimated_bytes = input_samples.frames * input_samples.channel_count * 4 + 4096
    if estimated_bytes > MAX_OUTPUT_BYTES:
        raise AudioAnalysisError("Normalized output exceeds the bounded 4 GiB WAV export limit.")
    if shutil.disk_usage(destination.parent).free < estimated_bytes + 16 * 1024**2:
        raise AudioAnalysisError(
            "There is not enough free disk space for the normalized file and report."
        )
    input_loudness = measure_loudness(
        source, sample_report=input_samples, cancel=cancel, progress=phase(100, 150)
    )
    if input_loudness.integrated_lufs is None:
        raise AudioAnalysisError(
            "Silence, sub-gate material and files shorter than 400 ms cannot be loudness-normalized."
        )
    require_unchanged(source, signature)
    internal_ceiling = true_peak_ceiling - TRUE_PEAK_FILTER_MARGIN_DB
    settings = f"I={target_lufs:.8f}:TP={internal_ceiling:.8f}:LRA=50:dual_mono=false"
    timeout = max(60, input_samples.duration_seconds * 4 + 30)

    def ffmpeg_progress(start, width):
        notify = phase(start, width)

        def line(value):
            if value.startswith("out_time_us="):
                try:
                    seconds = int(value.split("=", 1)[1]) / 1_000_000
                except ValueError:
                    return
                notify(
                    min(input_samples.frames, max(0, round(seconds * input_samples.sample_rate))),
                    input_samples.frames,
                )

        return line

    first = _statistics(
        run_ffmpeg(
            [
                "-protocol_whitelist",
                "file,pipe",
                "-i",
                source,
                "-map",
                "0:a:0",
                "-vn",
                "-sn",
                "-dn",
                "-af",
                f"loudnorm={settings}:print_format=json",
                "-progress",
                "pipe:1",
                "-f",
                "null",
                "-",
            ],
            cancel=cancel,
            on_line=ffmpeg_progress(250, 200),
            timeout=timeout,
        )
    )
    require_unchanged(source, signature)
    measured = (
        f":measured_I={first['input_i']:.8f}:measured_TP={first['input_tp']:.8f}"
        f":measured_LRA={first['input_lra']:.8f}:measured_thresh={first['input_thresh']:.8f}"
        f":offset={first['target_offset']:.8f}:linear=true:print_format=json"
    )
    with tempfile.TemporaryDirectory(
        prefix=".audio-normalization-", dir=destination.parent
    ) as temporary:
        temporary = Path(temporary)
        audio_path, report_path = temporary / "normalized.wav", temporary / "analysis.json"
        audio_filter = (
            f"loudnorm={settings}{measured},aresample={input_samples.sample_rate},"
            f"apad=whole_len={input_samples.frames},atrim=end_sample={input_samples.frames}"
        )
        second = _statistics(
            run_ffmpeg(
                [
                    "-protocol_whitelist",
                    "file,pipe",
                    "-i",
                    source,
                    "-map",
                    "0:a:0",
                    "-vn",
                    "-sn",
                    "-dn",
                    "-af",
                    audio_filter,
                    "-map_metadata",
                    "-1",
                    "-c:a",
                    "pcm_f32le",
                    "-ar",
                    input_samples.sample_rate,
                    "-ac",
                    input_samples.channel_count,
                    "-rf64",
                    "auto",
                    "-progress",
                    "pipe:1",
                    "-n",
                    audio_path,
                ],
                cancel=cancel,
                on_line=ffmpeg_progress(450, 250),
                timeout=timeout,
            )
        )
        require_unchanged(source, signature)
        output_samples = analyze_audio_file(audio_path, cancel=cancel, progress=phase(700, 100))
        if (
            output_samples.frames != input_samples.frames
            or output_samples.sample_rate != input_samples.sample_rate
            or output_samples.channel_count != input_samples.channel_count
            or output_samples.subtype != "FLOAT"
            or output_samples.source_size_bytes > MAX_OUTPUT_BYTES
            or any(channel.nonfinite_samples for channel in output_samples.channels)
        ):
            raise AudioAnalysisError(
                "Normalized audio failed format, duration or finite-sample verification."
            )
        output_loudness = measure_loudness(
            audio_path, sample_report=output_samples, cancel=cancel, progress=phase(800, 180)
        )
        if (
            output_loudness.integrated_lufs is None
            or abs(output_loudness.integrated_lufs - target_lufs) > LOUDNESS_TOLERANCE_LU + 1e-6
        ):
            raise AudioAnalysisError(
                f"Normalization could not meet {target_lufs:.1f} LUFS within ±{LOUDNESS_TOLERANCE_LU:g} LU "
                f"(measured {output_loudness.integrated_lufs}). No output was published."
            )
        if (
            output_loudness.true_peak_dbtp is None
            or output_loudness.true_peak_dbtp > true_peak_ceiling - TRUE_PEAK_GUARD_DB + 1e-6
        ):
            raise AudioAnalysisError(
                "Normalized output failed the independently measured true-peak ceiling; no output was published."
            )
        mode = second.get("normalization_type")
        if mode not in ("linear", "dynamic"):
            raise AudioAnalysisError("FFmpeg did not identify the applied normalization mode.")
        digest = hashlib.sha256()
        with audio_path.open("rb") as handle:
            while block := handle.read(1024 * 1024):
                check_cancel(cancel)
                digest.update(block)
        input_data = input_samples.to_dict()
        input_data["loudness"] = input_loudness.to_dict()
        output_data = replace(output_samples, source=str(destination)).to_dict()
        output_data["loudness"] = output_loudness.to_dict()
        report = NormalizationReport(
            str(source),
            str(destination),
            str(report_destination),
            target_lufs,
            true_peak_ceiling,
            mode,
            input_data,
            output_data,
            digest.hexdigest(),
        )
        with report_path.open("x", encoding="utf-8", newline="\n") as handle:
            handle.write(report.to_json())
            handle.flush()
            os.fsync(handle.fileno())
        _sync_file(audio_path)
        require_unchanged(source, signature)
        check_cancel(cancel)
        _publish_pair(audio_path, report_path, destination, report_destination)
    # Publication is the commit point: a subsequent cancellation cannot turn a
    # successfully delivered pair into a cancelled/ambiguous job.
    if progress:
        progress(1000, 1000)
    return report
