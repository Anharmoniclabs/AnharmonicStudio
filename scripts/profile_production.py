#!/usr/bin/env python
"""Collect device-independent audio hotspots; profiler timings are not release acceptance.

Runs the existing production workload at 48 kHz with the four-beat loop and
metronome used by the release soak. Unprofiled wall/CPU timings and a separate
cProfile run are recorded without opening an audio or MIDI device. Existing
release thresholds remain in soak_production.py; this diagnostic does not pass
or waive them.
"""

from __future__ import annotations

import argparse
import cProfile
import io
import json
import math
import os
from pathlib import Path
import platform
import pstats
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from mpclab.production_runtime import configure

configure()

import numpy as np
import psutil

from mpclab.native_dsp import NATIVE, STATUS
from scripts.bench_production import production_engine

FRAME_SIZES = (256, 512, 1024)
WORKLOADS = ("pads", "piano", "mixed")


def bounded_seconds(value):
    number = float(value)
    if not math.isfinite(number) or not 0.1 <= number <= 30:
        raise argparse.ArgumentTypeError("simulation duration must be between 0.1 and 30 seconds")
    return number


def environment():
    processor = platform.processor()
    if sys.platform == "darwin":
        result = subprocess.run(
            ["/usr/sbin/sysctl", "-n", "machdep.cpu.brand_string"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        if result.returncode == 0:
            processor = result.stdout.strip()
    commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    dirty = bool(
        subprocess.check_output(
            ["git", "status", "--porcelain", "--untracked-files=normal"], cwd=ROOT, text=True
        ).strip()
    )
    return {
        "source_commit": commit,
        "worktree_has_changes": dirty,
        "platform": platform.platform(),
        "architecture": platform.machine(),
        "processor": processor,
        "logical_cpus": psutil.cpu_count(),
        "physical_cpus": psutil.cpu_count(logical=False),
        "python": platform.python_version(),
        "numpy": np.__version__,
        "native_backend": STATUS,
        "wall_clock": time.get_clock_info("perf_counter").implementation,
        "thread_clock": time.get_clock_info("thread_time").implementation,
        "thread_clock_resolution_seconds": time.get_clock_info("thread_time").resolution,
        "github_run_id": os.environ.get("GITHUB_RUN_ID"),
        "github_run_attempt": os.environ.get("GITHUB_RUN_ATTEMPT"),
    }


def prepared_engine(frames, workload):
    engine = production_engine(rate=48000, frames=frames, workload=workload)
    engine.project.loop_start, engine.project.loop_end = 0, 4
    engine.loop_song = True
    engine.metronome = True
    output = np.zeros((frames, 2), dtype=np.float32)
    for _ in range(16):
        engine._callback(output, frames, None, False)
    engine.beat = 0
    engine.voices.clear()
    engine.synth_voices.clear()
    engine.reset_timing()
    return engine, output


def benchmark(frames, workload, seconds):
    engine, output = prepared_engine(frames, workload)
    count = max(64, int(seconds * engine.sr / frames))
    wall = np.empty(count)
    cpu = np.empty(count)
    wraps = 0
    rss_start = psutil.Process().memory_info().rss
    try:
        for index in range(count):
            previous = engine.beat
            started = time.perf_counter()
            cpu_started = time.thread_time()
            engine._callback(output, frames, None, False)
            cpu[index] = (time.thread_time() - cpu_started) * 1000
            wall[index] = (time.perf_counter() - started) * 1000
            wraps += engine.beat < previous
            if not np.isfinite(output).all():
                raise RuntimeError("Diagnostic workload produced nonfinite audio")
        if engine.stream is not None:
            raise RuntimeError("Diagnostic unexpectedly opened an audio stream")
        period = frames / engine.sr * 1000
        return {
            "frames": frames,
            "workload": workload,
            "sample_rate": engine.sr,
            "simulated_seconds": count * frames / engine.sr,
            "callbacks": count,
            "loop_wraps": wraps,
            "period_ms": period,
            "wall_p50_ms": float(np.percentile(wall, 50)),
            "wall_p99_ms": float(np.percentile(wall, 99)),
            "wall_worst_ms": float(wall.max()),
            "thread_cpu_p50_ms": float(np.percentile(cpu, 50)),
            "thread_cpu_p99_ms": float(np.percentile(cpu, 99)),
            "thread_cpu_worst_ms": float(cpu.max()),
            "over_budget": int(np.count_nonzero(wall >= period)),
            "late_fraction": float(np.count_nonzero(wall >= period) / count),
            "rss_growth_mib": (psutil.Process().memory_info().rss - rss_start) / (1024 * 1024),
        }
    finally:
        engine.stop()  # This function owns only this disposable, device-free engine.


def profile(output_directory, seconds):
    frames = 512
    engine, output = prepared_engine(frames, "mixed")
    count = max(64, int(seconds * engine.sr / frames))
    profiler = cProfile.Profile()
    started = time.perf_counter()
    cpu_started = time.thread_time()
    try:
        profiler.enable()
        for _ in range(count):
            engine._callback(output, frames, None, False)
            if not np.isfinite(output).all():
                raise RuntimeError("Profiled workload produced nonfinite audio")
    finally:
        profiler.disable()
        elapsed = time.perf_counter() - started
        cpu_elapsed = time.thread_time() - cpu_started
        engine.stop()
    stream = io.StringIO()
    stats = pstats.Stats(profiler, stream=stream).strip_dirs()
    stats.dump_stats(output_directory / "mixed-512.pstats")
    stats.sort_stats("tottime").print_stats(50)
    stats.sort_stats("cumulative").print_stats(50)
    (output_directory / "hotspots.txt").write_text(stream.getvalue(), encoding="utf-8")
    functions = [
        {
            "file": file,
            "line": line,
            "function": function,
            "primitive_calls": primitive,
            "total_calls": total,
            "self_ms": own * 1000,
            "cumulative_ms": cumulative * 1000,
        }
        for (file, line, function), (primitive, total, own, cumulative, _) in stats.stats.items()
    ]
    return {
        "frames": frames,
        "workload": "mixed",
        "callbacks": count,
        "simulated_seconds": count * frames / engine.sr,
        "profiled_wall_seconds": elapsed,
        "profiled_thread_cpu_seconds": cpu_elapsed,
        "timing_includes_profiler_overhead": True,
        "top_self_time": sorted(functions, key=lambda item: item["self_ms"], reverse=True)[:50],
        "top_cumulative_time": sorted(
            functions, key=lambda item: item["cumulative_ms"], reverse=True
        )[:50],
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seconds", type=bounded_seconds, default=10.0)
    parser.add_argument("--profile-seconds", type=bounded_seconds, default=10.0)
    parser.add_argument("--output", type=Path, required=True, help="new report directory")
    args = parser.parse_args(argv)
    if NATIVE is None:
        parser.error("Build the native helper before diagnosing production audio")
    output_directory = args.output.resolve()
    if output_directory.exists():
        parser.error("Use a new report directory; existing evidence is never overwritten")
    report = {
        "purpose": "diagnostic only; no release acceptance decision",
        "acceptance_evaluated": False,
        "devices_opened": 0,
        "pacing": "unpaced simulated callbacks; not the paced release soak",
        "environment": environment(),
        "benchmarks": [],
    }
    output_directory.mkdir(parents=True)
    try:
        for frames in FRAME_SIZES:
            for workload in WORKLOADS:
                measurement = benchmark(frames, workload, args.seconds)
                report["benchmarks"].append(measurement)
                print(json.dumps(measurement), flush=True)
        report["profile"] = profile(output_directory, args.profile_seconds)
    except Exception as exc:
        report["diagnostic_error"] = f"{type(exc).__name__}: {exc}"
        raise
    finally:
        (output_directory / "diagnostic.json").write_text(
            json.dumps(report, indent=2) + "\n", encoding="utf-8"
        )
    print(f"Saved diagnostic evidence to {output_directory}; release gates were not evaluated.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
