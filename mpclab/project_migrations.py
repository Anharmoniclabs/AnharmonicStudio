"""Versioned, non-destructive project document migrations.

Project loading used to rely on tolerant dataclass defaults alone. That remains
useful, but explicit migrations give future schema changes one controlled place
to preserve older songs before the model validates them.

Historical upgrades through format 4 require no structural rewrite: the model
defines their missing-field semantics through safe defaults. Those steps remain
explicit. Format 5 adds stable mixer identities while retaining the existing
integer routing fields.
"""

from __future__ import annotations

import math
from copy import deepcopy
from typing import Callable

Migration = Callable[[dict], dict]


def _identity(document: dict) -> dict:
    return document


def legacy_mixer_track_id(index: int) -> str:
    """Project-local identity for an original fixed mixer slot."""
    return f"mixer:{index:02d}"


def _mixer_track_ids(document: dict) -> dict:
    tracks = document.get("tracks")
    # Let the model report malformed collections and enforce its safety limit.
    if isinstance(tracks, list):
        for index, track in enumerate(tracks):
            if isinstance(track, dict):
                track.setdefault("id", legacy_mixer_track_id(index))
    return document


def _validate_pattern_step_shapes(document: dict) -> None:
    """Fail closed before the model converts nested step-map values.

    The model intentionally accepts legacy JSON keys such as ``"0"`` and turns
    them into integers. Validate the nested collection shape and scalar values
    here so malformed user documents never leak AttributeError/TypeError from
    ``row.items()`` or ``float(value)`` during that conversion.
    """
    patterns = document.get("patterns")
    if not isinstance(patterns, list):
        return
    for pattern_index, pattern in enumerate(patterns):
        if not isinstance(pattern, dict):
            continue
        steps = pattern.get("steps")
        if steps is None or not isinstance(steps, dict):
            continue
        for pad_key, row in steps.items():
            if not isinstance(row, dict):
                raise ValueError(
                    f"project patterns[{pattern_index}].steps[{pad_key!s}] must be an object"
                )
            try:
                pad_index = int(pad_key)
            except (TypeError, ValueError, OverflowError) as exc:
                raise ValueError(
                    f"project patterns[{pattern_index}].steps pad keys must be integers"
                ) from exc
            if pad_index < 0:
                raise ValueError(
                    f"project patterns[{pattern_index}].steps pad keys cannot be negative"
                )
            for step_key, raw_velocity in row.items():
                try:
                    step_index = int(step_key)
                except (TypeError, ValueError, OverflowError) as exc:
                    raise ValueError(
                        f"project patterns[{pattern_index}].steps[{pad_key!s}] step keys "
                        "must be integers"
                    ) from exc
                if step_index < 0:
                    raise ValueError(
                        f"project patterns[{pattern_index}].steps[{pad_key!s}] step keys "
                        "cannot be negative"
                    )
                try:
                    velocity = float(raw_velocity)
                except (TypeError, ValueError, OverflowError) as exc:
                    raise ValueError(
                        f"project patterns[{pattern_index}].steps[{pad_key!s}] velocities "
                        "must be finite numbers"
                    ) from exc
                if not math.isfinite(velocity):
                    raise ValueError(
                        f"project patterns[{pattern_index}].steps[{pad_key!s}] velocities "
                        "must be finite numbers"
                    )


# Version 0 means an unversioned legacy project. Keep every historical step
# explicit even when it is currently an identity migration.
MIGRATIONS: dict[int, Migration] = {
    0: _identity,
    1: _identity,
    2: _identity,
    3: _identity,
    4: _mixer_track_ids,
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
    mutate them. Legacy documents are deep-copied before the first migration,
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
    _validate_pattern_step_shapes(document)
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
    _validate_pattern_step_shapes(migrated)
    return migrated
