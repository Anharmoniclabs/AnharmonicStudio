"""Project schema migrations preserve old songs and fail closed on new formats."""

from __future__ import annotations

from copy import deepcopy

import pytest

from mpclab.model import Project
from mpclab.project_migrations import migrate_project_document, project_format_version


@pytest.mark.parametrize("version", [0, 1, 2, 3])
def test_historical_project_versions_upgrade_without_losing_fields(version):
    source = {
        "name": "migration fixture",
        "bpm": 123.5,
        "patterns": [{"name": "keep me", "notes": [{"pitch": 67}]}],
        "custom_unknown_field": {"future-safe": [1, 2, 3]},
    }
    if version:
        source["format_version"] = version
    original = deepcopy(source)

    migrated = migrate_project_document(source, target_version=4)

    assert source == original
    assert migrated is not source
    assert migrated["format_version"] == 4
    assert migrated["name"] == "migration fixture"
    assert migrated["patterns"] == original["patterns"]
    assert migrated["custom_unknown_field"] == original["custom_unknown_field"]


def test_current_project_avoids_an_unnecessary_document_copy():
    source = {"format_version": 4, "name": "current", "patterns": []}

    migrated = migrate_project_document(source, target_version=4)

    assert migrated is source


def test_project_loader_migrates_legacy_document_without_mutating_it():
    source = {
        "format_version": 2,
        "name": "old song",
        "bpm": 111.0,
        "patterns": [{"name": "legacy pattern", "notes": [{"pitch": 67}]}],
    }
    original = deepcopy(source)

    loaded = Project.from_dict(source)

    assert source == original
    assert loaded.name == "old song"
    assert loaded.bpm == 111.0
    assert loaded.pattern().name == "legacy pattern"
    assert loaded.pattern().notes[0].pitch == 67


def test_negative_legacy_version_keeps_old_unversioned_compatibility():
    assert project_format_version({"format_version": -3}) == 0
    migrated = migrate_project_document({"format_version": -3}, target_version=4)
    assert migrated["format_version"] == 4


def test_invalid_version_is_rejected():
    with pytest.raises(ValueError, match="format_version must be an integer"):
        migrate_project_document({"format_version": "broken"}, target_version=4)


def test_newer_project_fails_closed():
    with pytest.raises(ValueError, match="newer than this build"):
        migrate_project_document({"format_version": 5}, target_version=4)


def test_missing_intermediate_migration_fails_instead_of_skipping_schema_changes(monkeypatch):
    from mpclab import project_migrations

    monkeypatch.delitem(project_migrations.MIGRATIONS, 2)
    with pytest.raises(ValueError, match="has no migration to 3"):
        migrate_project_document({"format_version": 2}, target_version=4)
