"""Generated mathematical references from EBU Tech 3341 (2023), not EBU recordings.

Reference definitions/tolerances: https://tech.ebu.ch/docs/tech/tech3341.pdf
These generated tones are tests, not a claim of complete EBU Mode certification.
"""

from __future__ import annotations

import math
import threading

import numpy as np
import pytest
import soundfile as sf

from mpclab import loudness
from mpclab.audio_analysis import AnalysisCancelled, AudioAnalysisError, analyze_audio_file
from mpclab.runtime_paths import media_tool


@pytest.fixture
def tone_file(tmp_path):
    def make(
        segments=((20, -23),),
        *,
        rate=48000,
        channels=2,
        frequency=1000,
        phase=0,
        name="tone.wav",
        fade=False,
    ):
        path = tmp_path / name
        with sf.SoundFile(path, "w", samplerate=rate, channels=channels, subtype="FLOAT") as output:
            for seconds, level in segments:
                frames = round(seconds * rate)
                amplitude = 10 ** (level / 20) if level is not None else 0
                for start in range(0, frames, rate):
                    positions = np.arange(start, min(start + rate, frames))
                    signal = amplitude * np.sin(2 * np.pi * frequency * positions / rate + phase)
                    if fade:
                        signal *= np.clip(
                            np.minimum(positions, frames - 1 - positions) / (rate * 0.01), 0, 1
                        )
                    output.write(np.repeat(signal[:, None], channels, axis=1))
        return path

    return make


@pytest.fixture(autouse=True)
def require_ffmpeg():
    if not media_tool("ffmpeg"):
        pytest.skip("FFmpeg is required for the packaged standards-based scanner.")


@pytest.mark.parametrize("level", [-23, -33])
def test_ebu_3341_cases_1_and_2_calibrated_integrated_momentary_shortterm(tone_file, level):
    report = loudness.measure_loudness(tone_file(((20, level),)))
    assert report.integrated_lufs == pytest.approx(level, abs=0.1)
    assert report.momentary_max_lufs == pytest.approx(level, abs=0.1)
    assert report.short_term_max_lufs == pytest.approx(level, abs=0.1)
    assert report.true_peak_dbtp == pytest.approx(level, abs=0.1)
    assert report.loudness_range_lu == pytest.approx(0, abs=0.1)
    assert not report.lra_stable
    assert len(report.history) == 200
    assert report.history[0].momentary_lufs is None
    assert report.history[2].momentary_lufs is None
    assert report.history[3].momentary_lufs == pytest.approx(level, abs=0.1)
    assert report.history[28].short_term_lufs is None
    assert report.history[29].short_term_lufs == pytest.approx(level, abs=0.1)
    assert report.history[-1].end_seconds == pytest.approx(20)
    assert "not yet stable" in report.to_text()
    assert report.ffmpeg_version.startswith("ffmpeg version")


@pytest.mark.parametrize(
    "segments",
    [((10, -36), (60, -23), (10, -36)), ((10, -72), (10, -36), (60, -23), (10, -36), (10, -72))],
)
def test_ebu_3341_cases_3_and_4_absolute_and_relative_gating(tone_file, segments):
    report = loudness.measure_loudness(tone_file(segments))
    assert report.integrated_lufs == pytest.approx(-23, abs=0.1)
    assert report.lra_stable
    assert report.true_peak_over_windows == 0


def test_ebu_3341_case_5_gated_energy_average(tone_file):
    report = loudness.measure_loudness(tone_file(((20, -26), (20.1, -20), (20, -26))))
    assert report.integrated_lufs == pytest.approx(-23, abs=0.1)


