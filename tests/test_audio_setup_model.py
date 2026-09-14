"""Pure policy tests for first-run audio setup and adaptive warnings."""

import numpy as np
import pytest

from mpclab.audio_setup import (
    assess_audio_health,
    estimate_loopback_latency,
    loopback_stimulus,
    next_buffer_profile,
    recommended_buffer,
)


def test_workflow_recommendations_keep_128_explicitly_opt_in():
    assert recommended_buffer("build") == 512
    assert recommended_buffer("production") == 256
    assert recommended_buffer("live") == 128
    assert recommended_buffer("unknown") == 512


def test_loopback_measurement_reports_round_trip_samples_and_confidence():
    stimulus = loopback_stimulus(sample_rate=1_000, duration=0.5)
    captured = np.zeros_like(stimulus)
    delay = 37
    captured[delay:] = stimulus[:-delay]

    result = estimate_loopback_latency(stimulus, captured, sample_rate=1_000)

    assert result.samples == delay
    assert result.milliseconds == pytest.approx(37.0)
    assert result.confidence > 0.8


def test_loopback_rejects_silence_instead_of_saving_false_calibration():
    stimulus = loopback_stimulus(sample_rate=1_000, duration=0.5)
    with pytest.raises(ValueError, match="not detected"):
        estimate_loopback_latency(stimulus, np.zeros_like(stimulus), 1_000)


def test_audio_health_escalates_one_profile_for_xrun_or_hot_p99():
    assert next_buffer_profile(128) == 256
    assert next_buffer_profile(1024) is None
    healthy = assess_audio_health(256, {"period": 5.33, "p99": 2.0, "xruns": 0})
    hot = assess_audio_health(256, {"period": 5.33, "p99": 4.8, "xruns": 0})
    xrun = assess_audio_health(512, {"period": 10.66, "p99": 3.0, "xruns": 1})

    assert not healthy.unsafe
    assert (hot.unsafe, hot.recommended_frames) == (True, 512)
    assert (xrun.unsafe, xrun.recommended_frames) == (True, 1024)


def test_audio_health_warns_before_deadline_and_for_isolated_spikes():
    assert assess_audio_health(256, {"period": 5.33, "p99": 3.0}).unsafe
    spike = assess_audio_health(256, {"period": 5.33, "p99": 1.0, "max": 5.34})
    assert spike.unsafe
    assert "worst callback" in spike.reason
