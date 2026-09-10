#!/usr/bin/env python
"""Build optional native DSP before the workstation opens its audio stream."""

import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from mpclab.native_dsp import FLAGS, SOURCE, SOURCES, NativeDSP, binary_path


def build():
    destination = binary_path()
    if destination.exists():
        NativeDSP(destination)
        return destination
    compiler = shutil.which("clang++" if sys.platform == "win32" else "c++")
    if compiler is None:
        raise RuntimeError("A C++17 compiler is required for the portable audio engine")
    destination.parent.mkdir(exist_ok=True)
    fd, temporary = tempfile.mkstemp(
        prefix="dsp-", suffix=destination.suffix, dir=destination.parent
    )
    os.close(fd)
    try:
        subprocess.run(
            [
                compiler,
                *FLAGS,
                *(str(source) for source in SOURCES),
                *([] if sys.platform == "win32" else ["-lm"]),
                "-o",
                temporary,
            ],
            check=True,
            capture_output=True,
            text=True,
            timeout=60,
        )
        # A loaded DLL cannot be renamed on Windows. Validate in a child that
        # exits before publishing the library, keeping a failed build atomic.
        subprocess.run(
            [
                sys.executable,
                "-c",
                "from mpclab.native_dsp import NativeDSP; import sys; NativeDSP(sys.argv[1])",
                temporary,
            ],
            cwd=SOURCE.parents[2],
            check=True,
            timeout=30,
        )
        os.replace(temporary, destination)
    finally:
        Path(temporary).unlink(missing_ok=True)
    return destination


if __name__ == "__main__":
    try:
        print(f"Native audio DSP ready: {build()}")
    except (OSError, RuntimeError, subprocess.SubprocessError) as exc:
        print(f"Native audio DSP unavailable: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
