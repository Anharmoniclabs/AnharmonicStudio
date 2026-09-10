#!/usr/bin/env python
"""Bounded chronological diagnostics for intermittent paced callback overruns.

This is an instrumented, device-free run, NOT release acceptance. Stage timings
include wrapper/clock overhead; the unchanged soak_production gate must be rerun
without instrumentation after any proposed fix. Raw numeric trace contains no
user projects, plugin paths or device information.
"""

from __future__ import annotations

import argparse
from contextlib import ExitStack
import gc
import json
import math
from pathlib import Path
import sys
import time
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts.profile_production import NATIVE, environment, prepared_engine

import numpy as np
import psutil

MAX_SECONDS = 600
MAX_TRACE_BYTES = 128 * 1024 * 1024
STAGES = (
    "collect_events",
    "spawn_pad",
    "spawn_synth",
    "spawn_audio",
    "arp",
    "sample_voices",
    "synth_voices",
    "track_controls",
    "track_fx",
    "external_instrument",
    "external_effect",
    "delay",
    "reverb",
    "master_fx",
    "limiter",
)
SCALARS = (
    "wall_ms",
    "cpu_ms",
    "wake_lateness_ms",
    "elapsed_seconds",
    "beat_before",
    "beat_after",
    "pad_voices_before",
    "synth_voices_before",
    "pad_voices_after",
    "synth_voices_after",
)


def bounded_seconds(value):
    number = float(value)
    if not math.isfinite(number) or not 0.1 <= number <= MAX_SECONDS:
        raise argparse.ArgumentTypeError(f"duration must be between 0.1 and {MAX_SECONDS} seconds")
    return number


class StageTrace:
    """Preallocate and touch every numeric page before measuring RSS/timing."""

    def __init__(self, count):
        if type(count) is not int or not 1 <= count <= 112_500:
            raise ValueError("callback count exceeds bounded trace capacity")
        self.scalars = np.full((count, len(SCALARS)), np.nan, dtype=np.float64)
        self.wall = np.zeros((count, len(STAGES)), dtype=np.float64)
        self.cpu = np.zeros_like(self.wall)
        self.calls = np.zeros((count, len(STAGES)), dtype=np.uint32)
        self.gc_starts = np.zeros((count, 3), dtype=np.uint32)
        self.gc_wall = np.zeros((count, 3), dtype=np.float64)
        for values in (self.wall, self.cpu, self.calls, self.gc_starts, self.gc_wall):
            values.fill(0)
        self.bytes = sum(array.nbytes for array in self.arrays())
        if self.bytes > MAX_TRACE_BYTES:
            raise ValueError("trace exceeds memory budget")
        self.index = -1
        self.completed = 0
        self._gc_time = [0.0] * 3
        self._gc_owner = [-1] * 3

    def arrays(self):
        return self.scalars, self.wall, self.cpu, self.calls, self.gc_starts, self.gc_wall

    def wrapper(self, stage, original):
        column = STAGES.index(stage)

        def timed(*args, **kwargs):
            row = self.index
            if row < 0:
                return original(*args, **kwargs)
            start = time.perf_counter()
            cpu_start = time.thread_time()
            try:
                return original(*args, **kwargs)
            finally:
                self.cpu[row, column] += (time.thread_time() - cpu_start) * 1000
                self.wall[row, column] += (time.perf_counter() - start) * 1000
                self.calls[row, column] += 1

        return timed

    def gc_event(self, phase, info):
        generation = info.get("generation", -1)
        if generation not in (0, 1, 2):
            return
        if phase == "start":
            self._gc_owner[generation] = self.index
            self._gc_time[generation] = time.perf_counter()
            if self.index >= 0:
                self.gc_starts[self.index, generation] += 1
        elif phase == "stop":
            owner = self._gc_owner[generation]
            if owner >= 0:
                self.gc_wall[owner, generation] += (
                    time.perf_counter() - self._gc_time[generation]
                ) * 1000
            self._gc_owner[generation] = -1

    def instrument(self, engine, stack):
        from mpclab.sample_voice import PadVoice
        from mpclab.synth import SynthVoice

        targets = [
            (engine, "_collect", "collect_events"),
            (engine, "_spawn", "spawn_pad"),
            (engine, "_spawn_synth", "spawn_synth"),
            (engine, "_spawn_audio_clip", "spawn_audio"),
            (engine, "_schedule_arp", "arp"),
            (PadVoice, "render", "sample_voices"),
            (SynthVoice, "render", "synth_voices"),
            (engine, "_track_controls", "track_controls"),
            (engine.external, "render_instrument", "external_instrument"),
            (engine.external, "render_effect", "external_effect"),
            (engine.rack.delay, "process", "delay"),
            (engine.rack.reverb, "process", "reverb"),
            (engine.rack.master, "process", "master_fx"),
            (engine.mastering, "process", "limiter"),
        ]
        targets.extend((track, "process", "track_fx") for track in engine.rack.tracks)
        for target, name, stage in targets:
            stack.enter_context(
                patch.object(target, name, self.wrapper(stage, getattr(target, name)))
            )
        callback = self.gc_event
        gc.callbacks.append(callback)
        stack.callback(gc.callbacks.remove, callback)

    def summarize(self, start, end, period_ms):
        samples = self.scalars[start:end]
        wall = samples[:, 0]
        cpu = samples[:, 1]
        worst = start + int(np.argmax(wall))
        return {
            "first_callback": start,
            "callbacks": len(samples),
            "wall_p99_ms": float(np.percentile(wall, 99)),
            "wall_worst_ms": float(wall.max()),
            "cpu_p99_ms": float(np.percentile(cpu, 99)),
            "cpu_worst_ms": float(cpu.max()),
            "over_budget": int(np.count_nonzero(wall >= period_ms)),
            "wake_lateness_p99_ms": float(np.percentile(samples[:, 2], 99)),
            "max_pad_voices": int(samples[:, [6, 8]].max()),
            "max_synth_voices": int(samples[:, [7, 9]].max()),
            "gc_starts": self.gc_starts[start:end].sum(axis=0).tolist(),
            "gc_total_wall_ms": self.gc_wall[start:end].sum(axis=0).tolist(),
            "worst_callback": worst,
            "stages": {
                name: {
                    "wall_p99_ms": float(np.percentile(self.wall[start:end, column], 99)),
                    "cpu_p99_ms": float(np.percentile(self.cpu[start:end, column], 99)),
                    "total_calls": int(self.calls[start:end, column].sum()),
                }
                for column, name in enumerate(STAGES)
            },
        }

    def save(self, destination):
        count = self.completed
        np.savez_compressed(
            destination,
            scalars=self.scalars[:count],
            stage_wall_ms=self.wall[:count],
            stage_cpu_ms=self.cpu[:count],
            stage_calls=self.calls[:count],
            gc_starts=self.gc_starts[:count],
            gc_wall_ms=self.gc_wall[:count],
        )


