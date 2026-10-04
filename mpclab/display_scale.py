"""Choose whole-application density before Qt creates its first GUI application.

Qt scales layout, painting and pointer coordinates together. Changing fonts or
individual fixed sizes cannot provide the same consistent workspace zoom.
"""

import json
import math
import os
import subprocess
import sys
import tempfile
from pathlib import Path

from . import APP_SLUG, ORGANIZATION_NAME
from .runtime_paths import RESOURCE_ROOT, subprocess_options


def recommended_scale(width: int, height: int) -> float:
    """Fit a useful editing canvas into the OS's already DPI-adjusted work area."""
    ratio = min(width / 1600, height / 900, 1.0)
    return max(0.8, math.floor(ratio * 20 + 1e-6) / 20)


def scale_preference(value) -> str:
    if str(value).lower() == "auto":
        return "auto"
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise ValueError("Use auto or a scale between 0.75 and 1.5") from None
    if not math.isfinite(number) or not 0.75 <= number <= 1.5:
        raise ValueError("Use auto or a scale between 0.75 and 1.5")
    return f"{number:g}"


def saved_scale() -> str:
    from PySide6.QtCore import QSettings

    settings = QSettings(QSettings.IniFormat, QSettings.UserScope, ORGANIZATION_NAME, APP_SLUG)
    try:
        return scale_preference(settings.value("ui/interface_scale", "auto"))
    except ValueError:
        return "auto"


def probe_display(destination: Path, qt_args=()) -> int:
    """A short-lived GUI process measures the display without opening a window."""
    from PySide6.QtGui import QGuiApplication

    app = QGuiApplication([sys.argv[0], *qt_args])
    screen = app.primaryScreen()
    if screen is None:
        return 1
    work = screen.availableGeometry()
    # A frozen Windows GUI executable has no stdout. A private result file
    # works for both desktop bundles and source launches.
    destination.write_text(json.dumps({"width": work.width(), "height": work.height()}))
    return 0


def configure_scale(preference=None, qt_args=()) -> float:
    """Set this process's Qt scale before QApplication, preserving OS DPI."""
    choice = saved_scale() if preference is None else scale_preference(preference)
    # Respect a caller's explicit Qt scale unless Studio's own scale was chosen.
    inherited = os.environ.get("QT_SCALE_FACTOR")
    if choice == "auto" and inherited:
        try:
            factor = float(inherited)
            if math.isfinite(factor) and factor > 0 and factor != 1:
                return factor
        except ValueError:
            pass
    factor = 1.0
    if choice != "auto":
        factor = float(choice)
    elif os.environ.get("QT_QPA_PLATFORM", "").split(":")[0] not in ("offscreen", "minimal"):
        command = [sys.executable]
        if not getattr(sys, "frozen", False):
            command += ["-m", "mpclab"]
        # A neutral probe reports logical screen dimensions after the desktop's
        # own scaling. Never multiply them by devicePixelRatio a second time.
        env = os.environ.copy()
        env["QT_SCALE_FACTOR"] = "1"
        try:
            with tempfile.TemporaryDirectory(prefix="anharmonic-display-") as directory:
                result = Path(directory) / "screen.json"
                subprocess.run(
                    [*command, "--display-probe", str(result), *qt_args],
                    cwd=RESOURCE_ROOT,
                    env=env,
                    capture_output=True,
                    text=True,
                    timeout=8,
                    check=True,
                    **subprocess_options(),
                )
                work = json.loads(result.read_text())
            factor = recommended_scale(int(work["width"]), int(work["height"]))
        except (OSError, subprocess.SubprocessError, ValueError, KeyError, TypeError):
            pass  # A failed probe must not prevent opening a project.
    os.environ["QT_SCALE_FACTOR"] = f"{factor:g}"
    return factor


def show_workspace(window, *, windowed=False):
    """Production startup uses the complete desktop work area."""
    if windowed:
        window.show()
    else:
        window.showMaximized()
