from __future__ import annotations

import math

import numpy as np

from mpclab.mastering_analysis import (
    LiveAnalysisTap,
    integrated_loudness,
    loudness_range,
    measure_mastering,
    spectrum,
    stereo_field,
    true_peak,
)


def tone(sample_rate=48000, seconds=1.0, frequency=1000.0, amplitude=0.1, phase=0.0):
    frames = int(sample_rate * seconds)
    t = np.arange(frames, dtype=np.float64) / sample_rate
    signal = amplitude * np.sin(2 * np.pi * frequency * t + phase)
    return np.column_stack((signal, signal)).astype(np.float32)


def test_integrated_loudness_tracks_known_gain_change():
    quiet = tone(seconds=1.2, amplitude=0.05)
    loud = quiet * np.float32(2.0)
    quiet_lufs = integrated_loudness(quiet, 48000)
    loud_lufs = integrated_loudness(loud, 48000)
    assert math.isfinite(quiet_lufs)
    assert abs((loud_lufs - quiet_lufs) - 6.0206) < 0.08


def test_silence_is_gated_out_of_integrated_loudness():
    assert integrated_loudness(np.zeros((48000, 2), np.float32), 48000) == -math.inf


def test_lra_reports_dynamic_programme_spread():
    rng = np.random.default_rng(7)
    quiet = rng.normal(0, 0.01, size=(48000 * 4, 2)).astype(np.float32)
    loud = rng.normal(0, 0.10, size=(48000 * 4, 2)).astype(np.float32)
    assert loudness_range(np.concatenate((quiet, loud)), 48000) > 4.0


def test_true_peak_reconstructs_without_under_reporting_sample_peak():
    audio = tone(seconds=0.2, frequency=997.0, amplitude=0.9, phase=0.47)
    sample_peak = float(np.max(np.abs(audio)))
    reconstructed, dbtp = true_peak(audio)
    assert reconstructed >= sample_peak * 0.995
    assert abs(dbtp - 20 * math.log10(reconstructed)) < 1e-12


def test_mastering_measurement_reports_delivery_fields():
    result = measure_mastering(tone(seconds=1.0, amplitude=0.2), 48000)
    assert math.isfinite(result.integrated_lufs)
    assert result.true_peak > 0
    assert result.true_peak_dbtp <= 1.0
    assert result.correlation > 0.999
    assert result.side_percent < 0.001


def test_stereo_field_detects_opposite_polarity():
    source = tone(seconds=0.1)[:, 0]
    field = stereo_field(np.column_stack((source, -source)))
    assert field["correlation"] < -0.999
    assert field["side_percent"] > 99.9


def test_spectrum_finds_dominant_tone_band():
    freqs, values = spectrum(tone(seconds=0.5, frequency=1000.0), 48000, bins=256)
    dominant = float(freqs[int(np.argmax(values))])
    assert 850 < dominant < 1150


def test_live_tap_is_bounded_wraps_and_resets():
    tap = LiveAnalysisTap(1000, seconds=1.0)
    first = np.column_stack((np.arange(700), np.arange(700))).astype(np.float32)
    second = np.column_stack((np.arange(700, 1400), np.arange(700, 1400))).astype(np.float32)
    assert tap.push(first)
    assert tap.push(second)
    snapshot = tap.snapshot()
    assert len(snapshot) == tap.capacity == 1024
    np.testing.assert_array_equal(snapshot[-1], [1399, 1399])
    tap.reset()
    assert tap.snapshot().shape == (0, 2)
