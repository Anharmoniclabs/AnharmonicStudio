#!/usr/bin/env python3
"""Deterministic synthetic correctness/performance baseline for vocal tuning."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
import time

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mpclab.model import VocalSettings
from mpclab.vocal import PITCH_VOICING_THRESHOLD, analyze_pitch, render_autotune


def tone(midi: int, seconds: float, sample_rate: int, antiphase: bool = False) -> np.ndarray:
    frequency = 440.0 * 2.0 ** ((midi - 69) / 12.0)
    times = np.arange(round(seconds * sample_rate), dtype=np.float32) / sample_rate
    mono = np.float32(0.2) * np.sin(2.0 * np.pi * frequency * times)
    right = -mono if antiphase else mono
    return np.ascontiguousarray(np.column_stack((mono, right)), dtype=np.float32)


def percentile(values: list[float], value: float) -> float:
    return float(np.percentile(np.asarray(values, dtype=np.float64), value))


def run(seconds: float, sample_rate: int) -> dict:
    settings = VocalSettings(low_note=36, high_note=84)
    errors = []
    started = time.perf_counter()
    for midi in range(36, 85):
        analysis = analyze_pitch(tone(midi, seconds, sample_rate), settings, sr=sample_rate)
        voiced = analysis.confidence >= PITCH_VOICING_THRESHOLD
        if not np.any(voiced):
            errors.append(float("inf"))
            continue
        detected = float(np.nanmedian(analysis.detected_midi[voiced]))
        errors.append(abs(detected - midi) * 100.0)
    tracking_seconds = time.perf_counter() - started

    antiphase = analyze_pitch(
        tone(57, seconds, sample_rate, antiphase=True), settings, sr=sample_rate
    )
    render_source = tone(66, max(1.0, seconds), sample_rate)
    render_settings = VocalSettings(
        key="C",
        scale="major",
        retune_ms=0,
        humanize=0,
        formant=0,
        highpass_hz=20,
        deesser=0,
        compression=0,
        presence_db=0,
        gate_db=-80,
    )
    started = time.perf_counter()
    rendered, _analysis = render_autotune(render_source, render_settings, sr=sample_rate)
    render_seconds = time.perf_counter() - started
    audio_seconds = len(render_source) / sample_rate

    return {
        "sample_rate": sample_rate,
        "case_seconds": seconds,
        "tracker_cases": len(errors),
        "tracking_elapsed_seconds": round(tracking_seconds, 6),
        "tracking_median_cents": round(percentile(errors, 50), 4),
        "tracking_p95_cents": round(percentile(errors, 95), 4),
        "tracking_max_cents": round(max(errors), 4),
        "antiphase_voiced_fraction": round(antiphase.voiced_fraction, 4),
        "render_elapsed_seconds": round(render_seconds, 6),
        "render_realtime_factor": round(render_seconds / audio_seconds, 6),
        "render_exact_length": len(rendered) == len(render_source),
        "render_all_finite": bool(np.isfinite(rendered).all()),
        "render_peak": round(float(np.max(np.abs(rendered))), 6),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--seconds", type=float, default=0.35)
    parser.add_argument("--sample-rate", type=int, default=48_000)
    parser.add_argument("--check", action="store_true", help="fail when baseline gates fail")
    args = parser.parse_args()
    if args.seconds <= 0 or args.sample_rate < 8_000:
        parser.error("seconds must be positive and sample rate must be at least 8000")

    result = run(args.seconds, args.sample_rate)
    print(json.dumps(result, indent=2, sort_keys=True))
    if args.check and not (
        result["tracking_median_cents"] <= 10.0
        and result["tracking_p95_cents"] <= 25.0
        and result["antiphase_voiced_fraction"] >= 0.8
        and result["render_exact_length"]
        and result["render_all_finite"]
        and result["render_peak"] <= 0.981
    ):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
