"""Portable project packaging with deterministic media manifests."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import json
import logging
import os
from pathlib import Path
import shutil
import tempfile

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
    never guesses filesystem locations from untrusted sample IDs. The complete
    replacement is prepared beside the destination. Ordinary copy/write failures
    leave the existing folder untouched; publication failure restores its backup.
    The two directory renames are not crash-atomic: an interruption may leave a
    ``.<name>-backup-*`` folder requiring recovery. Concurrent writers are not
    supported.
    """
    destination = Path(destination)
    if destination.is_symlink():
        raise ValueError("package destination must not be a symlink")
    if destination.exists() and not destination.is_dir():
        raise NotADirectoryError(destination)
    if destination.exists() and any(destination.iterdir()) and not overwrite:
        raise FileExistsError(f"package destination is not empty: {destination}")
    refs = referenced_sample_ids(project)
    missing = [sample_id for sample_id in refs if sample_id not in media_paths]
    if missing:
        raise FileNotFoundError("missing media paths: " + ", ".join(missing))
    # An unplugged drive or missing take must not leave a half-built bundle or
    # replace media in a previously valid package before the error is found.
    sources = {sample_id: Path(media_paths[sample_id]) for sample_id in refs}
    for source in sources.values():
        if not source.is_file():
            raise FileNotFoundError(source)
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(
        prefix=f".{destination.name}-stage-", dir=destination.parent
    ) as temporary:
        staged = Path(temporary) / "package"
        if destination.exists():
            # Preserve unrelated links without traversing external trees or
            # cycles. Managed write paths below replace links before writing.
            shutil.copytree(destination, staged, symlinks=True)
        else:
            staged.mkdir()
        _write_package(project, staged, refs, sources)
        _publish_package(staged, destination)
    return destination


def _write_package(project, destination, refs, sources):
    media_dir = destination / "Media"
    if media_dir.is_symlink():
        media_dir.unlink()
    media_dir.mkdir(exist_ok=True)

    manifest: list[PackagedMedia] = []
    used_names: set[str] = set()
    for sample_id, uses in refs.items():
        source = sources[sample_id]
        stem = source.stem or sample_id
        suffix = source.suffix or ".wav"
        filename = f"{stem}{suffix}"
        n = 2
        while filename.casefold() in used_names:
            filename = f"{stem}-{n}{suffix}"
            n += 1
        used_names.add(filename.casefold())
        target = media_dir / filename
        if target.is_symlink():
            target.unlink()
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
    if project_path.is_symlink():
        project_path.unlink()
    project.save(project_path)
    manifest_path = destination / "media-manifest.json"
    if manifest_path.is_symlink():
        manifest_path.unlink()
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


def _publish_package(staged: Path, destination: Path) -> None:
    backup_root = None
    previous = None
    published = False
    try:
        if destination.exists():
            backup_root = Path(
                tempfile.mkdtemp(prefix=f".{destination.name}-backup-", dir=destination.parent)
            )
            previous = backup_root / "previous"
            os.replace(destination, previous)
        try:
            os.replace(staged, destination)
            published = True
        except BaseException as exc:
            if previous is not None:
                try:
                    os.replace(previous, destination)
                except OSError as rollback:
                    # Never let cleanup destroy the only remaining good copy.
                    retained = previous
                    backup_root = None
                    raise RuntimeError(
                        f"Package publication failed and restoration failed; "
                        f"previous package retained at {retained}: {rollback}"
                    ) from exc
            raise
    finally:
        if backup_root is not None and (published or not previous.exists()):
            # Cleanup failure after publication must not report an unsuccessful
            # save or remove the newly published package. Retain the backup.
            try:
                shutil.rmtree(backup_root)
            except OSError as exc:
                logging.getLogger(__name__).warning(
                    "Package backup retained at %s: %s", backup_root, exc
                )
