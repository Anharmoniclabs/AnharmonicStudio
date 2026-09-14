from __future__ import annotations

import json
import math
import os
import threading

import numpy as np
import pytest
import soundfile as sf

from mpclab import audio_analysis as analysis


@pytest.fixture
def audio_file(tmp_path):
    def create(samples, *, rate=8000, subtype="DOUBLE", name="render.wav"):
        path = tmp_path / name
        sf.write(path, np.asarray(samples), rate, subtype=subtype)
        return path

    return create


def test_reference_sine_levels_format_and_source_unchanged(audio_file):
    samples = 0.5 * np.sin(2 * np.pi * np.arange(8000) / 8)
    path = audio_file(samples)
    before = path.read_bytes()
    progress = []
    report = analysis.analyze_audio_file(path, progress=lambda *args: progress.append(args))
    assert path.read_bytes() == before
    assert report.frames == 8000
    assert report.duration_seconds == 1
    assert report.sample_rate == 8000
    assert (report.format, report.subtype, report.channel_count) == ("WAV", "DOUBLE", 1)
    assert report.sample_peak_dbfs == pytest.approx(20 * math.log10(0.5))
    assert report.rms_dbfs == pytest.approx(20 * math.log10(0.5 / math.sqrt(2)))
    assert report.channels[0].dc_offset == pytest.approx(0, abs=1e-14)
    assert report.stereo is None
    assert not report.findings
    assert progress[0] == (0, 8000)
    assert progress[-1] == (8000, 8000)
    assert "not LUFS" in report.to_text()
    assert "true peak / dBTP is not measured" in report.to_text()


@pytest.mark.parametrize("subtype,bits", [("PCM_16", 16), ("PCM_24", 24), ("PCM_32", 32)])
def test_integer_positive_ceiling_and_runs_cross_block_boundaries(audio_file, subtype, bits):
    ceiling = 1 - 2 ** (1 - bits)
    path = audio_file([0, ceiling, ceiling, -1, -1, 0, ceiling], subtype=subtype)
    report = analysis.analyze_audio_file(path, block_frames=2)
    channel = report.channels[0]
    assert report.positive_full_scale == ceiling
    assert channel.full_scale_samples == 5
    assert channel.first_full_scale_frame == 1
    assert channel.longest_full_scale_run == 4
    assert channel.sample_peak == 1
    assert channel.peak_frame == 3
    assert channel.overload_samples == 0
    assert {item.code for item in report.findings} >= {"full_scale"}


def test_float_overload_positions_dc_and_rms(audio_file):
    path = audio_file([0, 1.25, -1.5, 1, -1, 0.5], subtype="FLOAT")
    report = analysis.analyze_audio_file(path, block_frames=1)
    channel = report.channels[0]
    assert channel.overload_samples == 2
    assert channel.first_overload_frame == 1
    assert channel.full_scale_samples == 4
    assert channel.longest_full_scale_run == 4
    assert channel.peak_frame == 2
    assert channel.sample_peak == 1.5
    assert channel.dc_offset == pytest.approx(0.25 / 6)
    assert channel.rms == pytest.approx(np.sqrt(np.mean(np.square([0, 1.25, -1.5, 1, -1, 0.5]))))
    assert {item.code for item in report.findings} >= {"full_scale", "overload", "dc_offset"}


@pytest.mark.parametrize("sign", [1, -1])
def test_stereo_correlation_and_mono_cancellation(audio_file, sign):
    signal = 0.25 * np.sin(2 * np.pi * np.arange(8000) / 8)
    path = audio_file(np.column_stack((signal, sign * signal)))
    report = analysis.analyze_audio_file(path, block_frames=19)
    stereo = report.stereo
    assert stereo.valid_frames == len(signal)
    assert stereo.correlation == pytest.approx(sign, abs=1e-12)
    if sign == 1:
        assert stereo.side_rms_dbfs is None
        assert stereo.side_energy_percent == 0
        assert stereo.mono_sum_rms_dbfs == pytest.approx(report.rms_dbfs)
        assert stereo.mid_rms_dbfs == pytest.approx(report.rms_dbfs + 10 * math.log10(2))
    else:
        assert stereo.mid_rms_dbfs is None
        assert stereo.mono_sum_rms_dbfs is None
        assert stereo.side_energy_percent == 100
        assert "negative_correlation" in {item.code for item in report.findings}


