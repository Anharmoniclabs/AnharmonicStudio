#!/usr/bin/env python
"""Build optional native DSP before the workstation opens its audio stream."""

import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from mpclab.native_dsp import FLAGS, SOURCE, NativeDSP, binary_path


def build():
    destination = binary_path()
    if destination.exists():
        NativeDSP(destination)
        return destination
    compiler = shutil.which("cc")
    if compiler is None:
        raise RuntimeError("A C compiler is required for native audio DSP (install base-devel)")
    destination.parent.mkdir(exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix="dsp-", suffix=".so", dir=destination.parent)
    os.close(fd)
    try:
        subprocess.run(
            [compiler, *FLAGS, str(SOURCE), "-lm", "-o", temporary],
            check=True,
            capture_output=True,
            text=True,
            timeout=60,
        )
        NativeDSP(temporary)
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
