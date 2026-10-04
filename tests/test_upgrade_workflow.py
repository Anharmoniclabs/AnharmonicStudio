"""Portable package replacements preserve the last usable music project."""

from pathlib import Path

import pytest

from mpclab.model import Project
from mpclab import project_package as packaging


@pytest.fixture
def package_inputs(tmp_path):
    project = Project(name="replacement")
    project.pads[0].sample_id = "a"
    project.pads[1].sample_id = "b"
    sources = {}
    for sample_id in ("a", "b"):
        source = tmp_path / f"{sample_id}.wav"
        source.write_bytes(f"new {sample_id}".encode())
        sources[sample_id] = source
    destination = tmp_path / "bundle"
    (destination / "Media").mkdir(parents=True)
    (destination / "Media" / "a.wav").write_bytes(b"old audio")
    (destination / "project.json").write_bytes(b"old project")
    (destination / "media-manifest.json").write_bytes(b"old manifest")
    (destination / "notes.txt").write_bytes(b"session notes")
    return project, destination, sources


def contents(folder):
    return {
        path.relative_to(folder).as_posix(): path.read_bytes()
        for path in folder.rglob("*")
        if path.is_file()
    }


@pytest.mark.parametrize("failure", ["copy", "project", "manifest"])
def test_staging_failure_preserves_every_existing_file(package_inputs, monkeypatch, failure):
    project, destination, sources = package_inputs
    before = contents(destination)
    original_copy = packaging.shutil.copy2
    original_write = Path.write_text

    if failure == "copy":

        def copy(source, target, *args, **kwargs):
            if Path(source) == sources["b"]:
                raise OSError("copy interrupted")
            return original_copy(source, target, *args, **kwargs)

        monkeypatch.setattr(packaging.shutil, "copy2", copy)
    elif failure == "project":

        def save(*args):
            raise OSError("save interrupted")

        monkeypatch.setattr(Project, "save", save)
    else:

        def write(path, *args, **kwargs):
            if path.name == "media-manifest.json":
                raise OSError("manifest interrupted")
            return original_write(path, *args, **kwargs)

        monkeypatch.setattr(Path, "write_text", write)

    with pytest.raises(OSError, match="interrupted"):
        packaging.package_project(project, destination, sources, overwrite=True)
    assert contents(destination) == before
    assert not list(destination.parent.glob(".bundle-stage-*"))
    assert not list(destination.parent.glob(".bundle-backup-*"))


def test_publication_failure_restores_previous_package(package_inputs, monkeypatch):
    project, destination, sources = package_inputs
    before = contents(destination)
    original_replace = packaging.os.replace

    def replace(source, target):
        if Path(source).name == "package" and Path(target) == destination:
            raise OSError("publication interrupted")
        return original_replace(source, target)

    monkeypatch.setattr(packaging.os, "replace", replace)
    with pytest.raises(OSError, match="publication interrupted"):
        packaging.package_project(project, destination, sources, overwrite=True)
    assert contents(destination) == before
    assert not list(destination.parent.glob(".bundle-backup-*"))


def test_restore_failure_retains_original_backup(package_inputs, monkeypatch):
    project, destination, sources = package_inputs
    before = contents(destination)
    original_replace = packaging.os.replace

    def replace(source, target):
        if Path(source).name in ("package", "previous"):
            raise OSError("device disappeared")
        return original_replace(source, target)

    monkeypatch.setattr(packaging.os, "replace", replace)
    with pytest.raises(RuntimeError, match="previous package retained at"):
        packaging.package_project(project, destination, sources, overwrite=True)
    backups = list(destination.parent.glob(".bundle-backup-*/previous"))
    assert len(backups) == 1
    assert contents(backups[0]) == before


def test_success_preserves_unrelated_files_and_cleans_scratch(package_inputs):
    project, destination, sources = package_inputs
    packaging.package_project(project, destination, sources, overwrite=True)
    assert (destination / "Media" / "a.wav").read_bytes() == b"new a"
    assert (destination / "Media" / "b.wav").read_bytes() == b"new b"
    assert (destination / "notes.txt").read_bytes() == b"session notes"
    assert Project.load(destination / "project.json").name == "replacement"
    assert not list(destination.parent.glob(".bundle-stage-*"))
    assert not list(destination.parent.glob(".bundle-backup-*"))


def test_media_inside_destination_is_staged_before_replacement(package_inputs):
    project, destination, sources = package_inputs
    sources["a"] = destination / "Media" / "a.wav"
    packaging.package_project(project, destination, sources, overwrite=True)
    assert (destination / "Media" / "a.wav").read_bytes() == b"old audio"
    assert (destination / "Media" / "b.wav").read_bytes() == b"new b"


def test_symlink_destination_is_rejected(package_inputs, tmp_path):
    project, destination, sources = package_inputs
    link = tmp_path / "linked-bundle"
    link.symlink_to(destination, target_is_directory=True)
    before = contents(destination)
    with pytest.raises(ValueError, match="must not be a symlink"):
        packaging.package_project(project, link, sources, overwrite=True)
    assert contents(destination) == before


def test_existing_media_symlink_cannot_modify_external_audio(package_inputs, tmp_path):
    project, destination, sources = package_inputs
    external = tmp_path / "valuable.wav"
    external.write_bytes(b"original external audio")
    target = destination / "Media" / "a.wav"
    target.unlink()
    target.symlink_to(external)
    packaging.package_project(project, destination, sources, overwrite=True)
    assert external.read_bytes() == b"original external audio"
    assert target.read_bytes() == b"new a"
    assert not target.is_symlink()


@pytest.mark.parametrize("name", ["Media", "project.json", "media-manifest.json"])
def test_managed_symlinks_never_modify_external_targets(package_inputs, tmp_path, name):
    project, destination, sources = package_inputs
    target = destination / name
    if target.is_dir():
        packaging.shutil.rmtree(target)
        external = tmp_path / "external-media"
        external.mkdir()
        (external / "a.wav").write_bytes(b"external audio")
        target.symlink_to(external, target_is_directory=True)
        before = contents(external)
    else:
        target.unlink()
        external = tmp_path / "external-document"
        external.write_bytes(b"external document")
        target.symlink_to(external)
        before = external.read_bytes()
    packaging.package_project(project, destination, sources, overwrite=True)
    assert not target.is_symlink()
    assert (contents(external) if external.is_dir() else external.read_bytes()) == before


def test_unrelated_cyclic_directory_symlink_is_preserved(package_inputs):
    project, destination, sources = package_inputs
    (destination / "cycle").symlink_to(destination, target_is_directory=True)
    packaging.package_project(project, destination, sources, overwrite=True)
    assert (destination / "cycle").is_symlink()
    assert (destination / "cycle").samefile(destination)
