"""Project schema migrations preserve old songs and fail closed on new formats."""

from __future__ import annotations

from copy import deepcopy

import pytest

from mpclab.project_migrations import migrate_project_document, project_format_version


@pytest.mark.parametrize("version", [0, 1, 2, 3, 4])
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


def test_negative_legacy_version_keeps_old_unversioned_compatibility():
    assert project_format_version({"format_version": -3}) == 0
    assert migrate_project_document({"format_version": -3}, target_version=4)["format_version"] == 4


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
