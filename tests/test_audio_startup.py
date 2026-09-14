"""The editor remains usable when Linux audio output is unavailable."""

from __future__ import annotations

from unittest.mock import patch

from mpclab.engine import Engine
from mpclab.ui.main_window import MainWindow


def test_audio_device_failure_opens_editor_in_offline_mode(tmp_path):
    with patch.object(Engine, "start", side_effect=RuntimeError("device busy")):
        window = MainWindow(tmp_path)
    try:
        assert window.engine.stream is None
        assert window._audio_start_error == "device busy"
        window._tick()
        assert window.cpu_label.text() == "audio offline"
        assert "offline export" in window.cpu_label.toolTip()
    finally:
        window.close()
