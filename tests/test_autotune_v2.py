"""Native output measurements and persistent note-plan regressions."""

from dataclasses import replace
import numpy as np
import pytest
from mpclab.model import Project, VocalSettings
from mpclab.vocal import render_autotune, ProcessingCancelled
from mpclab.autotune.shifter import shift


def tone(hz=225.0, seconds=2.0, sr=48000):
    t = np.arange(int(seconds * sr)) / sr
    mono = 0.12 * np.sin(2 * np.pi * hz * t)
    return np.column_stack((mono, -mono)).astype(np.float32)


def frequency(data, sr=48000):
    a = data[len(data) // 4 : 3 * len(data) // 4, 0]
    spectrum = abs(np.fft.rfft(a * np.hanning(len(a))))
    i = np.argmax(spectrum)
    return i * sr / len(a)


@pytest.mark.parametrize("ratio", [2 ** (-7 / 12), 2 ** (7 / 12)])
def test_native_pitch_frequency_duration_stereo(ratio):
    source = tone(220)
    result = shift(source, np.array([ratio]), np.array([0.0]), 48000, 1.0)
    assert result.shape == source.shape
    assert np.isfinite(result).all()
    assert abs(1200 * np.log2(frequency(result) / (220 * ratio))) < 15
    np.testing.assert_allclose(result[:, 0], -result[:, 1], atol=1e-6)


def test_v2_manual_target_reaches_render_and_preserves_source():
    source = tone()
    saved = source.copy()
    settings = VocalSettings(
        backend="v2",
        retune_ms=0,
        humanize=0,
        formant=1.0,
        highpass_hz=20,
        presence_db=0,
        compression=0,
        deesser=0,
        pitch_edits={"take": [dict(start=0.0, end=2.0, target=60.0, bypass=False, strength=1.0)]},
    )
    result, _ = render_autotune(source, settings, source_id="take")
    assert abs(1200 * np.log2(frequency(result) / 261.625565)) < 10
    np.testing.assert_array_equal(source, saved)
    dry, _ = render_autotune(source, replace(settings, enabled=False))
    np.testing.assert_allclose(dry, source, atol=1e-6)


def test_v2_cancel_during_native_processing():
    progress = []
    with pytest.raises(ProcessingCancelled):
        shift(
            tone(),
            np.array([1.1]),
            np.array([0.0]),
            48000,
            1.0,
            progress.append,
            lambda: bool(progress and progress[-1] > 0.2),
        )


def test_edit_plan_roundtrip_and_old_project_backend():
    project = Project()
    project.vocal.backend = "v2"
    project.vocal.pitch_edits = {
        "take": [dict(start=0.1, end=0.5, target=62.0, strength=0.8, bypass=True)]
    }
    assert Project.from_dict(project.to_dict()).vocal == project.vocal
    payload = project.to_dict()
    payload["vocal"].pop("backend")
    assert Project.from_dict(payload).vocal.backend == "legacy"


@pytest.mark.parametrize(
    "note",
    [
        dict(start=1.0, end=0.0, target=60.0),
        dict(start=0.0, end=1.0, target=float("nan")),
        dict(start=0.0, end=1.0, target=60.0, bypass=1),
    ],
)
def test_invalid_edit_plan_rejected(note):
    with pytest.raises(ValueError):
        VocalSettings(pitch_edits={"take": [note]})


def test_streaming_job_commits_recipe_with_library_clip(tmp_path):
    from mpclab.autotune.service import render
    from mpclab.library import Library

    source = tone()
    settings = VocalSettings(
        backend="v2",
        retune_ms=0,
        humanize=0,
        pitch_edits={"take": [dict(start=0.0, end=2.0, target=60.0)]},
    )
    result, analysis = render(source, settings, 48000, "take")
    try:
        import soundfile as sf

        audio, sr = sf.read(result.path, dtype="float32", always_2d=True)
        assert sr == 48000 and audio.shape == source.shape
        assert abs(1200 * np.log2(frequency(audio) / 261.625565)) < 10
        library = Library(tmp_path / "library")
        clip = library.import_file(result.path, "V2", render_recipe=result.recipe)
        restored = Library(tmp_path / "library").clips[clip.id]
        assert restored.render_recipe["source_pcm_sha256"] == result.recipe["source_pcm_sha256"]
        assert restored.render_recipe["settings"]["pitch_edits"]["take"][0]["target"] == 60
    finally:
        result.close()
    assert not result.path.exists()


def test_streaming_job_cancellation_cleans_temporary_files(tmp_path, monkeypatch):
    import tempfile
    from mpclab.autotune.service import render

    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    values = []
    with pytest.raises(ProcessingCancelled):
        render(
            tone(),
            VocalSettings(backend="v2"),
            48000,
            "take",
            values.append,
            lambda: bool(values and values[-1] > 0.5),
        )
    assert not list(tmp_path.iterdir())


def test_formant_preservation_keeps_vowel_envelope():
    sr = 48000
    t = np.arange(sr * 2) / sr
    f0 = 120.0
    mono = np.zeros(len(t))
    for harmonic in range(1, 45):
        f = harmonic * f0
        amplitude = np.exp(-0.5 * ((f - 700) / 110) ** 2) + 0.2 * np.exp(
            -0.5 * ((f - 1400) / 160) ** 2
        )
        mono += amplitude * np.sin(2 * np.pi * f * t)
    source = np.column_stack((mono, mono)).astype(np.float32) * 0.08
    ratio = 2 ** (7 / 12)
    preserved = shift(source, np.array([ratio]), np.array([0.0]), sr, 1.0)
    shifted = shift(source, np.array([ratio]), np.array([0.0]), sr, 0.0)

    def envelope_peak(audio):
        signal = audio[sr // 2 : sr * 3 // 2, 0]
        spectrum = abs(np.fft.rfft(signal * np.hanning(len(signal))))
        smoothed = np.convolve(spectrum, np.ones(181) / 181, "same")
        return np.argmax(smoothed[400:1200]) + 400

    dry_peak = envelope_peak(source)
    assert abs(envelope_peak(preserved) - dry_peak) < 130
    assert abs(envelope_peak(shifted) - dry_peak) > 200


def test_live_worker_corrects_without_changing_dry_input():
    import time
    from mpclab.autotune.live import LiveMonitor

    blocks = []
    source = tone(225, seconds=1.0)
    saved = source.copy()
    live = LiveMonitor(VocalSettings(backend="v2", retune_ms=0, humanize=0), 48000, blocks.append)
    try:
        for start in range(0, len(source), 512):
            live.push(source[start : start + 512])
            time.sleep(0.012)
        assert not live.failed, live.error
        result = np.concatenate(blocks)
        assert abs(1200 * np.log2(frequency(result) / 220)) < 25
        np.testing.assert_array_equal(source, saved)
    finally:
        live.close()
    assert not live.worker.is_alive()


def test_monitor_rate_adapter_is_continuous():
    from types import SimpleNamespace
    from mpclab.autotune.live import MonitorRoute

    blocks = []
    engine = SimpleNamespace(sr=48000, queue_monitor=lambda data, gain: blocks.append(data))
    route = MonitorRoute(SimpleNamespace(sample_rate=44100), engine, 1.0)
    source = tone(440, seconds=1.0, sr=44100)
    for start in range(0, len(source), 256):
        route(source[start : start + 256])
    result = np.concatenate(blocks)
    assert abs(len(result) - 48000) <= 2
    assert abs(frequency(result) - 440) < 3


def test_plan_uses_current_key_and_transpose():
    from mpclab.autotune.targeting import correction
    from mpclab.vocal import analyze_pitch

    original = VocalSettings(retune_ms=0, humanize=0)
    analysis = analyze_pitch(tone(), original)
    first = correction(analysis, original)
    second = correction(analysis, replace(original, transpose=12))
    np.testing.assert_allclose(second, first * 2, rtol=1e-5)
