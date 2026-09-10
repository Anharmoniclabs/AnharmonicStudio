#!/usr/bin/env python
"""Build a deterministic source ZIP without user libraries, projects or binaries."""

from __future__ import annotations

import argparse
from pathlib import Path
import subprocess
import tempfile
import zipfile
import os
import hashlib

SOURCE_DIRS = {
    "mpclab",
    "native",
    "assets",
    "scripts",
    "tests",
    "automation",
    "docs",
    ".github",
    "packaging",
    "website",
    "delivery",
}
SOURCE_FILES = {
    "README.md",
    "CONTRIBUTING.md",
    "BUILDING.md",
    "SUPPORT.md",
    "DISTRIBUTION.md",
    "LICENSE",
    "THIRD_PARTY.md",
    "pyproject.toml",
    "uv.lock",
    "run.sh",
    "install-separation.sh",
    ".python-version",
    ".gitignore",
    "requirements-build.txt",
    "requirements-browser.txt",
}
EXCLUDED_PARTS = {"build", "__pycache__", ".venv", ".deps", "evidence", "previews", "benchmarks"}
BINARY_SUFFIXES = {".so", ".dll", ".dylib", ".pyc", ".o", ".a", ".exe"}


def build_bundle(root: Path, destination: Path, *, committed_only: bool = False) -> str:
    root = root.resolve()
    # Release sources come from Git blobs, not checkout line endings or host
    # executable bits. Windows/macOS/Linux must deliver identical source bytes.
    entries = subprocess.check_output(
        ["git", "ls-tree", "-rz", "HEAD"]
        if committed_only
        else ["git", "ls-files", "--stage", "-z"],
        cwd=root,
    )
    tracked = {}
    for entry in entries.decode().split("\0"):
        if entry:
            metadata, name = entry.split("\t", 1)
            fields = metadata.split()
            tracked[name] = (fields[0], fields[2] if committed_only else fields[1])
    names = set(tracked)
    if not committed_only:
        names.update(
            subprocess.check_output(
                ["git", "ls-files", "--others", "--exclude-standard", "-z"], cwd=root
            )
            .decode()
            .split("\0")
        )
    destination.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(suffix=".zip", dir=destination.parent)
    os.close(fd)
    try:
        # Stored entries avoid cross-platform zlib-version differences too.
        with zipfile.ZipFile(temporary, "w", compression=zipfile.ZIP_STORED) as bundle:
            for name in sorted(names - {""}):
                path = Path(name)
                if name not in SOURCE_FILES and path.parts[0] not in SOURCE_DIRS:
                    continue
                if EXCLUDED_PARTS.intersection(path.parts) or path.suffix in BINARY_SUFFIXES:
                    continue
                source = root / path
                if source.resolve() == destination.resolve():
                    continue
                if not committed_only and not source.exists():
                    continue
                git_mode, blob = tracked.get(name, ("100644", ""))
                if (
                    git_mode not in {"100644", "100755"}
                    or source.is_symlink()
                    or not source.resolve().is_relative_to(root)
                ):
                    raise ValueError(f"Source bundle cannot follow symlinks: {name}")
                entry = zipfile.ZipInfo(
                    f"AnharmonicStudio/{path.as_posix()}", (2026, 1, 1, 0, 0, 0)
                )
                entry.compress_type = zipfile.ZIP_STORED
                entry.create_system = 3
                mode = 0o755 if git_mode == "100755" else 0o644
                entry.external_attr = (0o100000 | mode) << 16
                content = (
                    subprocess.check_output(["git", "cat-file", "blob", blob], cwd=root)
                    if committed_only
                    else source.read_bytes()
                )
                bundle.writestr(entry, content)
        with open(temporary, "r+b") as handle:
            digest = hashlib.file_digest(handle, "sha256").hexdigest()
            os.fsync(handle.fileno())
        os.replace(temporary, destination)
        return digest
    finally:
        Path(temporary).unlink(missing_ok=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    digest = build_bundle(Path(__file__).resolve().parent.parent, args.output)
    print(f"{digest}  {args.output}")


if __name__ == "__main__":
    main()
