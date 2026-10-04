"""Calibration integrity and route preservation without physical devices."""

import numpy as np
import pytest

from mpclab.audio_setup import correlate_loopback, estimate_loopback_latency
from mpclab.engine import Engine
from mpclab.ui.audio_setup import AudioSetupDialog


@pytest.mark.parametrize("bad", [np.nan, np.inf, -np.inf])
@pytest.mark.parametrize("measure", [estimate_loopback_latency, correlate_loopback])
@pytest.mark.parametrize("corrupt_reference", [False, True])
def test_calibration_rejects_nonfinite_audio(measure, bad, corrupt_reference):
    reference = np.zeros(1000)
    reference[80] = 0.35
    captured = reference.copy()
    (reference if corrupt_reference else captured)[160] = bad
    with pytest.raises(ValueError, match="samples"):
        measure(reference, captured, 1000)


@pytest.mark.parametrize("measure", [estimate_loopback_latency, correlate_loopback])
@pytest.mark.parametrize("rate", [0, -1000, np.nan, np.inf])
def test_calibration_rejects_invalid_sample_rate(measure, rate):
    reference = np.zeros(1000)
    reference[80] = 0.35
    with pytest.raises(ValueError, match="sample rate"):
        measure(reference, reference, rate)


def test_peak_calibration_requires_an_emitted_click():
    captured = np.zeros(1000)
    captured[120] = 0.35
    with pytest.raises(ValueError, match="stimulus is silent"):
        estimate_loopback_latency(np.zeros(1000), captured, 1000)


@pytest.mark.parametrize("channels", [1, 2, 4])
def test_correlation_counts_frames_and_tolerates_opposite_polarity_channels(channels):
    reference = np.zeros((1000, channels))
    reference[80, :] = np.where(np.arange(channels) % 2, -0.35, 0.35)
    captured = np.zeros_like(reference)
    captured[37:] = -reference[:-37]
    result = correlate_loopback(reference, captured, 1000)
    assert result.samples == 37
    assert result.milliseconds == pytest.approx(37.0)
    assert result.confidence == pytest.approx(1.0)


def test_changing_input_preserves_selected_headphone_output_pair():
    outputs = [{"label": "Interface", "key": "out", "channels": 6}]
    inputs = [
        {"label": "Interface", "key": "in", "channels": 4},
        {"label": "USB mic", "key": "mic", "channels": 1},
    ]
    dialog = AudioSetupDialog(outputs, inputs)
    dialog.select_saved("out", "in")
    dialog.select_channels((2,), (4, 5), monitor=True)
    dialog.input_box.setCurrentIndex(dialog.input_box.findData("mic"))
    assert dialog.input_channels.currentData() == ((0,), False)
    assert dialog.output_channels.currentData() == (4, 5)
    assert dialog.monitor_mode.currentData() is True


def test_changing_output_preserves_selected_recording_input():
    outputs = [
        {"label": "Interface", "key": "out", "channels": 6},
        {"label": "Speakers", "key": "speaker", "channels": 2},
    ]
    inputs = [{"label": "Interface", "key": "in", "channels": 4}]
    dialog = AudioSetupDialog(outputs, inputs)
    dialog.select_saved("out", "in")
    dialog.select_channels((2,), (4, 5))
    dialog.output_box.setCurrentIndex(dialog.output_box.findData("speaker"))
    assert dialog.input_channels.currentData() == ((2,), False)
    assert dialog.output_channels.currentData() == (0, 1)


def test_offline_buffer_change_preserves_explicit_device_without_opening_stream(monkeypatch):
    engine = Engine(object())
    monkeypatch.setattr(engine, "start", lambda *_args: pytest.fail("opened audio"))
    engine.restart(256, device=7)
    assert engine.blocksize == 256
    assert engine.output_device == 7
    assert engine.stream is None


def test_failed_offline_buffer_change_restores_device():
    engine = Engine(object())
    engine.output_device = 2
    with pytest.raises(ValueError, match="power of two"):
        engine.restart(300, device=7)
    assert engine.output_device == 2
    assert engine.blocksize == 512
