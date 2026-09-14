#!/usr/bin/env python
"""Build the pinned native pitch engine on Linux, macOS or Windows."""

from pathlib import Path
import os
import shutil
import subprocess
import sys


def build():
    root = Path(__file__).resolve().parent.parent
    output = root / ".native"
    build_dir = output / "rubberband-build"
    subprocess.run(
        [
            "cmake",
            "-S",
            str(root / "native"),
            "-B",
            str(build_dir),
            "-DCMAKE_BUILD_TYPE=Release",
            "-DBUILD_TESTING=OFF",
        ],
        check=True,
    )
    subprocess.run(
        [
            "cmake",
            "--build",
            str(build_dir),
            "--config",
            "Release",
            "--target",
            "rubberband",
            "-j2",
        ],
        check=True,
    )
    name = (
        "rubberband.dll"
        if sys.platform == "win32"
        else "librubberband.dylib"
        if sys.platform == "darwin"
        else "librubberband.so.3"
    )
    source = next(build_dir.rglob(name))
    destination = output / name
    staged = destination.with_suffix(destination.suffix + ".tmp")
    shutil.copyfile(source, staged)
    os.replace(staged, destination)
    return destination


if __name__ == "__main__":
    print(build())
