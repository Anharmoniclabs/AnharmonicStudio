#!/usr/bin/env python
"""Measure what the audio callback costs, without an audio device.

The transport bar shows the load of the block that just went out. That number
is too jumpy to tune a buffer size against: what decides whether a block size
is usable is the *worst* callback in a long run, because one callback over the
period is one audible click.

This drives `Engine._callback` directly, offline and as fast as the CPU allows,
so the numbers are the DSP cost alone — no PortAudio, no PipeWire, no device.
Treat them as the floor. The real thing has to survive scheduling on top.

    uv run python scripts/bench_callback.py
    uv run python scripts/bench_callback.py --blocks 128 256 512 --voices 24
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mpclab.engine import Engine  # noqa: E402
from mpclab.model import NPADS  # noqa: E402


def verdict(load_p99: float, load_max: float) -> str:
    """Stable policy classification, separated from noisy wall-clock timing."""
    if load_max >= 1.0:
        return "will glitch"
    if load_p99 > 0.5:
        return "no headroom"
    if load_p99 > 0.25:
        return "tight"
    return "comfortable"


class _FakeLibrary:
    """Enough of `library.Library` to spawn voices, with no disk behind it."""

    def __init__(self, sample_rate: int, seconds: float = 2.0):
        frames = int(sample_rate * seconds)
        t = np.arange(frames, dtype=np.float32) / sample_rate
        # A decaying tone: broadband enough to be honest about the arithmetic,
        # cheap enough that generating it is not what we are timing.
        tone = np.sin(2.0 * np.pi * 220.0 * t) * np.exp(-3.0 * t)
        self._audio = np.stack([tone, tone], axis=1).astype(np.float32)
        self.sr = sample_rate

    def audio(self, sample_id: str) -> np.ndarray:
        return self._audio

    def duration(self, sample_id: str) -> float:
        return len(self._audio) / self.sr

    def get(self, sample_id: str):
        return None


def _prepare(engine: Engine, voices: int) -> None:
    """Fill pads and hold a bounded number of them ringing at once.

    Gate mode makes each retrigger replace the prior voice for that pad.  The
    old benchmark used two-second one-shots every 250 ms, silently growing a
    requested 16-voice run to the engine's 64-voice ceiling and then labelling
    the result "16 voices".
    """
    for i in range(min(voices, NPADS)):
        pad = engine.project.pads[i]
        pad.sample_id = "bench"
        pad.name = f"bench {i}"
        pad.start = 0.0
        pad.end = 0.0
        pad.gain = 0.5
        pad.mode = "gate"
        # Detune each voice so the interpolating path runs, not the fast
        # rate == 1.0 memcpy path. This is the expensive branch, and the one
        # that matters once pads are pitched.
        pad.pitch = (i % 12) - 6
        pad.track = i % 8


def bench(blocksize: int, voices: int, seconds: float, sample_rate: int = 48_000) -> dict:
    lib = _FakeLibrary(sample_rate)
    engine = Engine(lib, sample_rate=sample_rate, blocksize=blocksize)
    _prepare(engine, voices)

    out = np.zeros((blocksize, 2), dtype=np.float32)
    period = blocksize / sample_rate
    iterations = max(64, int(seconds / period))

    # Retrigger every voice a few times a second, so voice spawning and the
    # command queue are inside the measurement rather than a one-off at start.
    retrigger_every = max(1, int(0.25 / period))

    times = np.zeros(iterations, dtype=np.float64)
    for i in range(iterations):
        if i % retrigger_every == 0:
            for pad_index in range(min(voices, NPADS)):
                engine.trigger_pad(pad_index, 0.8)
        t0 = time.perf_counter()
        engine._callback(out, blocksize, None, None)
        times[i] = time.perf_counter() - t0

    # The first few callbacks pay for numpy warm-up and page faults; they are
    # real, but they are not what a steady-state buffer decision rests on.
    steady = times[8:] if len(times) > 16 else times
    ms = steady * 1000.0
    period_ms = period * 1000.0
    worst = float(ms.max())
    return {
        "blocksize": blocksize,
        "period_ms": period_ms,
        "p50": float(np.percentile(ms, 50)),
        "p99": float(np.percentile(ms, 99)),
        "max": worst,
        "load_p99": float(np.percentile(ms, 99)) / period_ms,
        "load_max": worst / period_ms,
        "iterations": len(steady),
    }


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument(
        "--blocks",
        type=int,
        nargs="+",
        default=[1024, 512, 256, 128, 64],
        help="block sizes to test (frames)",
    )
    ap.add_argument("--voices", type=int, default=16, help="pad voices held ringing at once")
    ap.add_argument(
        "--seconds", type=float, default=6.0, help="simulated audio seconds per block size"
    )
    ap.add_argument("--rate", type=int, default=48_000)
    args = ap.parse_args()

    print(f"{args.voices} voices · {args.rate} Hz · {args.seconds:.0f} s per block size")
    print("(DSP cost only — no device, no scheduler. This is the floor.)\n")

    header = (
        f"{'block':>6} {'period':>9} {'p50':>9} {'p99':>9} {'worst':>9} {'load p99':>9}  verdict"
    )
    print(header)
    print("-" * len(header))

    results = []
    for blocksize in args.blocks:
        r = bench(blocksize, args.voices, args.seconds, args.rate)
        results.append(r)
        result_verdict = verdict(r["load_p99"], r["load_max"])
        print(
            f"{r['blocksize']:>6} {r['period_ms']:>8.2f}ms {r['p50']:>8.2f}ms "
            f"{r['p99']:>8.2f}ms {r['max']:>8.2f}ms {r['load_p99']:>8.0%}  {result_verdict}"
        )

    usable = [r for r in results if r["load_p99"] <= 0.5 and r["load_max"] < 1.0]
    print()
    if usable:
        best = min(usable, key=lambda r: r["blocksize"])
        print(
            f"Smallest block with headroom to spare: {best['blocksize']} frames "
            f"({best['period_ms']:.2f} ms)."
        )
    else:
        print("No tested block size has headroom. The callback is the bottleneck, not the buffer.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
