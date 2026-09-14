"""Portable project packaging with deterministic media manifests."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import json
from pathlib import Path
import shutil

from .model import Project
from .project_media import file_sha256, referenced_sample_ids


@dataclass(frozen=True, slots=True)
class PackagedMedia:
    sample_id: str
    filename: str
    sha256: str
    uses: tuple[str, ...]


def package_project(
    project: Project,
    destination: Path,
    media_paths: dict[str, Path],
    *,
    overwrite: bool = False,
) -> Path:
    """Create a self-contained project folder without modifying source media.

    ``media_paths`` is deliberately injected by the library layer so this service
    never guesses filesystem locations from untrusted sample IDs.
    """
    destination = Path(destination)
    if destination.exists() and any(destination.iterdir()) and not overwrite:
        raise FileExistsError(f"package destination is not empty: {destination}")
    destination.mkdir(parents=True, exist_ok=True)
    media_dir = destination / "Media"
    media_dir.mkdir(exist_ok=True)

    refs = referenced_sample_ids(project)
    missing = [sample_id for sample_id in refs if sample_id not in media_paths]
    if missing:
        raise FileNotFoundError("missing media paths: " + ", ".join(missing))

    manifest: list[PackagedMedia] = []
    used_names: set[str] = set()
    for sample_id, uses in refs.items():
        source = Path(media_paths[sample_id])
        if not source.is_file():
            raise FileNotFoundError(source)
        stem = source.stem or sample_id
        suffix = source.suffix or ".wav"
        filename = f"{stem}{suffix}"
        n = 2
        while filename.casefold() in used_names:
            filename = f"{stem}-{n}{suffix}"
            n += 1
        used_names.add(filename.casefold())
        target = media_dir / filename
        shutil.copy2(source, target)
        manifest.append(
            PackagedMedia(
                sample_id=sample_id,
                filename=f"Media/{filename}",
                sha256=file_sha256(target),
                uses=uses,
            )
        )

    project_path = destination / "project.json"
    project.save(project_path)
    manifest_path = destination / "media-manifest.json"
    manifest_path.write_text(
        json.dumps(
            {
                "version": 1,
                "project": project_path.name,
                "media": [asdict(item) for item in manifest],
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    return destination
