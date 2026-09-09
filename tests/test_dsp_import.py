from pathlib import Path
from types import SimpleNamespace

import pytest

from mpclab import dsp


def _successful_run_creating(dst: Path, calls: list[list[str]]):
    def run(cmd, **kwargs):
        calls.append(cmd)
        dst.touch()
        return SimpleNamespace(returncode=0, stderr="")

    return run


def test_to_wav_prefers_soxr_and_keeps_requested_format(tmp_path, monkeypatch):
    src = tmp_path / "source.flac"
    dst = tmp_path / "converted.wav"
    calls: list[list[str]] = []
    monkeypatch.setattr(
        dsp.subprocess,
        "run",
        _successful_run_creating(dst, calls),
    )

    assert dsp.to_wav(src, dst, sr=48_000, channels=1) == dst

    assert len(calls) == 1
    cmd = calls[0]
    audio_filter = cmd[cmd.index("-af") + 1]
    assert "aresample=48000" in audio_filter
    assert "resampler=soxr" in audio_filter
    assert "precision=28" in audio_filter
    assert cmd[cmd.index("-ac") + 1] == "1"
    assert cmd[cmd.index("-ar") + 1] == "48000"
    assert cmd[cmd.index("-c:a") + 1] == "pcm_s24le"


def test_to_wav_retries_with_native_resampler_when_soxr_fails(
    tmp_path,
    monkeypatch,
):
    src = tmp_path / "source.mp3"
    dst = tmp_path / "converted.wav"
    calls: list[list[str]] = []

    def run(cmd, **kwargs):
        calls.append(cmd)
        if len(calls) == 1:
            return SimpleNamespace(
                returncode=1,
                stderr="Requested resampling engine is unavailable",
            )
        dst.touch()
        return SimpleNamespace(returncode=0, stderr="")

    monkeypatch.setattr(dsp.subprocess, "run", run)

    assert dsp.to_wav(src, dst, sr=96_000) == dst

    assert len(calls) == 2
    first_filter = calls[0][calls[0].index("-af") + 1]
    fallback_filter = calls[1][calls[1].index("-af") + 1]
    assert "resampler=soxr" in first_filter
    assert "aresample=96000" in fallback_filter
    assert "resampler=swr" in fallback_filter
    assert "filter_size=64" in fallback_filter
    assert calls[1][calls[1].index("-c:a") + 1] == "pcm_s24le"


def test_to_wav_reports_both_resampler_failures(tmp_path, monkeypatch):
    src = tmp_path / "broken.aac"
    dst = tmp_path / "converted.wav"
    results = iter(
        (
            SimpleNamespace(returncode=1, stderr="soxr unavailable"),
            SimpleNamespace(returncode=1, stderr="invalid input"),
        )
    )
    monkeypatch.setattr(
        dsp.subprocess,
        "run",
        lambda cmd, **kwargs: next(results),
    )

    with pytest.raises(dsp.AudioError) as exc_info:
        dsp.to_wav(src, dst)

    message = str(exc_info.value)
    assert "SoXR: soxr unavailable" in message
    assert "SWR fallback: invalid input" in message
