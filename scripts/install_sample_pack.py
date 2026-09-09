#!/usr/bin/env python
"""Extract a local ZIP sample pack and register it with AnharmonicStudio.

This helper never executes archive contents, never overwrites existing files,
and rejects members that would escape the destination directory.
"""

from __future__ import annotations

import argparse
from pathlib import Path, PurePosixPath
import shutil
import sys
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mpclab.library import AUDIO_EXT, Library  # noqa: E402

DEFAULT_PACK_ROOT = Path.home() / "Music" / "DrumPacks"


def safe_members(archive: zipfile.ZipFile) -> list[zipfile.ZipInfo]:
    members: list[zipfile.ZipInfo] = []
    for info in archive.infolist():
        raw = info.filename.replace("\\", "/")
        path = PurePosixPath(raw)
        if (
            not raw
            or raw.startswith("/")
            or path.is_absolute()
            or ".." in path.parts
            or any(part in ("", ".") for part in path.parts)
        ):
            raise ValueError(f"unsafe ZIP member: {info.filename!r}")
        # Unix symlinks are not useful sample assets and can redirect extraction.
        mode = (info.external_attr >> 16) & 0o170000
        if mode == 0o120000:
            raise ValueError(f"symlink ZIP member is not allowed: {info.filename!r}")
        members.append(info)
    return members


def pack_folder_name(zip_path: Path) -> str:
    name = zip_path.stem.strip()
    return name or "SamplePack"


def extract_pack(zip_path: Path, destination: Path, *, dry_run: bool = False) -> tuple[int, int]:
    destination = destination.expanduser().resolve()
    files = 0
    audio = 0
    with zipfile.ZipFile(zip_path) as archive:
        members = safe_members(archive)
        for info in members:
            relative = PurePosixPath(info.filename.replace("\\", "/"))
            target = destination.joinpath(*relative.parts)
            if info.is_dir():
                if not dry_run:
                    target.mkdir(parents=True, exist_ok=True)
                continue
            files += 1
            if target.suffix.lower() in AUDIO_EXT:
                audio += 1
            if dry_run or target.exists():
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(info) as source, target.open("xb") as output:
                shutil.copyfileobj(source, output, length=1024 * 1024)
    return files, audio


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("archive", type=Path, help="ZIP sample pack to extract")
    p.add_argument(
        "--pack-root",
        type=Path,
        default=DEFAULT_PACK_ROOT,
        help="parent directory for extracted packs (default: ~/Music/DrumPacks)",
    )
    p.add_argument("--folder", help="destination folder name; defaults to ZIP filename")
    p.add_argument("--name", help="display name in AnharmonicStudio")
    p.add_argument(
        "--repo",
        type=Path,
        default=Path(__file__).resolve().parent.parent,
        help="AnharmonicStudio checkout containing library/",
    )
    p.add_argument("--dry-run", action="store_true", help="validate and report without writing")
    return p


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    archive = args.archive.expanduser().resolve()
    if not archive.is_file():
        raise SystemExit(f"archive not found: {archive}")
    if archive.suffix.lower() != ".zip":
        raise SystemExit("only .zip sample packs are supported by this installer")

    repo = args.repo.expanduser().resolve()
    if not (repo / "mpclab" / "library.py").is_file():
        raise SystemExit(f"not an AnharmonicStudio checkout: {repo}")

    folder = (args.folder or pack_folder_name(archive)).strip()
    if not folder or folder in {".", ".."} or "/" in folder or "\\" in folder:
        raise SystemExit("--folder must be one plain directory name")
    destination = (args.pack_root.expanduser().resolve() / folder).resolve()
    pack_root = args.pack_root.expanduser().resolve()
    if destination.parent != pack_root:
        raise SystemExit("destination must stay directly inside --pack-root")

    print(f"Archive:     {archive}")
    print(f"Destination: {destination}")
    print(f"Mode:        {'DRY RUN' if args.dry_run else 'extract + register'}")

    try:
        files, audio = extract_pack(archive, destination, dry_run=args.dry_run)
    except (OSError, ValueError, zipfile.BadZipFile) as exc:
        raise SystemExit(f"could not extract sample pack: {exc}") from exc

    print(f"ZIP files:   {files}")
    print(f"Audio files: {audio}")
    if audio == 0:
        raise SystemExit("no supported audio files were found in this ZIP")
    if args.dry_run:
        return 0

    library = Library(repo / "library")
    display_name = (args.name or folder).strip() or folder
    indexed = library.register_pack(destination, name=display_name)
    library.scan()
    total = sum(
        clip.kind == "pack" and clip.pack == display_name for clip in library.clips.values()
    )
    print(f"Newly indexed: {indexed}")
    print(f"Pack sounds:   {total}")
    print("Done. Open AnharmonicStudio and choose Browser → SAMPLE PACKS.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