def test_correlation_removes_dc_and_matches_independent_numpy_reference(audio_file):
    rng = np.random.default_rng(892)
    samples = rng.normal(0, 0.1, (4001, 2)) + [0.2, -0.3]
    path = audio_file(samples)
    report = analysis.analyze_audio_file(path, block_frames=31)
    assert report.stereo.correlation == pytest.approx(np.corrcoef(samples.T)[0, 1], abs=1e-12)
    assert abs(report.stereo.correlation) < 0.05
    for index, channel in enumerate(report.channels):
        assert channel.dc_offset == pytest.approx(samples[:, index].mean(), abs=1e-13)
        assert channel.rms == pytest.approx(np.sqrt(np.mean(samples[:, index] ** 2)), abs=1e-13)


@pytest.mark.parametrize("constant", [0.0, 0.125, 0.1])
def test_silent_or_constant_stereo_correlation_is_undefined(audio_file, constant):
    path = audio_file(np.full((101, 2), constant))
    report = analysis.analyze_audio_file(path, block_frames=3)
    assert report.stereo.correlation is None
    if constant == 0:
        assert report.rms_dbfs is None
        assert report.sample_peak_dbfs is None
        assert report.stereo.side_energy_percent is None
        assert report.channels[0].peak_frame is None
    assert "undefined (silent or constant channel)" in report.to_text()
    assert "Infinity" not in report.to_json()


def test_nonfinite_samples_are_counted_excluded_and_json_safe(audio_file):
    samples = np.array([[np.nan, 0.25], [np.inf, 0.5], [-np.inf, np.nan], [0.5, -0.5], [-0.5, 0.5]])
    path = audio_file(samples)
    report = analysis.analyze_audio_file(path, block_frames=2)
    assert report.channels[0].nonfinite_samples == 3
    assert report.channels[0].valid_samples == 2
    assert report.channels[0].first_nonfinite_frame == 0
    assert report.channels[0].rms == 0.5
    assert report.channels[0].dc_offset == 0
    assert report.channels[1].nonfinite_samples == 1
    assert report.channels[1].first_nonfinite_frame == 2
    assert report.stereo.valid_frames == 2
    assert report.stereo.correlation == pytest.approx(-1)
    assert "nonfinite" in {item.code for item in report.findings}
    payload = report.to_json()
    assert "NaN" not in payload and "Infinity" not in payload
    assert json.loads(payload)["channels"][0]["nonfinite_samples"] == 3


def test_all_invalid_samples_are_not_misreported_as_silence(audio_file):
    path = audio_file(np.full((9, 2), np.nan))
    report = analysis.analyze_audio_file(path, block_frames=2)
    assert all(channel.valid_samples == 0 for channel in report.channels)
    assert report.stereo.valid_frames == 0
    assert report.stereo.correlation is None
    assert "unavailable (no finite samples)" in report.to_text()
    assert "−∞ dBFS (silence)" not in report.to_text()
    assert json.loads(report.to_json())["rms_dbfs"] is None


def test_one_constant_channel_does_not_have_a_defined_correlation(audio_file):
    path = audio_file(np.column_stack((np.full(101, 0.1), np.linspace(-0.1, 0.1, 101))))
    assert analysis.analyze_audio_file(path, block_frames=3).stereo.correlation is None


