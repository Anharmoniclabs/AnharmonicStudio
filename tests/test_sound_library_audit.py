"""Check that the audit catches audio defects rather than just container differences."""

import numpy as np
import soundfile as sf

from scripts.audit_sound_library import audit, metrics
from scripts.build_prism_packs import finish


def test_detects_nonfinite_silence_and_mono_cancellation():
    assert metrics(np.array([np.nan]), 48000)["errors"]
    assert metrics(np.zeros(500), 48000)["errors"]
    t = np.arange(4800) / 48000
    signal = 0.5 * np.sin(2 * np.pi * 1000 * t)
    row = metrics(np.column_stack((signal, -signal)), 48000)
    assert "more than 6 dB lost in mono" in row["review"]


def test_detects_decoded_duplicates_across_containers(tmp_path):
    signal = finish(np.sin(np.arange(4800) * 0.09))
    sf.write(tmp_path / "first.wav", signal, 48000, subtype="PCM_24")
    decoded, rate = sf.read(tmp_path / "first.wav")
    sf.write(tmp_path / "renamed.flac", decoded, rate, subtype="PCM_24")
    report = audit(tmp_path)
    assert report["exact_decoded_duplicates"] == [["first.wav", "renamed.flac"]]
    assert len(report["similar_onsets"]) == 1


def test_finishing_removes_dc_after_asymmetric_fades():
    t = np.arange(48000) / 48000
    audio = finish(np.sin(2 * np.pi * 43 * t) * np.exp(-t / 0.08) + 0.02)
    assert np.abs(audio.mean(axis=0)).max() < 1e-12
    assert np.max(np.abs(audio[[0, -1]])) == 0
    assert 0.707 < np.max(np.abs(audio)) < 0.708


def test_empty_library_is_valid(tmp_path):
    assert audit(tmp_path)["files"] == 0