@pytest.mark.parametrize("rate", [44100, 48000, 96000])
@pytest.mark.parametrize(
    "divisor,phase,amplitude,expected",
    [(4, 0, 0.5, -6), (4, 45, 0.5, -6), (6, 60, 0.5, -6), (8, 67.5, 0.5, -6), (4, 45, 1.41, 3)],
)
def test_ebu_3341_true_peak_cases_15_to_19(tone_file, rate, divisor, phase, amplitude, expected):
    path = tone_file(
        ((1, 20 * math.log10(amplitude)),),
        rate=rate,
        frequency=rate / divisor,
        phase=math.radians(phase),
        fade=True,
    )
    report = loudness.measure_loudness(path)
    assert expected - 0.4 <= report.true_peak_dbtp <= expected + 0.2
    if expected > 0:
        assert report.true_peak_over_windows > 0
        assert report.first_true_peak_over_seconds is not None
        # The above-full-scale reconstructed signal is hidden between stored samples.
        assert analyze_audio_file(path).sample_peak_dbfs < 0


def test_mono_is_not_silently_counted_as_dual_mono(tone_file):
    mono = loudness.measure_loudness(tone_file(((4, -23),), channels=1))
    assert mono.integrated_lufs == pytest.approx(-26, abs=0.1)
    assert mono.dual_mono is False


def test_silence_and_too_short_files_have_explicit_unavailable_loudness(tone_file):
    silent = loudness.measure_loudness(tone_file(((4, None),)))
    assert silent.integrated_lufs is None
    assert silent.true_peak_dbtp is None
    assert silent.momentary_max_lufs is None
    assert silent.short_term_max_lufs is None
    assert silent.loudness_range_lu is None
    short = loudness.measure_loudness(tone_file(((0.31, -23),)))
    assert short.integrated_lufs is None
    assert short.momentary_max_lufs is None
    assert short.short_term_max_lufs is None
    assert short.true_peak_dbtp == pytest.approx(-23, abs=0.1)


def test_true_peak_flush_includes_last_sample_without_padding_loudness(tone_file):
    path = tone_file(((0.31, None),), channels=1)
    with sf.SoundFile(path, "r+") as output:
        output.seek(-1, sf.SEEK_END)
        output.write([0.8])
    report = loudness.measure_loudness(path)
    assert report.true_peak_dbtp is not None
    assert report.true_peak_dbtp >= 20 * math.log10(0.8) - 0.1
    assert report.integrated_lufs is None


def test_history_bound_retains_last_point_and_all_window_maxima(tone_file, monkeypatch):
    monkeypatch.setattr(loudness, "MAX_HISTORY_POINTS", 10)
    report = loudness.measure_loudness(tone_file(((2, -33), (2, -23), (2, -33))))
    assert len(report.history) <= 10
    assert report.history_step_seconds > 0.1
    assert report.measured_windows == 60
    assert report.history[-1].end_seconds == pytest.approx(6)
    assert report.momentary_max_lufs == pytest.approx(-23, abs=0.1)


def test_each_measurement_resets_persistent_overs(tone_file):
    overloaded = loudness.measure_loudness(tone_file(((1, 3),)))
    normal = loudness.measure_loudness(tone_file(((1, -23),)))
    assert overloaded.true_peak_over_windows > 0
    assert normal.true_peak_over_windows == 0
    assert normal.first_true_peak_over_seconds is None


def test_layout_rate_invalid_data_dependency_and_cancellation_fail_explicitly(
    tone_file, monkeypatch
):
    with pytest.raises(AudioAnalysisError, match="mono/stereo"):
        loudness.measure_loudness(tone_file(((1, -23),), channels=3))
    with pytest.raises(AudioAnalysisError, match="delivery files"):
        loudness.measure_loudness(tone_file(((1, -23),), rate=8000))
    path = tone_file(((1, -23),))
    event = threading.Event()
    event.set()
    with pytest.raises(AnalysisCancelled):
        loudness.measure_loudness(path, cancel=event)
    monkeypatch.setattr(loudness, "media_tool", lambda name: None)
    with pytest.raises(AudioAnalysisError, match="FFmpeg is unavailable"):
        loudness.measure_loudness(path)
