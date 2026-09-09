#!/usr/bin/env python
"""Measure piano, pitched pads, and a mixed arrangement at several rates.

These are device-independent DSP measurements. Non-48 kHz runs are CPU/rate
experiments with inserts disabled: the production mix effects are fixed at
48 kHz. No test changes the desktop sound server's rate or quantum.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
import time

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mpclab.engine import Engine
from mpclab.model import Clip, Row
from mpclab.music import Note, AutomationLane, AutomationPoint
from scripts.bench_callback import _FakeLibrary, _prepare


def production_engine(rate=48000, frames=256, workload="mixed"):
    engine = Engine(_FakeLibrary(rate), sample_rate=rate, blocksize=frames)
    project = engine.project
    project.bpm = 120
    pattern = project.pattern()
    pattern.bars = 1
    if workload in ("pads", "mixed"):
        count = 16 if workload == "pads" else 8
        _prepare(engine, count)
        for pad in range(count):
            pattern.steps[pad] = {step: 0.2 for step in range(pad % 2, 16, 2)}
    if workload in ("piano", "mixed"):
        count = 8 if workload == "piano" else 6
        pattern.notes = [
            Note(48 + i * 3, beat, 0.85, 0.55) for beat in (0, 1, 2, 3) for i in range(count)
        ]
    project.rows = [
        Row(clips=[Clip(kind="pattern", ref=pattern.id, start_beat=0, length_beats=1_000_000)])
    ]
    project.automation = [
        AutomationLane("master", [AutomationPoint(0, 0.6), AutomationPoint(32, 0.8)])
    ]
    if workload == "mixed" and rate == 48000:
        for i in (0, 2, 3):
            fx = project.tracks[i].fx
            fx.low, fx.drive, fx.comp = 2, 0.05, True
            fx.send_delay, fx.send_reverb = 0.05, 0.08
    engine.prepare_fx()
    engine.mode = "song"
    engine.playing = True
    return engine


def benchmark(rate, frames, workload, seconds):
    engine = production_engine(rate, frames, workload)
    output = np.zeros((frames, 2), dtype=np.float32)
    count = max(64, int(seconds * rate / frames))
    timings = np.empty(count)
    for index in range(count):
        start = time.perf_counter()
        engine._callback(output, frames, None, False)
        timings[index] = (time.perf_counter() - start) * 1000
        if not np.isfinite(output).all():
            raise RuntimeError("Nonfinite audio")
    steady = timings[8:]
    period = frames / rate * 1000
    return dict(
        rate=rate,
        frames=frames,
        workload=workload,
        synth_voice_limit=4 if frames <= 128 else 8,
        inserts=workload == "mixed" and rate == 48000,
        blocks=count,
        period_ms=period,
        p50_ms=float(np.percentile(steady, 50)),
        p99_ms=float(np.percentile(steady, 99)),
        worst_ms=float(steady.max()),
        cold_worst_ms=float(timings[:8].max()),
        over_budget=int(np.count_nonzero(steady >= period)),
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rates", nargs="+", type=int, default=[44100, 48000, 96000])
    parser.add_argument("--frames", nargs="+", type=int, default=[128, 256, 512])
    parser.add_argument(
        "--workloads",
        nargs="+",
        choices=["pads", "piano", "mixed"],
        default=["pads", "piano", "mixed"],
    )
    parser.add_argument("--seconds", type=float, default=2)
    parser.add_argument("--json", type=Path)
    parser.add_argument(
        "--max-p99-load",
        type=float,
        default=None,
        help="Fail if steady p99 exceeds this fraction of the block deadline",
    )
    parser.add_argument("--max-late-fraction", type=float, default=None)
    args = parser.parse_args()
    if (
        args.seconds <= 0
        or any(rate <= 0 for rate in args.rates)
        or any(frames <= 0 for frames in args.frames)
    ):
        parser.error("seconds, rates and frames must be positive")
    results = []
    for rate in args.rates:
        for frames in args.frames:
            for workload in args.workloads:
                result = benchmark(rate, frames, workload, args.seconds)
                results.append(result)
                print(json.dumps(result), flush=True)
    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(json.dumps(results, indent=2) + "\n")
    failed = any(
        (args.max_p99_load is not None and r["p99_ms"] / r["period_ms"] > args.max_p99_load)
        or (
            args.max_late_fraction is not None
            and r["over_budget"] / (r["blocks"] - 8) > args.max_late_fraction
        )
        for r in results
    )
    if failed:
        print("FAIL: production callback headroom threshold exceeded", file=sys.stderr)
    return int(failed)


if __name__ == "__main__":
    raise SystemExit(main())
