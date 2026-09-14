#!/usr/bin/env python
"""Run a wall-clock audio callback soak with deadline and memory checks.

The default is intentionally one hour. CI and local smoke checks can shorten it:

    uv run --no-sync python scripts/soak_callback.py
    uv run --no-sync python scripts/soak_callback.py --seconds 10 --voices 8
"""

from __future__ import annotations

import argparse
import resource
import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mpclab.engine import MAX_PAD_VOICES, Engine  # noqa: E402
from scripts.bench_callback import _FakeLibrary, _prepare  # noqa: E402


def _rss_bytes() -> int:
    value = int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
    # Linux reports KiB; macOS reports bytes.
    return value if sys.platform == "darwin" else value * 1024


def soak(
    seconds: float = 3600.0,
    blocksize: int = 256,
    voices: int = 16,
    sample_rate: int = 48_000,
) -> dict[str, float | int]:
    """Exercise the real callback for wall-clock ``seconds`` and return metrics."""
    seconds = max(0.01, float(seconds))
    engine = Engine(_FakeLibrary(sample_rate), sample_rate=sample_rate, blocksize=blocksize)
    _prepare(engine, voices)
    out = np.zeros((blocksize, 2), dtype=np.float32)
    period = blocksize / sample_rate
    retrigger_every = max(1, int(0.25 / period))
    started = time.monotonic()
    rss_start = _rss_bytes()
    iterations = 0
    late = 0
    worst = 0.0
    total = 0.0

    while time.monotonic() - started < seconds:
        if iterations % retrigger_every == 0:
            for pad_index in range(min(voices, len(engine.project.pads))):
                engine.trigger_pad(pad_index, 0.8)
        before = time.perf_counter()
        engine._callback(out, blocksize, None, None)
        elapsed = time.perf_counter() - before
        total += elapsed
        worst = max(worst, elapsed)
        late += int(elapsed >= period)
        if not np.isfinite(out).all():
            raise RuntimeError(f"non-finite audio after {iterations} callbacks")
        if len(engine.voices) > MAX_PAD_VOICES:
            raise RuntimeError(f"voice bound exceeded: {len(engine.voices)} > {MAX_PAD_VOICES}")
        iterations += 1

    rss_growth = max(0, _rss_bytes() - rss_start)
    return {
        "seconds": time.monotonic() - started,
        "iterations": iterations,
        "late_callbacks": late,
        "late_fraction": late / max(1, iterations),
        "mean_ms": total / max(1, iterations) * 1000.0,
        "worst_ms": worst * 1000.0,
        "period_ms": period * 1000.0,
        "rss_growth_bytes": rss_growth,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seconds", type=float, default=3600.0)
    parser.add_argument("--blocksize", type=int, default=256)
    parser.add_argument("--voices", type=int, default=16)
    parser.add_argument("--rate", type=int, default=48_000)
    parser.add_argument("--max-late-fraction", type=float, default=0.001)
    parser.add_argument("--max-growth-mib", type=float, default=64.0)
    args = parser.parse_args()

    result = soak(args.seconds, args.blocksize, args.voices, args.rate)
    for key, value in result.items():
        print(f"{key}: {value}")
    growth_limit = args.max_growth_mib * 1024 * 1024
    if result["late_fraction"] > args.max_late_fraction:
        print("FAIL: callback deadline threshold exceeded", file=sys.stderr)
        return 1
    if result["rss_growth_bytes"] > growth_limit:
        print("FAIL: memory growth threshold exceeded", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