def test_cancellation_from_final_progress_does_not_publish_success(audio_file):
    path = audio_file([0.25, -0.25])
    cancel = threading.Event()

    def cancel_at_completion(done, total):
        if done == total:
            cancel.set()

    with pytest.raises(analysis.AnalysisCancelled):
        analysis.analyze_audio_file(path, cancel=cancel, progress=cancel_at_completion)


def test_block_size_does_not_change_measurements(audio_file):
    samples = np.random.default_rng(593).normal(0.01, 0.5, (1003, 3))
    path = audio_file(samples)
    reference = analysis.analyze_audio_file(path, block_frames=1003)
    for block in (1, 7, 128, 16384):
        actual = analysis.analyze_audio_file(path, block_frames=block)
        for left, right in zip(actual.channels, reference.channels, strict=True):
            assert left.rms == pytest.approx(right.rms, abs=1e-13)
            assert left.dc_offset == pytest.approx(right.dc_offset, abs=1e-13)
            assert left.full_scale_samples == right.full_scale_samples
            assert left.longest_full_scale_run == right.longest_full_scale_run
            assert left.peak_frame == right.peak_frame
        assert actual.stereo is None


def test_reads_are_bounded_and_file_handle_closes(audio_file, monkeypatch):
    path = audio_file(np.zeros((100, 64)))
    real_soundfile = sf.SoundFile
    handles = []
    reads = []

    def open_file(*args, **kwargs):
        handle = real_soundfile(*args, **kwargs)
        original_read = handle.read

        def read(frames, **options):
            reads.append(frames)
            assert options == {"dtype": "float64", "always_2d": True}
            return original_read(frames, **options)

        handle.read = read
        handles.append(handle)
        return handle

    monkeypatch.setattr(analysis.sf, "SoundFile", open_file)
    analysis.analyze_audio_file(path, block_frames=1_048_576)
    assert reads and all(size * 64 * 8 <= analysis.MAX_BLOCK_BYTES for size in reads)
    assert all(handle.closed for handle in handles)


def test_cancellation_interrupts_next_block_and_closes_source(audio_file, monkeypatch):
    path = audio_file(np.zeros(10000))
    real_soundfile = sf.SoundFile
    cancel = threading.Event()
    handles = []
    reads = []

    def open_file(*args, **kwargs):
        handle = real_soundfile(*args, **kwargs)
        original_read = handle.read

        def read(*args, **kwargs):
            reads.append(1)
            result = original_read(*args, **kwargs)
            cancel.set()
            return result

        handle.read = read
        handles.append(handle)
        return handle

    monkeypatch.setattr(analysis.sf, "SoundFile", open_file)
    with pytest.raises(analysis.AnalysisCancelled):
        analysis.analyze_audio_file(path, block_frames=32, cancel=cancel)
    assert len(reads) == 1
    assert handles[0].closed
    assert list(path.parent.glob("*.json")) == []
    with pytest.raises(analysis.AnalysisCancelled):
        analysis.analyze_audio_file(path, cancel=cancel)
    assert len(handles) == 1


def test_source_changed_during_analysis_is_rejected(audio_file):
    path = audio_file([0.25, -0.25])
    before = path.stat()

    def mutate_metadata(done, total):
        if done == 0:
            os.utime(path, ns=(before.st_atime_ns, before.st_mtime_ns + 1_000_000_000))

    with pytest.raises(analysis.AudioAnalysisError, match="source changed"):
        analysis.analyze_audio_file(path, progress=mutate_metadata)


def test_empty_file_and_invalid_source_inputs(audio_file, tmp_path):
    empty = audio_file(np.zeros(0))
    report = analysis.analyze_audio_file(empty)
    assert report.frames == 0
    assert report.duration_seconds == 0
    assert report.channels[0].dc_offset is None
    assert report.findings[0].code == "empty"
    assert json.loads(report.to_json())["frames"] == 0
    with pytest.raises(analysis.AudioAnalysisError, match="regular audio file"):
        analysis.analyze_audio_file(tmp_path)
    with pytest.raises(FileNotFoundError):
        analysis.analyze_audio_file(tmp_path / "missing.wav")
    invalid = tmp_path / "not-audio.wav"
    invalid.write_text("not audio")
    with pytest.raises(sf.LibsndfileError):
        analysis.analyze_audio_file(invalid)


