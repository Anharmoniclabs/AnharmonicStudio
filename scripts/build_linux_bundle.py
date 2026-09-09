#!/usr/bin/env python
"""Build a local Linux folder bundle; never publish or install it automatically.

Requires PyInstaller 6.16.0 in the build environment. Build on the oldest Linux
distribution you intend to support: glibc and graphics drivers remain system
dependencies. FFmpeg is supplied by the target's package manager.
"""

import argparse
import hashlib
import importlib.metadata as metadata
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import tarfile
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from scripts.build_native import build


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "output", type=Path, help="new output directory; existing paths are rejected"
    )
    args = parser.parse_args()
    if not sys.platform.startswith("linux"):
        parser.error("Build on Linux for Linux")
    if metadata.version("pyinstaller") != "6.16.0":
        parser.error("Install the pinned build tool: uv pip install pyinstaller==6.16.0")
    output = args.output.resolve()
    if output.exists():
        parser.error(f"Output already exists: {output}")
    root = Path(__file__).resolve().parent.parent
    native = build()
    output.parent.mkdir(parents=True, exist_ok=True)
    packages = (
        "numpy",
        "PySide6",
        "PySide6_Addons",
        "PySide6_Essentials",
        "shiboken6",
        "sounddevice",
        "soundfile",
        "cffi",
        "pycparser",
    )
    with tempfile.TemporaryDirectory(prefix="linux-build-", dir=output.parent) as temporary:
        stage = Path(temporary)
        command = [
            sys.executable,
            "-m",
            "PyInstaller",
            "--noconfirm",
            "--clean",
            "--onedir",
            "--name",
            "AnharmonicStudio",
            "--distpath",
            str(stage / "dist"),
            "--workpath",
            str(stage / "work"),
            "--specpath",
            str(stage),
            "--paths",
            str(root),
            "--add-data",
            f"{root / 'assets'}:assets",
            "--add-data",
            f"{root / 'mpclab/native/dsp.c'}:mpclab/native",
            "--add-binary",
            f"{native}:.native",
        ]
        for name in (
            "torch",
            "torchaudio",
            "demucs",
            "scipy",
            "matplotlib",
            "tkinter",
            "pytest",
            "IPython",
        ):
            command += ["--exclude-module", name]
        for package in packages:
            command += ["--copy-metadata", package]
        command.append(str(root / "scripts/frozen_entry.py"))
        subprocess.run(command, cwd=root, check=True)
        bundle = stage / "dist/AnharmonicStudio"
        for name in ("LICENSE", "THIRD_PARTY.md"):
            shutil.copy2(root / name, bundle / name)
        manifest = {
            "platform": platform.platform(),
            "machine": platform.machine(),
            "libc": platform.libc_ver(),
            "python": platform.python_version(),
            "pyinstaller": metadata.version("pyinstaller"),
            "packages": {name: metadata.version(name) for name in packages},
            "system_dependencies": [
                "ffmpeg (including ffprobe)",
                "Linux graphics/audio drivers and glibc",
            ],
            "optional_stem_separation": False,
        }
        (bundle / "build-info.json").write_text(json.dumps(manifest, indent=2) + "\n")
        (bundle / "README.txt").write_text(
            "Anharmonic Studio — local Linux candidate\n\n"
            "Run ./AnharmonicStudio to open the workstation. Python, uv and a C compiler\n"
            "are bundled or unnecessary. Install ffmpeg with your system package manager.\n"
            "Requires compatible Linux/glibc and system graphics/audio drivers; see build-info.json.\n"
            "Songs live in $XDG_DATA_HOME/anharmonic-studio (default ~/.local/share/anharmonic-studio).\n"
            "Use --data-dir /path/to/existing/checkout to open an existing source workspace.\n"
            "Do not move or delete your old workspace: no automatic migration is performed.\n"
            "Run ./AnharmonicStudio --self-check for a disposable offscreen check (no devices).\n"
            "Run ./AnharmonicStudio --install to copy the app into ~/.local/opt and add a menu entry.\n"
            "Installation does not launch the app or overwrite older app folders or songs.\n"
            "Optional AI stem separation requires the separate source installation.\n"
            "This unsigned candidate has not been certified on other Linux distributions.\n"
            "Keep the entire folder together. Preserve LICENSE, THIRD_PARTY.md and dependency notices.\n"
        )
        env = dict(os.environ, QT_QPA_PLATFORM="offscreen", OPENBLAS_NUM_THREADS="1")
        checked = subprocess.run(
            [str(bundle / "AnharmonicStudio"), "--self-check"],
            cwd=stage,
            env=env,
            capture_output=True,
            text=True,
            timeout=180,
        )
        (bundle / "self-check.log").write_text(checked.stdout + checked.stderr)
        if checked.returncode:
            raise RuntimeError("Bundled self-check failed:\n" + checked.stdout + checked.stderr)
        # Atomically expose a candidate only after the real frozen export/UI check passes.
        os.replace(stage / "dist", output)
    archive = output / "AnharmonicStudio-linux.tar.gz"
    with tarfile.open(archive, "w:gz") as stream:
        stream.add(output / "AnharmonicStudio", arcname="AnharmonicStudio")
    with archive.open("rb") as stream:
        digest = hashlib.file_digest(stream, "sha256").hexdigest()
    archive.with_suffix(archive.suffix + ".sha256").write_text(f"{digest}  {archive.name}\n")
    print(f"Validated local Linux bundle: {archive}\nSHA256: {digest}")


if __name__ == "__main__":
    main()
