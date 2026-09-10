"""User-facing serial insert chains for tracks, routing buses and master."""

from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import threading
import weakref

from PySide6.QtCore import QObject, QTimer, Qt, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)
from shiboken6 import isValid

from .plugin_chain_runtime import build_live_chain, known_chain_targets
from .plugin_registry import compatibility
from .pro_daw_state import ensure_pro_daw, plugin_chains, validate_pro_daw
from .workflow_commands import CommandSpec


def attach_plugin_chain_ui(window, command_controller):
    existing = getattr(window, "plugin_chain_controller", None)
    if existing is not None:
        return existing
    controller = PluginChainController(window, command_controller)
    window.plugin_chain_controller = controller
    return controller


class PluginChainController(QObject):
    load_finished = Signal(int, str, object, object, str)

    def __init__(self, window, command_controller):
        super().__init__(window)
        self.app = window
        self.command_controller = command_controller
        self.registry = command_controller.registry
        self.devices = window.devices
        self.status = "Insert chains ready"
        self._generation = 0
        self._pending: dict[str, tuple[int, list[dict]]] = {}
        self._loading = False
        self._closed = False
        self._signature = None
        self.dialog = None
        self.load_finished.connect(self._loaded)
        self._register_commands()
        command_controller._reindex_bindings()
        self._build_menu()
        self.timer = QTimer(self)
        self.timer.setInterval(250)
        self.timer.timeout.connect(self._watch_project)
        self.timer.start()
        window.destroyed.connect(self.shutdown)
        QTimer.singleShot(0, self.sync_project)

    def _register_commands(self) -> None:
        specs = (
            CommandSpec(
                "plugins.insert_chains",
                "Manage insert plugin chains",
                self.show,
                category="Plugins",
                keywords=("insert", "vst3", "audio unit", "bus", "effect", "chain"),
            ),
            CommandSpec(
                "plugins.reload_chains",
                "Reload insert plugin chains",
                self.sync_project,
                category="Plugins",
                keywords=("reload", "rescan", "insert", "effect"),
            ),
        )
        for spec in specs:
            try:
                self.registry.register(spec)
            except ValueError as exc:
                if "duplicate command id" not in str(exc):
                    raise

    def _build_menu(self) -> None:
        menu = self.app.menuBar().addMenu("Plugins")
        for command_id in ("plugins.insert_chains", "plugins.reload_chains"):
            action = menu.addAction(self.registry.get(command_id).title)
            action.triggered.connect(
                lambda _checked=False, command_id=command_id: self.command_controller._execute(
                    command_id
                )
            )
        menu.addSeparator()
        menu.addAction("Devices & Plugins…", self.app.show_devices)

    def _state_signature(self) -> str:
        state = validate_pro_daw(getattr(self.app.project, "pro_daw", {}))
        payload = {
            "blocksize": int(self.app.engine.blocksize),
            "targets": sorted(known_chain_targets(self.app.project)),
            "state": state,
        }
        return json.dumps(payload, sort_keys=True, separators=(",", ":"))

    def _watch_project(self) -> None:
        if self._closed:
            return
        try:
            signature = self._state_signature()
        except (TypeError, ValueError):
            return
        if signature != self._signature:
            self.sync_project()

    def sync_project(self) -> None:
        if self._closed:
            return
        self._generation += 1
        generation = self._generation
        self._signature = self._state_signature()
        valid = known_chain_targets(self.app.project)
        desired = {
            target: [deepcopy(item) for item in items if not item.get("bypass", False)]
            for target, items in plugin_chains(self.app.project).items()
            if target in valid and any(not item.get("bypass", False) for item in items)
        }
        runtime = self.app.engine.plugin_chains
        for target in tuple(runtime.bridges):
            if target not in desired:
                runtime.remove(target)
        self._pending = {
            target: (generation, specifications) for target, specifications in desired.items()
        }
        self.app.engine.prepare_plugin_chain_latency()
        self.status = "Loading insert chains…" if self._pending else "Insert chains ready"
        self._continue_load()
        self._refresh_dialog()

    def _continue_load(self) -> None:
        if self._closed or self._loading or not self._pending:
            if not self._pending and not self._loading and not self._closed:
                self.status = "Insert chains ready"
                self._refresh_dialog()
            return
        target = next(iter(self._pending))
        generation, specifications = self._pending.pop(target)
        self._loading = True
        reference = weakref.ref(self)
        rate = int(self.app.engine.sr)
        frames = int(self.app.engine.blocksize)

        def work():
            bridge = None
            error = ""
            try:
                bridge = build_live_chain(specifications, rate, frames)
            except Exception as exc:
                error = str(exc) or type(exc).__name__
            controller = reference()
            try:
                if controller is not None and isValid(controller) and not controller._closed:
                    controller.load_finished.emit(
                        generation,
                        target,
                        bridge,
                        specifications,
                        error,
                    )
                    return
            except RuntimeError:
                pass
            if bridge is not None:
                bridge.close()

        threading.Thread(
            target=work,
            name=f"Insert chain loader · {target}",
            daemon=True,
        ).start()

    def _loaded(self, generation, target, bridge, specifications, error) -> None:
        self._loading = False
        if self._closed or generation != self._generation:
            if bridge is not None:
                bridge.close()
            self._continue_load()
            return
        current = [
            item
            for item in plugin_chains(self.app.project).get(target, [])
            if not item.get("bypass", False)
        ]
        if current != specifications:
            if bridge is not None:
                bridge.close()
            self._continue_load()
            return
        if error:
            self.status = f"{self.target_label(target)} unavailable · {error}"
        else:
            self.app.engine.plugin_chains.install(target, bridge)
            self.app.engine.prepare_plugin_chain_latency()
            self.status = f"{self.target_label(target)} chain ready"
        self._refresh_dialog()
        self._continue_load()

    def target_choices(self) -> list[tuple[str, str]]:
        choices = [
            (f"Track {index + 1} · {track.name}", f"track:{track.id}")
            for index, track in enumerate(self.app.project.tracks)
        ]
        workflow = getattr(self.app.project, "workflow", {})
        routing = workflow.get("routing", {}) if isinstance(workflow, dict) else {}
        buses = routing.get("buses", []) if isinstance(routing, dict) else []
        if isinstance(buses, list):
            choices.extend(
                (f"Bus · {bus.get('name', 'BUS')}", f"bus:{bus.get('id')}")
                for bus in buses
                if isinstance(bus, dict) and bus.get("id")
            )
        choices.append(("Master", "master"))
        return choices

    def target_label(self, target: str) -> str:
        return next(
            (label for label, candidate in self.target_choices() if candidate == target),
            target,
        )

    def chain(self, target: str) -> list[dict]:
        return deepcopy(plugin_chains(self.app.project).get(target, []))

    def commit_chain(self, target: str, items: list[dict]) -> None:
        state = deepcopy(ensure_pro_daw(self.app.project))
        chains = deepcopy(state.get("plugin_chains", {}))
        if items:
            chains[target] = items
        else:
            chains.pop(target, None)
        if chains:
            state["plugin_chains"] = chains
        else:
            state.pop("plugin_chains", None)
        checked = validate_pro_daw(state)
        # Validation happens before snapshot/mutation so invalid third-party
        # metadata can never leave the current project half-edited.
        self.app.snapshot()
        self.app.project.pro_daw = checked
        self.app._set_dirty(True)
        self.sync_project()

    def show(self) -> None:
        if self.dialog is None:
            self.dialog = PluginChainDialog(self, self.app)
            self.dialog.finished.connect(self._dialog_closed)
        self.dialog.refresh()
        self.dialog.show()
        self.dialog.raise_()

    def _dialog_closed(self, _result) -> None:
        self.dialog = None

    def _refresh_dialog(self) -> None:
        if self.dialog is not None and isValid(self.dialog):
            self.dialog.refresh()

    def shutdown(self, *_args) -> None:
        if self._closed:
            return
        self._closed = True
        self.timer.stop()
        self._generation += 1
        self._pending.clear()
        try:
            self.app.engine.plugin_chains.close()
        except RuntimeError:
            pass


