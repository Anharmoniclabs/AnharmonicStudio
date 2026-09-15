from __future__ import annotations

import numpy as np
import pytest

from mpclab.model import VocalSettings
from mpclab.vocal import PITCH_VOICING_THRESHOLD, PitchAnalysis, analyze_pitch


SAMPLE_RATE = 48_000


def _tone(midi: float, seconds: float = 0.18) -> np.ndarray:
    frequency = 440.0 * 2.0 ** ((midi - 69.0) / 12.0)
    time = np.arange(round(seconds * SAMPLE_RATE), dtype=np.float32) / SAMPLE_RATE
    mono = 0.25 * np.sin(np.float32(2.0 * np.pi * frequency) * time)
    return np.column_stack((mono, mono)).astype(np.float32)


def test_public_voicing_threshold_is_inclusive():
    threshold = np.float32(PITCH_VOICING_THRESHOLD)
    confidence = np.asarray(
        [
            np.nextafter(threshold, np.float32(0.0)),
            threshold,
            np.nextafter(threshold, np.float32(1.0)),
        ],
        dtype=np.float32,
    )
    analysis = PitchAnalysis(
        times=np.arange(3, dtype=np.float32),
        detected_hz=np.full(3, 440.0, dtype=np.float32),
        detected_midi=np.full(3, 69.0, dtype=np.float32),
        target_midi=np.full(3, 69.0, dtype=np.float32),
        confidence=confidence,
    )

    assert analysis.voiced_fraction == pytest.approx(2.0 / 3.0)


@pytest.mark.parametrize("midi", range(36, 85))
def test_clean_chromatic_pitch_goldset_has_no_octave_errors(midi):
    analysis = analyze_pitch(
        _tone(float(midi)),
        VocalSettings(low_note=36, high_note=84),
        sr=SAMPLE_RATE,
    )
    voiced = analysis.confidence >= PITCH_VOICING_THRESHOLD

    assert np.any(voiced)
    error_cents = 100.0 * np.abs(analysis.detected_midi[voiced] - midi)
    assert float(np.median(error_cents)) < 1.0
    assert not np.any(error_cents >= 600.0)


@pytest.mark.parametrize("kind", ["white", "breath"])
def test_unpitched_noise_is_not_eligible_for_correction(kind):
    generator = np.random.default_rng(0xA045)
    mono = generator.normal(0.0, 0.05, SAMPLE_RATE // 4).astype(np.float32)
    if kind == "breath":
        # A deterministic first-difference emphasizes the high-frequency,
        # breath-like spectrum without adding a DSP dependency to the fixture.
        mono = np.diff(mono, prepend=mono[0]).astype(np.float32)
    source = np.column_stack((mono, mono))

    analysis = analyze_pitch(
        source,
        VocalSettings(low_note=36, high_note=84),
        sr=SAMPLE_RATE,
    )

    assert not np.any(analysis.confidence >= PITCH_VOICING_THRESHOLD)
    assert not np.any(analysis.detected_hz > 0.0)
    assert np.isnan(analysis.detected_midi).all()
    assert np.isnan(analysis.target_midi).all()
    assert analysis.voiced_fraction == 0.0


@pytest.mark.parametrize("length", [2047, 2048, 2049, 2559, 2560, 2561])
def test_pitch_frame_and_hop_boundaries_are_stable(length):
    source = _tone(60.0, seconds=length / SAMPLE_RATE)[:length]
    analysis = analyze_pitch(
        source,
        VocalSettings(low_note=36, high_note=84),
        sr=SAMPLE_RATE,
    )
    voiced = analysis.confidence >= PITCH_VOICING_THRESHOLD

    assert len(analysis.times) == 1 + max(0, (max(length, 2048) - 2048) // 512)
    assert np.any(voiced)
    assert float(np.nanmedian(np.abs(analysis.detected_midi[voiced] - 60.0))) < 0.01