@pytest.mark.parametrize("block", [0, -1, True, 1.5, 1_048_577])
def test_invalid_block_size_is_rejected_before_opening(tmp_path, block):
    with pytest.raises(analysis.AudioAnalysisError, match="block size"):
        analysis.analyze_audio_file(tmp_path / "not-opened.wav", block_frames=block)


def test_unbounded_duration_or_sample_values_are_rejected(audio_file, monkeypatch):
    path = audio_file([0.5, -0.5])
    monkeypatch.setattr(analysis, "MAX_DURATION_SECONDS", 0)
    with pytest.raises(analysis.AudioAnalysisError, match="24 hours"):
        analysis.analyze_audio_file(path)
    monkeypatch.setattr(analysis, "MAX_DURATION_SECONDS", 24 * 60 * 60)
    excessive = audio_file([1e100], name="excessive.wav")
    with pytest.raises(analysis.AudioAnalysisError, match="magnitude"):
        analysis.analyze_audio_file(excessive)


def test_atomic_json_text_report_and_overwrite_rules(audio_file, tmp_path):
    report = analysis.analyze_audio_file(audio_file([0.25, -0.25]))
    json_path = tmp_path / "report.json"
    text_path = tmp_path / "report.txt"
    analysis.save_analysis_report(report, json_path)
    analysis.save_analysis_report(report, text_path, report_format="text")
    assert json.loads(json_path.read_text())["frames"] == 2
    assert text_path.read_text() == report.to_text()
    with pytest.raises(FileExistsError):
        analysis.save_analysis_report(report, json_path)
    analysis.save_analysis_report(report, json_path, overwrite=True)
    assert json_path.read_text() == report.to_json()
    assert not list(tmp_path.glob(".audio-analysis-*"))
    with pytest.raises(ValueError, match="format"):
        analysis.save_analysis_report(report, text_path, report_format="wav")


def test_report_cannot_replace_source_or_hard_link(audio_file, tmp_path):
    path = audio_file([0.25, -0.25])
    before = path.read_bytes()
    report = analysis.analyze_audio_file(path)
    alias = tmp_path / "audio-alias.json"
    os.link(path, alias)
    for target in (path, alias):
        with pytest.raises(analysis.AudioAnalysisError, match="source audio"):
            analysis.save_analysis_report(report, target, overwrite=True)
    assert path.read_bytes() == alias.read_bytes() == before


def test_failed_report_publication_preserves_existing_and_cleans_temporary(
    audio_file, tmp_path, monkeypatch
):
    report = analysis.analyze_audio_file(audio_file([0.25]))
    target = tmp_path / "report.json"
    target.write_text("previous report")

    def refuse_replace(*args):
        raise OSError("simulated publish failure")

    monkeypatch.setattr(analysis.os, "replace", refuse_replace)
    with pytest.raises(OSError, match="publish failure"):
        analysis.save_analysis_report(report, target, overwrite=True)
    assert target.read_text() == "previous report"
    assert not list(tmp_path.glob(".audio-analysis-*"))


def test_command_line_reports_and_clean_errors(audio_file, tmp_path, capsys):
    path = audio_file([0.25, -0.25])
    output = tmp_path / "command-report.json"
    assert analysis.main([str(path), "--json", str(output)]) == 0
    assert json.loads(output.read_text())["source"] == str(path)
    assert "SAMPLE DOMAIN" in capsys.readouterr().out
    with pytest.raises(SystemExit) as error:
        analysis.main([str(tmp_path / "missing.wav")])
    assert error.value.code == 2
    assert "Analysis failed" in capsys.readouterr().err
