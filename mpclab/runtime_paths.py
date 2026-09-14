"""Separate bundled resources from writable songs and external program libraries."""

import os
from pathlib import Path
import sys
import shutil
import subprocess

from . import APP_SLUG

RESOURCE_ROOT = Path(__file__).resolve().parent.parent


def data_root(override=None):
    if override is not None:
        return Path(override).expanduser().resolve()
    if getattr(sys, "frozen", False):
        if sys.platform == "win32":
            return Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData/Local") / APP_SLUG
        if sys.platform == "darwin":
            return Path.home() / "Library/Application Support" / APP_SLUG
        base = Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local/share")
        if not base.is_absolute():
            base = Path.home() / ".local/share"
        return base / APP_SLUG
    return RESOURCE_ROOT


def export_command(specification):
    if getattr(sys, "frozen", False):
        if sys.platform == "win32":
            return [
                str(Path(sys.executable).with_name("AnharmonicStudio-worker.exe")),
                "--export-worker",
                str(specification),
            ]
        return [sys.executable, "--export-worker", str(specification)]
    return [sys.executable, "-m", "mpclab.export_worker", str(specification)]


def external_environment(program=None):
    """System FFmpeg/wpctl must use system libraries, not bundled Qt libraries."""
    env = os.environ.copy()
    if program is not None and Path(program).is_relative_to(RESOURCE_ROOT / "tools"):
        # Bundled FFmpeg needs its bundled dependencies. Only external system
        # programs should lose PyInstaller's library search path.
        return env
    if getattr(sys, "frozen", False) and sys.platform.startswith("linux"):
        original = env.pop("LD_LIBRARY_PATH_ORIG", None)
        env.pop("LD_LIBRARY_PATH", None)
        if original is not None:
            env["LD_LIBRARY_PATH"] = original
    return env


def media_tool(name):
    """Prefer the exact FFmpeg binaries included with an official build."""
    executable = name + (".exe" if sys.platform == "win32" else "")
    bundled = RESOURCE_ROOT / "tools" / executable
    return str(bundled) if bundled.is_file() else shutil.which(name)


def subprocess_options():
    """Headless helper processes must not create desktop console windows."""
    return {"creationflags": subprocess.CREATE_NO_WINDOW} if sys.platform == "win32" else {}
