"""Chronological diagnostics retain bounded raw data and never change release gates."""

import argparse
from contextlib import ExitStack
import gc
import json

import numpy as np
import pytest

from mpclab.engine import Engine
from mpclab.sample_voice import PadVoice
from scripts import trace_production as tracing


@pytest.mark.parametrize("value", ["nan", "inf", "0", "-1", "0.01", "601"])
def test_duration_is_bounded(value):
    with pytest.raises(argparse.ArgumentTypeError):
        tracing.bounded_seconds(value)


@pytest.mark.parametrize("count", [0, -1, 112501, True, 2.5])
def test_trace_storage_is_bounded_before_allocating(count):
    with pytest.raises(ValueError, match="bounded"):
        tracing.StageTrace(count)


def test_measurement_wrapper_records_calls_and_propagates_errors():
    trace = tracing.StageTrace(1)

    def failing():
        raise RuntimeError("owned test failure")

    wrapper = trace.wrapper("spawn_pad", failing)
    trace.index = 0
    with pytest.raises(RuntimeError, match="owned"):
        wrapper()
    index = tracing.STAGES.index("spawn_pad")
    assert trace.calls[0, index] == 1
    assert trace.wall[0, index] > 0 and trace.cpu[0, index] > 0


def test_gc_events_and_monkeypatches_are_scoped_to_owned_diagnostic():
    engine, _ = tracing.prepared_engine(512, "mixed")
    original = PadVoice.render
    callbacks = list(gc.callbacks)
    trace = tracing.StageTrace(2)
    try:
        with pytest.raises(RuntimeError, match="test interrupt"), ExitStack() as stack:
            trace.instrument(engine, stack)
            assert PadVoice.render is not original
            trace.index = 0
            trace.gc_event("start", {"generation": 1})
            trace.gc_event("stop", {"generation": 1})
            assert trace.gc_starts[0, 1] == 1
            assert trace.gc_wall[0, 1] > 0
            raise RuntimeError("test interrupt")
        assert PadVoice.render is original
        assert gc.callbacks == callbacks
    finally:
        engine.stop()


@pytest.mark.skipif(tracing.NATIVE is None, reason="Build native DSP before production tracing")
def test_paced_trace_records_real_stages_without_devices_or_acceptance(tmp_path, monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("No audio devices allowed")

    monkeypatch.setattr(Engine, "start", forbidden)
    directory = tmp_path / "trace"
    assert tracing.main(["--seconds", "0.1", "--output", str(directory)]) == 0
    report = json.loads((directory / "diagnostic.json").read_text())
    assert not report["acceptance_evaluated"] and report["devices_opened"] == 0
    assert report["timing_includes_instrumentation_overhead"]
    assert report["callbacks_recorded"] == 9
    assert report["summary"]["stages"]["synth_voices"]["total_calls"] > 0
    assert report["trace_allocated_bytes"] <= tracing.MAX_TRACE_BYTES
    with np.load(directory / "trace.npz", allow_pickle=False) as arrays:
        assert arrays["scalars"].shape == (9, len(tracing.SCALARS))
        assert np.isfinite(arrays["scalars"]).all()
        assert arrays["stage_calls"].shape == (9, len(tracing.STAGES))
        assert np.all(np.diff(arrays["scalars"][:, 3]) > 0)
    previous = (directory / "diagnostic.json").read_bytes()
    with pytest.raises(SystemExit):
        tracing.main(["--output", str(directory)])
    assert (directory / "diagnostic.json").read_bytes() == previous


def test_failed_callback_preserves_partial_trace_and_restores_hooks(tmp_path, monkeypatch):
    original = Engine._callback
    callback_count = 0

    def fail(self, output, *args):
        nonlocal callback_count
        callback_count += 1
        original(self, output, *args)
        if callback_count > 16:  # after the owned warmup
            output.fill(np.nan)

    monkeypatch.setattr(Engine, "_callback", fail)
    directory = tmp_path / "trace"
    directory.mkdir()
    callbacks = list(gc.callbacks)
    render = PadVoice.render
    with pytest.raises(RuntimeError, match="nonfinite"):
        tracing.run_trace(0.1, 512, directory)
    assert gc.callbacks == callbacks and PadVoice.render is render
    report = json.loads((directory / "diagnostic.json").read_text())
    assert "nonfinite" in report["diagnostic_error"]
    assert report["callbacks_recorded"] == 1
    assert "summary" not in report
    with np.load(directory / "trace.npz", allow_pickle=False) as arrays:
        assert arrays["scalars"].shape[0] == 1
