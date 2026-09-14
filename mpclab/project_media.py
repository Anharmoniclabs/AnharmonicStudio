"""Project media inventory used by collect/relink and diagnostics workflows."""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from typing import Iterable

from .model import Project


@dataclass(frozen=True)
class MediaReference:
    sample_id: str
    uses: tuple[str, ...]


def referenced_sample_ids(project: Project) -> dict[str, tuple[str, ...]]:
    """Return every library sample referenced by the musical document.

    The result is deterministic and records why each asset is needed, which is
    useful for Package Project, missing-media diagnostics and relink UI.
    """
    uses: dict[str, set[str]] = {}

    def add(sample_id: str | None, reason: str) -> None:
        if sample_id:
            uses.setdefault(str(sample_id), set()).add(reason)

    for index, pad in enumerate(project.pads):
        add(pad.sample_id, f"pad:{index}")
    for row_index, row in enumerate(project.rows):
        for clip in row.clips:
            if clip.kind == "audio":
                add(clip.ref, f"arrange:{row_index}:{clip.id}")
    for comp in project.vocal_comps:
        for region in comp.regions:
            add(region.source_id, f"vocal:{comp.id}:{region.id}")
        add(comp.rendered_clip_id, f"vocal-render:{comp.id}")

    return {sample_id: tuple(sorted(reasons)) for sample_id, reasons in sorted(uses.items())}


def missing_sample_ids(project: Project, available_ids: Iterable[str]) -> tuple[str, ...]:
    available = {str(item) for item in available_ids}
    return tuple(
        sample_id for sample_id in referenced_sample_ids(project) if sample_id not in available
    )


def file_sha256(path: Path, *, chunk_size: int = 1024 * 1024) -> str:
    """Hash a media file without reading the whole asset into memory."""
    digest = sha256()
    with Path(path).open("rb") as handle:
        while chunk := handle.read(chunk_size):
            digest.update(chunk)
    return digest.hexdigest()


def relink_candidates(
    missing_name: str,
    candidates: Iterable[Path],
    *,
    expected_hash: str | None = None,
) -> list[Path]:
    """Rank conservative relink candidates by exact name and optional hash.

    Hash matches always win. Without a trusted hash, only exact case-insensitive
    filenames are returned; fuzzy guessing belongs in UI and must require user
    confirmation before changing the project.
    """
    wanted = Path(missing_name).name.casefold()
    matches: list[tuple[int, str, Path]] = []
    for candidate in candidates:
        path = Path(candidate)
        if not path.is_file():
            continue
        score = 0
        if path.name.casefold() == wanted:
            score += 10
        if expected_hash:
            try:
                if file_sha256(path) == expected_hash:
                    score += 100
            except OSError:
                continue
        if score:
            matches.append((-score, str(path).casefold(), path))
    matches.sort()
    return [path for _, _, path in matches]
