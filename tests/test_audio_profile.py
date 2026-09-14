"""Diagnostic profiling is bounded, separate from gates and device-independent."""

import argparse
import json

import numpy as np
import pytest

from mpclab.engine import Engine
from scripts import profile_production


@pytest.mark.parametrize("value", ["nan", "inf", "-1", "0", "0.01", "31"])
def test_profile_duration_is_bounded(value):
    with pytest.raises(argparse.ArgumentTypeError):
        profile_production.bounded_seconds(value)


def test_nonfinite_audio_fails_diagnostic(monkeypatch):
    callback = Engine._callback

    def nonfinite(self, output, *args):
        callback(self, output, *args)
        output[:] = np.nan

    monkeypatch.setattr(Engine, "_callback", nonfinite)
    with pytest.raises(RuntimeError, match="nonfinite"):
        profile_production.benchmark(512, "mixed", 0.1)


@pytest.mark.skipif(profile_production.NATIVE is None, reason="Build native audio before profiling")
def test_short_profile_records_actual_functions_without_opening_devices(tmp_path, monkeypatch):
    def forbidden_start(*args, **kwargs):
        raise AssertionError("Diagnostics must never open audio devices")

    monkeypatch.setattr(Engine, "start", forbidden_start)
    output = tmp_path / "reports"
    assert (
        profile_production.main(
            ["--seconds", "0.1", "--profile-seconds", "0.1", "--output", str(output)]
        )
        == 0
    )
    report = json.loads((output / "diagnostic.json").read_text())
    assert report["acceptance_evaluated"] is False
    assert report["devices_opened"] == 0
    assert len(report["benchmarks"]) == 9
    assert {item["frames"] for item in report["benchmarks"]} == {256, 512, 1024}
    assert all(item["callbacks"] >= 64 for item in report["benchmarks"])
    assert report["profile"]["timing_includes_profiler_overhead"] is True
    assert any(item["function"] == "_callback" for item in report["profile"]["top_cumulative_time"])
    assert (output / "mixed-512.pstats").stat().st_size > 0
    before = (output / "diagnostic.json").read_bytes()
    with pytest.raises(SystemExit):
        profile_production.main(["--output", str(output)])
    assert (output / "diagnostic.json").read_bytes() == before
