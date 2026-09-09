"""Versioned, non-destructive project document migrations.

Project loading used to rely on tolerant dataclass defaults alone.  That remains
useful, but explicit migrations give future schema changes one controlled place
to preserve older songs before the model validates them.

Historical formats 0..4 intentionally require no structural rewrite: the current
model already defines their missing-field semantics through safe defaults.  The
steps are still explicit so the next format change must add a reviewed migration
instead of silently changing load behavior.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Callable

Migration = Callable[[dict], dict]


def _identity(document: dict) -> dict:
    return document


# Version 0 means an unversioned legacy project.  Keep every historical step
# explicit even when it is currently an identity migration.
MIGRATIONS: dict[int, Migration] = {
    0: _identity,
    1: _identity,
    2: _identity,
    3: _identity,
}


def project_format_version(document: dict) -> int:
    """Return the declared project version using the legacy parser contract."""
    if not isinstance(document, dict):
        raise ValueError("project root must be a JSON object")
    try:
        version = int(document.get("format_version", 0))
    except (TypeError, ValueError) as exc:
        raise ValueError("project format_version must be an integer") from exc
    # Negative versions were historically treated like unversioned documents.
    # Preserve that compatibility rather than inventing a new failure mode.
    return max(0, version)


def migrate_project_document(document: dict, *, target_version: int) -> dict:
    """Upgrade legacy documents without mutating the caller's data.

    Current-format documents are returned directly because no migration can
    mutate them.  Legacy documents are deep-copied before the first migration,
    so future schema transforms can safely edit nested structures in place.
    Newer documents fail closed so an older build cannot silently discard fields.
    """
    if type(target_version) is not int or target_version < 0:
        raise ValueError("target project format must be a non-negative integer")

    version = project_format_version(document)
    if version > target_version:
        raise ValueError(
            f"project format {version} is newer than this build supports ({target_version})"
        )
    if version == target_version:
        return document

    migrated = deepcopy(document)
    while version < target_version:
        migration = MIGRATIONS.get(version)
        if migration is None:
            raise ValueError(f"project format {version} has no migration to {version + 1}")
        migrated = migration(migrated)
        if not isinstance(migrated, dict):
            raise ValueError(f"project migration {version} returned an invalid document")
        version += 1
        migrated["format_version"] = version
    return migrated