def run_trace(seconds, frames, directory):
    engine, output = prepared_engine(frames, "mixed")
    period = frames / engine.sr
    count = max(1, int(seconds / period))
    trace = StageTrace(count)
    report = {
        "purpose": "instrumented diagnostic only; never release acceptance",
        "acceptance_evaluated": False,
        "devices_opened": 0,
        "timing_includes_instrumentation_overhead": True,
        "environment": environment(),
        "frames": frames,
        "sample_rate": engine.sr,
        "requested_seconds": seconds,
        "period_ms": period * 1000,
        "trace_allocated_bytes": trace.bytes,
        "scalar_columns": list(SCALARS),
        "stage_columns": list(STAGES),
        "stage_note": "Stage clocks include measurement overhead and may overlap when one stage calls another; do not add them as exclusive costs. Other callback work includes routing and meters. GC may overlap stage timings.",
    }
    process = psutil.Process()
    try:
        with ExitStack() as stack:
            trace.instrument(engine, stack)
            rss_start = process.memory_info().rss
            rss_peak = rss_start
            deadline = origin = time.perf_counter()
            for index in range(count):
                time.sleep(max(0, deadline - time.perf_counter()))
                started = time.perf_counter()
                values = trace.scalars[index]
                values[2:8] = (
                    max(0, started - deadline) * 1000,
                    started - origin,
                    engine.beat,
                    0,
                    len(engine.voices),
                    len(engine.synth_voices),
                )
                trace.index = index
                cpu_start = time.thread_time()
                try:
                    engine._callback(output, frames, None, False)
                finally:
                    values[1] = (time.thread_time() - cpu_start) * 1000
                    values[0] = (time.perf_counter() - started) * 1000
                    trace.index = -1
                    values[5] = engine.beat
                    values[8:] = len(engine.voices), len(engine.synth_voices)
                    trace.completed = index + 1
                if not np.isfinite(output).all():
                    raise RuntimeError("Diagnostic produced nonfinite audio")
                if index % max(1, round(1 / period)) == 0:
                    rss_peak = max(rss_peak, process.memory_info().rss)
                deadline += period
            if engine.stream is not None:
                raise RuntimeError("Diagnostic unexpectedly opened an audio stream")
            report["rss_growth_mib_after_trace_allocation"] = (rss_peak - rss_start) / 1024**2
        report["summary"] = trace.summarize(0, count, period * 1000)
        window = max(1, round(60 / period))
        report["minute_windows"] = [
            trace.summarize(start, min(count, start + window), period * 1000)
            for start in range(0, count, window)
        ]
        worst = np.argsort(trace.scalars[:, 0])[-64:][::-1]
        report["worst_callbacks"] = [
            {
                "callback": int(index),
                **dict(zip(SCALARS, trace.scalars[index].tolist(), strict=True)),
                "stage_wall_ms": dict(zip(STAGES, trace.wall[index].tolist(), strict=True)),
                "stage_cpu_ms": dict(zip(STAGES, trace.cpu[index].tolist(), strict=True)),
                "gc_wall_ms": trace.gc_wall[index].tolist(),
            }
            for index in worst
        ]
        return report
    except Exception as error:
        report["diagnostic_error"] = f"{type(error).__name__}: {error}"
        raise
    finally:
        trace.index = -1
        engine.stop()  # Only this disposable engine; it never owns a physical stream.
        report["callbacks_recorded"] = trace.completed
        trace.save(directory / "trace.npz")
        (directory / "diagnostic.json").write_text(
            json.dumps(report, indent=2) + "\n", encoding="utf-8"
        )


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seconds", type=bounded_seconds, default=600.0)
    parser.add_argument("--frames", type=int, choices=[256, 512, 1024], default=512)
    parser.add_argument("--output", type=Path, required=True, help="new evidence directory")
    args = parser.parse_args(argv)
    if NATIVE is None:
        parser.error("Build the native helper before tracing production audio")
    directory = args.output.resolve()
    if directory.exists():
        parser.error("Use a new directory; existing evidence is never overwritten")
    directory.mkdir(parents=True)
    report = run_trace(args.seconds, args.frames, directory)
    print(json.dumps(report["summary"], indent=2))
    print(f"Diagnostic saved to {directory}. Release gates were not evaluated.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
