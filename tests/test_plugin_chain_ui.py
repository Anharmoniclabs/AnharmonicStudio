from __future__ import annotations

import os
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np
from PySide6.QtCore import QCoreApplication

from mpclab.plugin_chain_runtime import install_plugin_chain_runtime
from mpclab.plugin_chain_ui import attach_plugin_chain_ui
from mpclab.premium_workflows import attach_premium_workflows, install_premium_runtime
from mpclab.pro_daw_state import install_pro_daw_state
from mpclab.routing_ui import attach_routing_ui
from mpclab.ui.main_window import MainWindow


def until(predicate, timeout=3.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        QCoreApplication.processEvents()
        if predicate():
            return True
        time.sleep(0.01)
    return predicate()


def make_window(tmp_path):
    install_premium_runtime()
    install_pro_daw_state()
    install_plugin_chain_runtime()
    window = MainWindow(tmp_path, restore_session=False)
    commands = attach_premium_workflows(window)
    attach_routing_ui(window, commands)
    controller = attach_plugin_chain_ui(window, commands)
    return window, commands, controller


def _spec(*, bypass=False):
    return {
        "path": "/plugins/Test.vst3",
        "plugin_name": "Test",
        "parameters": {},
        "state": "",
        "bypass": bypass,
    }


def test_insert_chain_commands_and_all_target_kinds_are_attached(tmp_path):
    window, commands, controller = make_window(tmp_path)
    try:
        window.project.workflow = {
            "routing": {
                "buses": [
                    {
                        "id": "parallel",
                        "name": "Parallel",
                        "gain": 1.0,
                        "pan": 0.0,
                        "mute": False,
                        "output": "master",
                    }
                ]
            }
        }
        ids = {target for _label, target in controller.target_choices()}
        assert "plugins.insert_chains" in commands.registry.commands
        assert "plugins.reload_chains" in commands.registry.commands
        assert "master" in ids
        assert "bus:parallel" in ids
        assert f"track:{window.project.tracks[0].id}" in ids
    finally:
        window.close()


def test_bypassed_chain_persists_without_starting_third_party_code(tmp_path):
    window, _commands, controller = make_window(tmp_path)
    try:
        target = f"track:{window.project.tracks[0].id}"
        controller.commit_chain(target, [_spec(bypass=True)])
        assert window.project.pro_daw["plugin_chains"][target][0]["bypass"]
        assert target not in window.engine.plugin_chains.bridges
        assert window.project.to_dict()["pro_daw"] == window.project.pro_daw
    finally:
        window.close()


def test_async_chain_swap_refreshes_latency_without_blocking_gui(tmp_path, monkeypatch):
    window, _commands, controller = make_window(tmp_path)

    class Bridge:
        blocksize = 64
        error = ""
        info = {
            "name": "Fixture",
            "latency_samples": 9,
            "chain": [{"name": "Fixture", "latency_samples": 9, "parameters": {}}],
        }

        def __init__(self):
            self.closed = False

        def render(self, audio, frames):
            return np.asarray(audio, dtype=np.float32)

        def close(self):
            self.closed = True

    monkeypatch.setattr("mpclab.plugin_chain_ui.build_live_chain", lambda *_args: Bridge())
    try:
        window.engine.blocksize = 64
        target = f"track:{window.project.tracks[0].id}"
        controller.commit_chain(target, [_spec()])
        assert until(lambda: target in window.engine.plugin_chains.bridges)
        # 9 intrinsic samples + the shared two-block live bridge.
        assert window.engine.plugin_chains.latencies()[target] == 137
        assert window.engine.plugin_chain_delays.plan.output_latency == 137
    finally:
        window.close()
