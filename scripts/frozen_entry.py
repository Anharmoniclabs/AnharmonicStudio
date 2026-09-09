"""PyInstaller entry point; workers are dispatched before GUI startup."""

from mpclab.__main__ import main

if __name__ == "__main__":
    raise SystemExit(main())
