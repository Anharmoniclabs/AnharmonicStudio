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
}
EXCLUDED_PARTS = {"build", "__pycache__", ".venv", ".deps", "evidence", "previews", "benchmarks"}
BINARY_SUFFIXES = {".so", ".dll", ".dylib", ".pyc", ".o", ".a", ".exe"}


def build_bundle(root: Path, destination: Path) -> str:
    root = root.resolve()
    names = (
        subprocess.check_output(
            ["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"], cwd=root
        )
        .decode()
        .split("\0")
    )
    destination.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(suffix=".zip", dir=destination.parent)
    os.close(fd)
    try:
        with zipfile.ZipFile(temporary, "w", compression=zipfile.ZIP_DEFLATED) as bundle:
            for name in sorted(set(names) - {""}):
                path = Path(name)
                if name not in SOURCE_FILES and path.parts[0] not in SOURCE_DIRS:
                    continue
                if EXCLUDED_PARTS.intersection(path.parts) or path.suffix in BINARY_SUFFIXES:
                    continue
                source = root / path
                if source.resolve() == destination.resolve():
                    continue
                if not source.exists():
                    continue
                if source.is_symlink() or not source.resolve().is_relative_to(root):
                    raise ValueError(f"Source bundle cannot follow symlinks: {name}")
                entry = zipfile.ZipInfo(
                    f"AnharmonicStudio/{path.as_posix()}", (2026, 1, 1, 0, 0, 0)
                )
                entry.compress_type = zipfile.ZIP_DEFLATED
                entry.create_system = 3
                mode = 0o755 if source.stat().st_mode & 0o111 else 0o644
                entry.external_attr = (0o100000 | mode) << 16
                bundle.writestr(entry, source.read_bytes())
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
