"""Bounded per-track/plugin performance telemetry for audio diagnostics."""

from __future__ import annotations

from dataclasses import dataclass
import threading
import time

import numpy as np


@dataclass(frozen=True, slots=True)
class ProfileSnapshot:
    name: str
    calls: int
    mean_ms: float
    max_ms: float
    last_ms: float


class _Metric:
    __slots__ = ("samples", "write", "count", "calls", "last")

    def __init__(self, history: int):
        self.samples = np.zeros(history, dtype=np.float64)
        self.write = 0
        self.count = 0
        self.calls = 0
        self.last = 0.0

    def add(self, seconds: float) -> None:
        value = max(0.0, float(seconds)) * 1000.0
        self.samples[self.write] = value
        self.write = (self.write + 1) % len(self.samples)
        self.count = min(len(self.samples), self.count + 1)
        self.calls += 1
        self.last = value

    def snapshot(self, name: str) -> ProfileSnapshot:
        values = self.samples[: self.count]
        return ProfileSnapshot(
            name=name,
            calls=self.calls,
            mean_ms=float(values.mean()) if self.count else 0.0,
            max_ms=float(values.max()) if self.count else 0.0,
            last_ms=float(self.last),
        )


class AudioPerformanceProfiler:
    """Opt-in fixed-history profiler.

    Registration happens outside the callback. Recording a known key only does
    a clock read plus a bounded NumPy write. Unknown callback keys are ignored
    instead of growing a dictionary on the realtime thread.
    """

    def __init__(self, history: int = 256):
        history = int(history)
        if history < 8 or history > 8192:
            raise ValueError("profiler history must be between 8 and 8192")
        self.history = history
        self.enabled = False
        self._metrics: dict[str, _Metric] = {}
        self.disk_pressure = 0.0
        self._thread = threading.local()

    def register(self, *names: str) -> None:
        for name in names:
            if not isinstance(name, str) or not name:
                raise ValueError("profiler metric names must be non-empty")
            self._metrics.setdefault(name, _Metric(self.history))

    def replace_track_set(self, track_ids) -> None:
        retained = {name: metric for name, metric in self._metrics.items() if not name.startswith("track:")}
        self._metrics = retained
        self.register(*(f"track:{track_id}" for track_id in track_ids))

    def begin(self, name: str):
        if not self.enabled or name not in self._metrics:
            return None
        return time.perf_counter_ns()

    def end(self, name: str, started) -> None:
        if started is None:
            return
        metric = self._metrics.get(name)
        if metric is not None:
            metric.add((time.perf_counter_ns() - started) * 1e-9)

    def measure_call(self, name: str, function, *args, **kwargs):
        started = self.begin(name)
        try:
            return function(*args, **kwargs)
        finally:
            self.end(name, started)

    def set_disk_pressure(self, pressure: float) -> None:
        self.disk_pressure = float(np.clip(pressure, 0.0, 1.0))

    def snapshot(self) -> dict:
        return {
            "enabled": self.enabled,
            "disk_pressure": self.disk_pressure,
            "metrics": {name: metric.snapshot(name) for name, metric in self._metrics.items()},
        }

    def reset(self) -> None:
        names = tuple(self._metrics)
        self._metrics = {name: _Metric(self.history) for name in names}
        self.disk_pressure = 0.0


class TimedProcessor:
    """Transparent process/render wrapper used by first- and third-party DSP."""

    def __init__(self, processor, profiler: AudioPerformanceProfiler, metric: str):
        self.processor = processor
        self.profiler = profiler
        self.metric = metric
        profiler.register(metric)

    def __getattr__(self, name):
        return getattr(self.processor, name)

    def process(self, *args, **kwargs):
        return self.profiler.measure_call(self.metric, self.processor.process, *args, **kwargs)

    def render(self, *args, **kwargs):
        return self.profiler.measure_call(self.metric, self.processor.render, *args, **kwargs)
