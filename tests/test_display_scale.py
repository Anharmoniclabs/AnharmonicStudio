"""Whole-interface scale is chosen before Qt, independently of display DPI."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from PySide6.QtWidgets import QApplication, QWidget

from mpclab import display_scale


@pytest.mark.parametrize(
    "width,height,expected",
    [
        (1318, 744, 0.8),
        (1366, 768, 0.85),
        (1440, 900, 0.9),
        (1920, 1080, 1),
        (3840, 2160, 1),
        (1024, 600, 0.8),
    ],
)
def test_auto_density_uses_available_logical_screen(width, height, expected):
    assert display_scale.recommended_scale(width, height) == expected


def test_auto_probe_applies_scale_without_compounding_desktop_dpi(monkeypatch):
    monkeypatch.delenv("QT_SCALE_FACTOR", raising=False)
    monkeypatch.setenv("QT_QPA_PLATFORM", "wayland;xcb")
    calls = []

    def probe(command, **kwargs):
        calls.append((command, kwargs))
        Path(command[command.index("--display-probe") + 1]).write_text(
            json.dumps({"width": 1318, "height": 744})
        )

    monkeypatch.setattr(display_scale.subprocess, "run", probe)
    assert display_scale.configure_scale("auto") == 0.8
    assert os.environ["QT_SCALE_FACTOR"] == "0.8"
    assert calls[0][1]["env"]["QT_SCALE_FACTOR"] == "1"
    assert "--display-probe" in calls[0][0]


def test_explicit_scale_does_not_probe_and_honors_accessibility_override(monkeypatch):
    monkeypatch.setenv("QT_SCALE_FACTOR", "1.25")
    monkeypatch.setattr(display_scale.subprocess, "run", lambda *a, **kw: pytest.fail("probe"))
    assert display_scale.configure_scale("auto") == 1.25
    assert display_scale.configure_scale("0.8") == 0.8
    assert os.environ["QT_SCALE_FACTOR"] == "0.8"


def test_failed_probe_still_opens_at_normal_scale(monkeypatch):
    monkeypatch.delenv("QT_SCALE_FACTOR", raising=False)
    monkeypatch.setenv("QT_QPA_PLATFORM", "wayland;xcb")

    def failed(*args, **kwargs):
        raise subprocess.TimeoutExpired("probe", 8)

    monkeypatch.setattr(display_scale.subprocess, "run", failed)
    assert display_scale.configure_scale("auto") == 1


@pytest.mark.parametrize("value", ["nan", "inf", "0", "-1", "5", "large"])
def test_invalid_scale_is_rejected(value):
    with pytest.raises(ValueError):
        display_scale.scale_preference(value)


def test_startup_maximizes_and_windowed_option_remains_available():
    window = QWidget()
    display_scale.show_workspace(window)
    QApplication.processEvents()
    assert window.isMaximized()
    window.close()
    window = QWidget()
    display_scale.show_workspace(window, windowed=True)
    QApplication.processEvents()
    assert not window.isMaximized()
    window.close()


def test_scale_changes_actual_render_size_and_preserves_widget_coordinates():
    code = """
import json
from mpclab.display_scale import configure_scale
configure_scale('0.8')
from PySide6.QtWidgets import QApplication, QPushButton
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
app = QApplication([])
button = QPushButton('Play')
button.resize(100, 40)
button.show()
app.processEvents()
hits = []
button.clicked.connect(lambda: hits.append(True))
QTest.mouseClick(button, Qt.LeftButton, pos=button.rect().center())
image = button.grab()
print(json.dumps({'width': image.width(), 'height': image.height(), 'hits': hits}))
"""
    env = os.environ.copy()
    env["QT_QPA_PLATFORM"] = "offscreen"
    result = subprocess.run(
        [sys.executable, "-c", code],
        env=env,
        capture_output=True,
        text=True,
        timeout=20,
        check=True,
    )
    assert json.loads(result.stdout) == {"width": 80, "height": 32, "hits": [True]}