class PluginChainDialog(QDialog):
    def __init__(self, controller: PluginChainController, parent):
        super().__init__(parent)
        self.controller = controller
        self.setAttribute(Qt.WA_DeleteOnClose, True)
        self.setWindowTitle("Insert plugin chains")
        self.resize(900, 660)
        layout = QVBoxLayout(self)
        hint = QLabel(
            "Up to eight serial VST3/Audio Unit effects can run on every mixer track, "
            "routing bus and the master. Each chain is isolated from the audio callback."
        )
        hint.setWordWrap(True)
        layout.addWidget(hint)

        target_row = QHBoxLayout()
        target_row.addWidget(QLabel("Insert target"))
        self.target = QComboBox()
        self.target.currentIndexChanged.connect(self._target_changed)
        target_row.addWidget(self.target, 1)
        reload_button = QPushButton("Reload")
        reload_button.clicked.connect(controller.sync_project)
        target_row.addWidget(reload_button)
        layout.addLayout(target_row)

        columns = QHBoxLayout()
        left = QVBoxLayout()
        left.addWidget(QLabel("Current serial chain"))
        self.chain_list = QListWidget()
        self.chain_list.setAccessibleName("Insert plugin chain")
        left.addWidget(self.chain_list, 1)
        chain_buttons = QHBoxLayout()
        for label, callback in (
            ("Up", self._up),
            ("Down", self._down),
            ("Bypass", self._bypass),
            ("Parameters", self._parameters),
            ("Remove", self._remove),
        ):
            button = QPushButton(label)
            button.clicked.connect(callback)
            chain_buttons.addWidget(button)
        left.addLayout(chain_buttons)
        columns.addLayout(left, 1)

        right = QVBoxLayout()
        right.addWidget(QLabel("Discovered effects"))
        self.plugins = QListWidget()
        self.plugins.setAccessibleName("Discovered effect plugins")
        right.addWidget(self.plugins, 1)
        add = QPushButton("Add selected effect")
        add.clicked.connect(self._add)
        right.addWidget(add)
        scan = QPushButton("Scan plugin folders")
        scan.clicked.connect(controller.devices.scan)
        right.addWidget(scan)
        columns.addLayout(right, 1)
        layout.addLayout(columns, 1)

        self.status = QLabel()
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        close = QDialogButtonBox(QDialogButtonBox.Close)
        close.rejected.connect(self.reject)
        layout.addWidget(close)

    def _selected_target(self) -> str:
        return str(self.target.currentData() or "master")

    def _selected_index(self) -> int:
        return self.chain_list.currentRow()

    def _target_changed(self, *_args) -> None:
        self._refresh_chain()

    def refresh(self) -> None:
        current = self._selected_target()
        choices = self.controller.target_choices()
        self.target.blockSignals(True)
        self.target.clear()
        for label, target in choices:
            self.target.addItem(label, target)
        index = next(
            (i for i, (_label, target) in enumerate(choices) if target == current),
            0,
        )
        self.target.setCurrentIndex(index)
        self.target.blockSignals(False)
        self._refresh_chain()
        selected = self.plugins.currentItem()
        selected_id = selected.data(Qt.UserRole) if selected is not None else None
        self.plugins.clear()
        for candidate in self.controller.devices.registry.candidates.values():
            status = compatibility(candidate)
            item = QListWidgetItem(f"{candidate.name} · {candidate.format.upper()} · {status}")
            item.setData(Qt.UserRole, candidate.id)
            self.plugins.addItem(item)
            if candidate.id == selected_id:
                self.plugins.setCurrentItem(item)
        if self.plugins.currentRow() < 0 and self.plugins.count():
            self.plugins.setCurrentRow(0)
        self.status.setText(self.controller.status)

    def _refresh_chain(self) -> None:
        selected = self._selected_index()
        self.chain_list.clear()
        for index, spec in enumerate(self.controller.chain(self._selected_target())):
            name = spec.get("plugin_name") or Path(spec["path"]).stem
            state = " · BYPASSED" if spec.get("bypass", False) else ""
            self.chain_list.addItem(f"{index + 1}. {name}{state}")
        if self.chain_list.count():
            self.chain_list.setCurrentRow(min(max(0, selected), self.chain_list.count() - 1))

    def _commit(self, items: list[dict], row: int | None = None) -> None:
        self.controller.commit_chain(self._selected_target(), items)
        self.refresh()
        if row is not None and self.chain_list.count():
            self.chain_list.setCurrentRow(min(max(0, row), self.chain_list.count() - 1))

    def _add(self) -> None:
        item = self.plugins.currentItem()
        if item is None:
            return
        candidate = self.controller.devices.registry.candidates[item.data(Qt.UserRole)]
        status = compatibility(candidate)
        if candidate.format not in {"vst3", "au"} or status != "Ready to load":
            self.controller.status = status
            self.status.setText(status)
            return
        chain = self.controller.chain(self._selected_target())
        if len(chain) >= 8:
            QMessageBox.information(self, "Insert chain", "This chain already has eight inserts.")
            return
        chain.append(
            {
                "path": candidate.path,
                "plugin_name": candidate.name,
                "parameters": {},
                "state": "",
                "bypass": False,
            }
        )
        self._commit(chain, len(chain) - 1)

    def _remove(self) -> None:
        index = self._selected_index()
        chain = self.controller.chain(self._selected_target())
        if 0 <= index < len(chain):
            chain.pop(index)
            self._commit(chain, max(0, index - 1))

    def _up(self) -> None:
        index = self._selected_index()
        chain = self.controller.chain(self._selected_target())
        if 0 < index < len(chain):
            chain[index - 1], chain[index] = chain[index], chain[index - 1]
            self._commit(chain, index - 1)

    def _down(self) -> None:
        index = self._selected_index()
        chain = self.controller.chain(self._selected_target())
        if 0 <= index < len(chain) - 1:
            chain[index + 1], chain[index] = chain[index], chain[index + 1]
            self._commit(chain, index + 1)

    def _bypass(self) -> None:
        index = self._selected_index()
        chain = self.controller.chain(self._selected_target())
        if 0 <= index < len(chain):
            chain[index]["bypass"] = not chain[index].get("bypass", False)
            self._commit(chain, index)

    def _parameters(self) -> None:
        index = self._selected_index()
        target = self._selected_target()
        chain = self.controller.chain(target)
        if not 0 <= index < len(chain) or chain[index].get("bypass", False):
            return
        bridge = self.controller.app.engine.plugin_chains.bridges.get(target)
        if bridge is None:
            self.controller.status = "Load this chain before editing its parameters"
            self.status.setText(self.controller.status)
            return
        active_index = sum(not item.get("bypass", False) for item in chain[:index])
        infos = bridge.info.get("chain", [])
        if active_index >= len(infos):
            return
        info = infos[active_index]
        parameters = info.get("parameters", {})
        dialog = QDialog(self)
        dialog.setWindowTitle(f"{info.get('name', 'Plugin')} parameters")
        dialog.resize(540, 620)
        layout = QVBoxLayout(dialog)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        page = QWidget()
        form = QFormLayout(page)
        controls = {}
        for name, parameter in parameters.items():
            box = QDoubleSpinBox()
            box.setRange(0.0, 1.0)
            box.setSingleStep(0.01)
            box.setDecimals(4)
            box.setValue(float(parameter.get("value", 0.0)))
            box.setToolTip(str(parameter.get("display", "")))
            form.addRow(str(parameter.get("name", name)), box)
            controls[name] = box
        if not controls:
            form.addRow(QLabel("This plugin exposes no automatable normalized parameters."))
        scroll.setWidget(page)
        layout.addWidget(scroll, 1)
        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        if dialog.exec() != QDialog.Accepted:
            return
        chain[index]["parameters"] = {name: box.value() for name, box in controls.items()}
        state = info.get("state", "")
        if isinstance(state, str):
            chain[index]["state"] = state
        self._commit(chain, index)
