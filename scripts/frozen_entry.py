"""PyInstaller entry point; workers are dispatched before GUI startup."""

import os
import sys

# The Windows GUI bootloader has no standard streams. The separate console
# worker retains its pipes for export progress and release self-check reports.
if sys.stdout is None:
    sys.stdout = open(os.devnull, "w")
if sys.stderr is None:
    sys.stderr = open(os.devnull, "w")

from mpclab.__main__ import main

if __name__ == "__main__":
    raise SystemExit(main())
