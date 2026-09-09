"""Separate bundled resources from writable songs and external program libraries."""

import os
from pathlib import Path
import sys

from . import APP_SLUG

RESOURCE_ROOT = Path(__file__).resolve().parent.parent


def data_root(override=None):
    if override is not None:
        return Path(override).expanduser().resolve()
    if getattr(sys, "frozen", False):
        base = Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local/share")
        if not base.is_absolute():
            base = Path.home() / ".local/share"
        return base / APP_SLUG
    return RESOURCE_ROOT


def export_command(specification):
    if getattr(sys, "frozen", False):
        return [sys.executable, "--export-worker", str(specification)]
    return [sys.executable, "-m", "mpclab.export_worker", str(specification)]


def external_environment():
    """System FFmpeg/wpctl must use system libraries, not bundled Qt libraries."""
    env = os.environ.copy()
    if getattr(sys, "frozen", False) and sys.platform.startswith("linux"):
        original = env.pop("LD_LIBRARY_PATH_ORIG", None)
        env.pop("LD_LIBRARY_PATH", None)
        if original is not None:
            env["LD_LIBRARY_PATH"] = original
    return env
