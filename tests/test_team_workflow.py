"""Failure-path regressions for media storage and portable music workflows."""

from dataclasses import replace
import numpy as np
import pytest
import soundfile as sf

from mpclab.library import Library
from mpclab.model import Project
from mpclab.project_package import package_project


@pytest.mark.parametrize("value", [float("nan"), float("inf"), -float("inf")])
def test_invalid_generated_audio_does_not_create_media(tmp_path, value):
    library = Library(tmp_path / "library", sample_rate=8000)
    with pytest.raises(ValueError, match="finite samples"):
        library.add_audio(np.array([[0.1, value]], dtype=np.float32), "broken take")
    assert not library.clips
    assert not library._audio
    assert not list(library.root.glob("*/audio.wav"))


@pytest.mark.parametrize("rate", [float("nan"), float("inf")])
def test_invalid_capture_rate_does_not_create_media(tmp_path, rate):
    library = Library(tmp_path / "library", sample_rate=8000)
    with pytest.raises(ValueError, match="source sample rate"):
        library.add_audio(np.zeros((8, 2)), "broken take", source_sample_rate=rate)
    assert not library.clips
    assert not list(library.root.glob("*/audio.wav"))


def test_metadata_write_failure_keeps_catalog_entry(tmp_path, monkeypatch):
    library = Library(tmp_path / "library", sample_rate=8000)
    clip = library.add_audio(np.zeros((8, 2)), "original")

    def failed_write(_clip):
        raise OSError("disk full")

    monkeypatch.setattr(library, "_write_meta", failed_write)
    with pytest.raises(OSError, match="disk full"):
        library.update(replace(clip, name="replacement"))
    assert library.clips[clip.id].name == "original"


def test_package_missing_media_preserves_existing_bundle(tmp_path):
    project = Project(name="portable")
    project.pads[0].sample_id = "a"
    project.pads[1].sample_id = "z"
    source = tmp_path / "Kick.wav"
    source.write_bytes(b"new audio")
    destination = tmp_path / "bundle"
    media = destination / "Media"
    media.mkdir(parents=True)
    (media / "Kick.wav").write_bytes(b"previous audio")
    with pytest.raises(FileNotFoundError):
        package_project(
            project,
            destination,
            {"a": source, "z": tmp_path / "disconnected-drive.wav"},
            overwrite=True,
        )
    assert (media / "Kick.wav").read_bytes() == b"previous audio"


def test_package_missing_mapping_does_not_create_destination(tmp_path):
    project = Project()
    project.pads[0].sample_id = "missing"
    destination = tmp_path / "bundle"
    with pytest.raises(FileNotFoundError, match="missing media paths"):
        package_project(project, destination, {})
    assert not destination.exists()


def test_nonfinite_export_keeps_previous_file_and_removes_temporary(tmp_path, monkeypatch):
    import mpclab.export as exporter

    class BrokenEngine:
        sr = 8000

        def __init__(self, library, *, sample_rate):
            pass

        def iter_offline_blocks(self, *args):
            yield np.zeros((8, 2), dtype=np.float32)
            yield np.full((8, 2), np.nan, dtype=np.float32)

    monkeypatch.setattr(exporter, "Engine", BrokenEngine)
    destination = tmp_path / "master.wav"
    destination.write_bytes(b"previous master")
    with pytest.raises(ValueError, match="non-finite audio"):
        exporter.render_export(Project(), Library(tmp_path / "library"), destination)
    assert destination.read_bytes() == b"previous master"
    assert not list(tmp_path.glob(".render-*.wav"))


def test_export_preserves_library_clock_domain(tmp_path):
    from mpclab.export import render_export

    library = Library(tmp_path / "library", sample_rate=44_100)
    project = Project()
    destination = tmp_path / "master.wav"
    duration = render_export(project, library, destination, mode="pattern", tail=0)
    info = sf.info(destination)
    expected_seconds = project.pattern().length_beats * 60 / project.bpm
    assert info.samplerate == 44_100
    assert info.frames == round(expected_seconds * 44_100)
    assert duration == pytest.approx(expected_seconds, abs=1 / 44_100)
