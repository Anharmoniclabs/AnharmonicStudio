"""Project files fail safely and remain compatible across product renames."""

from __future__ import annotations

import json

import pytest

from mpclab.model import PROJECT_FORMAT_VERSION, Project, safe_filename


def test_project_save_is_versioned_and_round_trips(tmp_path):
    path = tmp_path / "song.json"
    source = Project(name="A real song", bpm=127.5)

    source.save(path)

    payload = json.loads(path.read_text())
    assert payload["format_version"] == PROJECT_FORMAT_VERSION
    restored = Project.load(path)
    assert restored.name == source.name
    assert restored.bpm == source.bpm
    assert not list(tmp_path.glob("*.tmp"))


def test_legacy_unversioned_project_still_loads():
    assert Project.from_dict({"name": "legacy"}).name == "legacy"


def test_newer_project_is_rejected_instead_of_silently_losing_fields():
    with pytest.raises(ValueError, match="newer than this build"):
        Project.from_dict({"format_version": PROJECT_FORMAT_VERSION + 1})


def test_browser_bundle_is_not_silently_opened_as_an_empty_native_project(tmp_path):
    document = {
        "anharmonic_bundle": 1,
        "project": Project(name="Keep my song").to_dict(),
        "media": [],
    }
    path = tmp_path / "portable.json"
    original = json.dumps(document)
    path.write_text(original)
    with pytest.raises(ValueError, match="browser Project \\+ audio bundle"):
        Project.load(path)
    assert path.read_text() == original


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("../../escape", "_.._escape"),
        ("mix / take: 1", "mix _ take_ 1"),
        ("...", "untitled"),
    ],
)
def test_safe_filename_keeps_user_names_inside_managed_folders(name, expected):
    assert safe_filename(name) == expected
