"""Compatibility and lifecycle contracts across shared engine modules."""

from pathlib import Path
import subprocess
import sys

import numpy as np
import pytest

from mpclab import engine_offline
from mpclab.engine import Engine, PadRenderWorkspace, PadVoice
from mpclab import sample_voice


def test_existing_voice_imports_keep_class_identity():
    # Extensions patching the existing class must affect the extracted renderer.
    assert PadVoice is sample_voice.PadVoice
    assert PadRenderWorkspace is sample_voice.PadRenderWorkspace


def test_shared_modules_import_without_coordinator_devices_or_gui():
    code = """
import importlib.abc
import sys

class RejectRuntimeImports(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname in ('mpclab.engine', 'mpclab.plugin_host', 'sounddevice', 'PySide6'):
            raise AssertionError('shared module imported runtime: ' + fullname)

sys.meta_path.insert(0, RejectRuntimeImports())
from mpclab import sample_voice, engine_scheduling, engine_mixing, engine_offline
"""
    subprocess.run(
        [sys.executable, "-c", code],
        cwd=Path(__file__).resolve().parents[1],
        check=True,
        capture_output=True,
        text=True,
        timeout=15,
    )


@pytest.mark.parametrize("fail", [False, True])
def test_export_cancellation_and_failure_close_only_export_plugins(monkeypatch, fail):
    class Library:
        def audio(self, sample_id):
            return np.zeros((32, 2), dtype=np.float32)

    opened = []

    class Plugins:
        instrument = None

        def __init__(self, *args):
            self.closed = False
            opened.append(self)

        def render_instrument(self, *args):
            pass

        def render_effect(self, block):
            if fail:
                raise RuntimeError("export effect failed")

        def close(self):
            self.closed = True

    monkeypatch.setattr(engine_offline, "OfflinePlugins", Plugins)
    engine = Engine(Library(), blocksize=256)
    engine.mode = "song"
    engine.project.plugins = {"effect": {}}
    live_host = object()
    engine.external.effect = live_host
    stream = engine.iter_offline_blocks(mode="pattern", tail=0)
    # Creating an iterator must not load plugins or change live transport state.
    assert engine.mode == "song" and not opened
    if fail:
        with pytest.raises(RuntimeError, match="export effect failed"):
            next(stream)
    else:
        assert next(stream).shape == (256, 2)
        assert engine.mode == "pattern"
        assert not opened[0].closed
        stream.close()
    assert engine.mode == "song"
    assert len(opened) == 1 and opened[0].closed
    assert engine.external.effect is live_host
