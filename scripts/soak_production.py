#!/usr/bin/env python
"""Paced mixed piano/pad/FX test, optionally through an explicitly silent stream.

The live option requires an existing 48 kHz/256-frame PipeWire graph and never
forces graph settings, routes devices, or changes an existing application's stream.
"""

import argparse
import json
import math
from pathlib import Path
import subprocess
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from mpclab.production_runtime import configure

configure()
import numpy as np
from scripts.bench_production import production_engine
from mpclab.native_dsp import STATUS


def rss_mib():
    try:
        for line in Path("/proc/self/status").read_text().splitlines():
            if line.startswith("VmRSS:"):
                return int(line.split()[1]) / 1024
    except OSError:
        pass
    return None


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seconds", type=float, default=60)
    parser.add_argument("--frames", type=int, choices=[128, 256, 512, 1024], default=512)
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--json", type=Path)
    parser.add_argument("--max-late-fraction", type=float, default=0)
    parser.add_argument("--max-p99-load", type=float, default=1)
    parser.add_argument("--max-growth-mib", type=float, default=64)
    args = parser.parse_args()
    if not math.isfinite(args.seconds) or args.seconds <= 0:
        parser.error("seconds must be finite and positive")
    if not 0 <= args.max_late_fraction <= 1 or not 0 < args.max_p99_load <= 1:
        parser.error("deadline limits must be fractions between 0 and 1")
    if not math.isfinite(args.max_growth_mib) or args.max_growth_mib < 0:
        parser.error("memory growth limit must be finite and nonnegative")
    engine = production_engine(frames=args.frames)
    # Exercise the loop seam repeatedly, including effects and metronome.
    engine.project.loop_start, engine.project.loop_end = 0, 4
    engine.loop_song = True
    engine.metronome = True
    out = np.zeros((args.frames, 2), dtype=np.float32)
    for _ in range(16):
        engine._callback(out, args.frames, None, False)
    engine.beat = 0
    engine.voices.clear()
    engine.synth_voices.clear()
    engine.reset_timing()
    period = args.frames / engine.sr
    measurements, cpu_measurements, lateness = [], [], []
    slow_blocks = []
    invalid = 0
    callback_errors = []
    stream_stayed_active = True
    rss_start = rss_mib()
    rss_peak = rss_start
    if args.live:
        if args.frames not in (256, 512):
            parser.error("Live validation supports 256/512 callbacks on the existing graph")
        metadata = subprocess.run(
            ["pw-metadata", "-n", "settings"], check=True, capture_output=True, text=True, timeout=5
        ).stdout
        if (
            "key:'clock.rate' value:'48000'" not in metadata
            or "key:'clock.quantum' value:'256'" not in metadata
        ):
            raise RuntimeError("Expected an existing 48000/256 graph; no settings changed")
        # Retain the current host request even for a 512-frame PortAudio
        # callback. The adapter may buffer extra audio; report its estimate.
        configure_host = engine.linux_audio.configure_host
        engine.linux_audio.configure_host = lambda frames, rate: configure_host(256, rate)
        # Replace only this test engine's callback before it opens its own stream.
        render = engine._callback

        def silent_callback(output, frames, time_info, status):
            nonlocal invalid
            start = time.perf_counter()
            cpu_start = time.thread_time()
            try:
                render(output, frames, time_info, status)
                invalid += int(not np.isfinite(output).all())
            except Exception as exc:
                # PortAudio otherwise prints callback exceptions and can leave
                # a short, apparently successful measurement behind.
                if not callback_errors:
                    callback_errors.append(f"{type(exc).__name__}: {exc}")
            finally:
                output.fill(0)  # test audio never reaches speakers/headphones
            measurements.append(time.perf_counter() - start)
            cpu_measurements.append(time.thread_time() - cpu_start)

        engine._callback = silent_callback
        engine.start(device="pipewire")
        output_latency = engine.stream.latency
        try:
            deadline = time.perf_counter() + args.seconds
            while time.perf_counter() < deadline:
                if not engine.stream.active:
                    stream_stayed_active = False
                    break
                current_rss = rss_mib()
                if current_rss is not None:
                    rss_peak = max(rss_peak or 0, current_rss)
                time.sleep(min(0.1, max(0, deadline - time.perf_counter())))
        finally:
            engine.stop()  # only the stream created by this test
    else:
        deadline = time.perf_counter()
        for index in range(max(1, int(args.seconds / period))):
            time.sleep(max(0, deadline - time.perf_counter()))
            start = time.perf_counter()
            lateness.append(max(0, start - deadline))
            beat_before = engine.beat
            cpu_start = time.thread_time()
            engine._callback(out, args.frames, None, False)
            elapsed = time.perf_counter() - start
            cpu_elapsed = time.thread_time() - cpu_start
            measurements.append(elapsed)
            cpu_measurements.append(cpu_elapsed)
            if elapsed >= period and len(slow_blocks) < 32:
                slow_blocks.append(
                    dict(
                        block=index,
                        wall_ms=elapsed * 1000,
                        thread_cpu_ms=cpu_elapsed * 1000,
                        loop_wrap=engine.beat < beat_before,
                    )
                )
            invalid += int(not np.isfinite(out).all())
            if index % max(1, int(1 / period)) == 0:
                current_rss = rss_mib()
                if current_rss is not None:
                    rss_peak = max(rss_peak or 0, current_rss)
            deadline += period
    values = np.asarray(measurements) * 1000
    result = dict(
        backend=STATUS,
        live_silent=args.live,
        frames=args.frames,
        seconds=args.seconds,
        blocks=len(values),
        period_ms=period * 1000,
        p99_ms=float(np.percentile(values, 99)) if len(values) else None,
        worst_ms=float(values.max()) if len(values) else None,
        dsp_over_budget=int(np.count_nonzero(values >= period * 1000)),
        xruns=engine.underruns if args.live else None,
        nonfinite_blocks=invalid,
        scheduler=engine.linux_audio.status.scheduler,
        output_latency_ms=output_latency * 1000 if args.live else None,
        host_request=engine.linux_audio.status.quantum if args.live else None,
        wake_lateness_p99_ms=float(np.percentile(lateness, 99) * 1000) if lateness else None,
        rss_start_mib=rss_start,
        rss_peak_mib=rss_peak,
        rss_growth_mib=max(0, rss_peak - rss_start) if rss_start is not None else None,
        thread_cpu_p99_ms=float(np.percentile(cpu_measurements, 99) * 1000)
        if cpu_measurements
        else None,
        thread_cpu_worst_ms=float(max(cpu_measurements) * 1000) if cpu_measurements else None,
        first_slow_blocks=slow_blocks,
        callback_errors=callback_errors,
        stream_stayed_active=stream_stayed_active if args.live else None,
    )
    result["late_fraction"] = result["dsp_over_budget"] / max(1, len(values))
    result["passed"] = bool(
        len(values) > 0
        and invalid == 0
        and not callback_errors
        and stream_stayed_active
        and (not args.live or len(values) >= int(args.seconds / period * 0.98))
        and (not args.live or engine.underruns == 0)
        and result["late_fraction"] <= args.max_late_fraction
        and result["p99_ms"] <= result["period_ms"] * args.max_p99_load
        and (result["rss_growth_mib"] is None or result["rss_growth_mib"] <= args.max_growth_mib)
    )
    print(json.dumps(result, indent=2))
    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(json.dumps(result, indent=2) + "\n")
    return int(not result["passed"])


if __name__ == "__main__":
    raise SystemExit(main())
