"""User-facing controls for the compiled mixer routing graph."""

from __future__ import annotations

from copy import deepcopy

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
)

from .model import uid
from .plugin_latency import plugin_path_latency_samples
from .workflow_commands import CommandSpec
from .workflow_routing import compile_routing
from .workflow_state import ensure_workflow, validate_workflow


def attach_routing_ui(window, command_controller):
    existing = getattr(window, "routing_controller", None)
    if existing is not None:
        return existing
    controller = RoutingController(window, command_controller)
    window.routing_controller = controller
    return controller


class RoutingController:
    def __init__(self, window, command_controller):
        self.window = window
        self.command_controller = command_controller
        self.registry = command_controller.registry
        self._register_commands()
        command_controller._reindex_bindings()
        self._build_menu()
        prepare = getattr(window.engine, "prepare_routing", None)
        if prepare is not None:
            prepare(window.project)

    def _register_commands(self) -> None:
        specs = (
            CommandSpec(
                "mixer.bus_create",
                "Create routing bus",
                self.create_bus,
                category="Mixer",
                keywords=("aux", "return", "group", "routing"),
            ),
            CommandSpec(
                "mixer.track_output",
                "Set selected track output",
                self.set_track_output,
                category="Mixer",
                keywords=("route", "bus", "output"),
            ),
            CommandSpec(
                "mixer.send_create",
                "Create pre/post-fader send",
                self.create_send,
                category="Mixer",
                keywords=("aux", "return", "parallel", "send"),
            ),
            CommandSpec(
                "mixer.routing_manage",
                "Manage mixer routing",
                self.show_manager,
                category="Mixer",
                keywords=("bus", "send", "aux", "return", "routing"),
            ),
            CommandSpec(
                "mixer.latency_report",
                "Plugin latency / PDC report",
                self.show_latency_report,
                category="Mixer",
                keywords=("latency", "pdc", "plugin", "delay compensation"),
            ),
        )
        for spec in specs:
            try:
                self.registry.register(spec)
            except ValueError as exc:
                if "duplicate command id" not in str(exc):
                    raise

    def _build_menu(self) -> None:
        menu = self.window.menuBar().addMenu("Routing")
        for command_id in (
            "mixer.bus_create",
            "mixer.track_output",
            "mixer.send_create",
            "mixer.routing_manage",
            "mixer.latency_report",
        ):
            action = menu.addAction(self.registry.get(command_id).title)
            action.triggered.connect(
                lambda _checked=False, command_id=command_id: self.command_controller._execute(
                    command_id
                )
            )

    def _routing(self) -> dict:
        value = ensure_workflow(self.window.project).get("routing", {})
        return deepcopy(value) if isinstance(value, dict) else {}

    def _commit(self, routing: dict) -> dict:
        workflow = deepcopy(ensure_workflow(self.window.project))
        if routing and any(routing.get(key) for key in ("buses", "track_outputs", "sends")):
            workflow["routing"] = routing
        else:
            workflow.pop("routing", None)
        checked = validate_workflow(workflow)

        # Compile before mutating the project so a graph error cannot leave the
        # current song half-edited.
        probe = deepcopy(self.window.project)
        probe.workflow = checked
        compile_routing(probe)

        self.window.snapshot()
        self.window.project.workflow = checked
        prepare = getattr(self.window.engine, "prepare_routing", None)
        if prepare is not None:
            prepare(self.window.project)
        self.window._set_dirty(True)
        self.window.status.showMessage("Mixer routing updated", 3000)
        return checked.get("routing", {})

    def _bus_choices(self, routing: dict, *, include_master: bool = True):
        choices = []
        if include_master:
            choices.append(("Master", "master"))
        for bus in routing.get("buses", []):
            choices.append((f"Bus · {bus['name']}", bus["id"]))
        return choices

    def _selected_track_index(self) -> int:
        selected = int(getattr(self.window.mixer, "selected", 0))
        return max(0, min(len(self.window.project.tracks) - 1, selected))

    def create_bus(self):
        routing = self._routing()
        name, ok = QInputDialog.getText(self.window, "Create bus", "Bus / aux name")
        if not ok:
            return None
        name = name.strip() or "BUS"
        choices = self._bus_choices(routing)
        labels = [label for label, _target in choices]
        label, ok = QInputDialog.getItem(
            self.window, "Create bus", "Output", labels, 0, False
        )
        if not ok:
            return None
        output = dict(choices)[label]
        bus = {
            "id": f"bus:{uid()}",
            "name": name,
            "gain": 1.0,
            "pan": 0.0,
            "mute": False,
            "output": output,
        }
        routing.setdefault("buses", []).append(bus)
        self._commit(routing)
        return bus

    def set_track_output(self):
        routing = self._routing()
        index = self._selected_track_index()
        track = self.window.project.tracks[index]
        choices = self._bus_choices(routing)
        labels = [label for label, _target in choices]
        if len(labels) == 1:
            raise ValueError("create a routing bus first")
        current_target = routing.get("track_outputs", {}).get(track.id, "master")
        current = next(
            (i for i, (_label, target) in enumerate(choices) if target == current_target), 0
        )
        label, ok = QInputDialog.getItem(
            self.window,
            "Track output",
            f"{track.name} output",
            labels,
            current,
            False,
        )
        if not ok:
            return None
        target = dict(choices)[label]
        outputs = routing.setdefault("track_outputs", {})
        if target == "master":
            outputs.pop(track.id, None)
        else:
            outputs[track.id] = target
        if not outputs:
            routing.pop("track_outputs", None)
        self._commit(routing)
        return target

    def create_send(self):
        routing = self._routing()
        buses = routing.get("buses", [])
        if not buses:
            raise ValueError("create a routing bus before adding an aux/parallel send")

        sources = [
            (f"Track {index + 1} · {track.name}", track.id)
            for index, track in enumerate(self.window.project.tracks)
        ]
        sources.extend((f"Bus · {bus['name']}", bus["id"]) for bus in buses)
        source_labels = [label for label, _source in sources]
        current = self._selected_track_index()
        source_label, ok = QInputDialog.getItem(
            self.window, "Create send", "Source", source_labels, current, False
        )
        if not ok:
            return None
        source = dict(sources)[source_label]

        targets = [
            (label, target)
            for label, target in self._bus_choices(routing)
            if target != source
        ]
        target_labels = [label for label, _target in targets]
        target_label, ok = QInputDialog.getItem(
            self.window, "Create send", "Destination", target_labels, 0, False
        )
        if not ok:
            return None
        target = dict(targets)[target_label]
        gain, ok = QInputDialog.getDouble(
            self.window, "Create send", "Send gain", 0.5, 0.0, 2.0, 3
        )
        if not ok:
            return None
        position, ok = QInputDialog.getItem(
            self.window,
            "Create send",
            "Tap position",
            ["Post-fader", "Pre-fader"],
            0,
            False,
        )
        if not ok:
            return None
        send = {
            "id": f"send:{uid()}",
            "source": source,
            "target": target,
            "gain": gain,
            "pre_fader": position == "Pre-fader",
            "enabled": True,
        }
        routing.setdefault("sends", []).append(send)
        self._commit(routing)
        return send

    def _bus_by_id(self, routing: dict, bus_id: str):
        return next((bus for bus in routing.get("buses", []) if bus.get("id") == bus_id), None)

    def _source_name(self, routing: dict, source: str) -> str:
        for index, track in enumerate(self.window.project.tracks):
            if track.id == source:
                return f"Track {index + 1} · {track.name}"
        bus = self._bus_by_id(routing, source)
        return f"Bus · {bus['name']}" if bus else source

    def _target_name(self, routing: dict, target: str) -> str:
        if target == "master":
            return "Master"
        bus = self._bus_by_id(routing, target)
        return f"Bus · {bus['name']}" if bus else target

    def show_manager(self):
        routing = self._routing()
        dialog = QDialog(self.window)
        dialog.setWindowTitle("Mixer routing")
        layout = QVBoxLayout(dialog)
        summary = QLabel(
            "Tracks can feed one output bus plus parallel pre/post-fader sends. "
            "Bus-to-bus cycles are rejected before they reach the audio callback."
        )
        summary.setWordWrap(True)
        layout.addWidget(summary)
        items = QListWidget()
        layout.addWidget(items, 1)

        def refill():
            items.clear()
            outputs = routing.get("track_outputs", {})
            for index, track in enumerate(self.window.project.tracks):
                target = outputs.get(track.id, "master")
                row = QListWidgetItem(
                    f"OUT  Track {index + 1} · {track.name}  →  {self._target_name(routing, target)}"
                )
                row.setData(Qt.UserRole, ("track", track.id))
                items.addItem(row)
            for bus in routing.get("buses", []):
                state = "MUTED" if bus.get("mute") else f"gain {bus.get('gain', 1.0):.3f}"
                row = QListWidgetItem(
                    f"BUS  {bus['name']}  →  {self._target_name(routing, bus.get('output', 'master'))}  · {state}"
                )
                row.setData(Qt.UserRole, ("bus", bus["id"]))
                items.addItem(row)
            for send in routing.get("sends", []):
                tap = "PRE" if send.get("pre_fader") else "POST"
                enabled = "" if send.get("enabled", True) else " · OFF"
                row = QListWidgetItem(
                    f"SEND {tap}  {self._source_name(routing, send['source'])}  →  "
                    f"{self._target_name(routing, send['target'])}  · {send.get('gain', 1.0):.3f}{enabled}"
                )
                row.setData(Qt.UserRole, ("send", send["id"]))
                items.addItem(row)

        buttons = QHBoxLayout()
        edit = QPushButton("Edit")
        remove = QPushButton("Remove")
        buttons.addWidget(edit)
        buttons.addWidget(remove)
        buttons.addStretch(1)
        layout.addLayout(buttons)
        close = QDialogButtonBox(QDialogButtonBox.Close)
        close.rejected.connect(dialog.reject)
        layout.addWidget(close)

        def edit_selected():
            item = items.currentItem()
            if item is None:
                return
            kind, identifier = item.data(Qt.UserRole)
            if kind == "track":
                index = self.window.project.track_index(identifier)
                self.window.mixer.selected = index
                dialog.hide()
                try:
                    self.set_track_output()
                finally:
                    dialog.show()
                routing.clear()
                routing.update(self._routing())
            elif kind == "bus":
                bus = self._bus_by_id(routing, identifier)
                if bus is None:
                    return
                gain, ok = QInputDialog.getDouble(
                    dialog, "Edit bus", "Gain", float(bus.get("gain", 1.0)), 0.0, 2.0, 3
                )
                if not ok:
                    return
                pan, ok = QInputDialog.getDouble(
                    dialog, "Edit bus", "Pan (-1 left, +1 right)", float(bus.get("pan", 0.0)), -1.0, 1.0, 3
                )
                if not ok:
                    return
                muted, ok = QInputDialog.getItem(
                    dialog,
                    "Edit bus",
                    "State",
                    ["Active", "Muted"],
                    1 if bus.get("mute") else 0,
                    False,
                )
                if not ok:
                    return
                bus["gain"], bus["pan"], bus["mute"] = gain, pan, muted == "Muted"
                self._commit(routing)
            elif kind == "send":
                send = next(
                    (entry for entry in routing.get("sends", []) if entry.get("id") == identifier),
                    None,
                )
                if send is None:
                    return
                gain, ok = QInputDialog.getDouble(
                    dialog, "Edit send", "Gain", float(send.get("gain", 1.0)), 0.0, 2.0, 3
                )
                if not ok:
                    return
                position, ok = QInputDialog.getItem(
                    dialog,
                    "Edit send",
                    "Tap position",
                    ["Post-fader", "Pre-fader"],
                    1 if send.get("pre_fader") else 0,
                    False,
                )
                if not ok:
                    return
                state, ok = QInputDialog.getItem(
                    dialog,
                    "Edit send",
                    "State",
                    ["Enabled", "Disabled"],
                    0 if send.get("enabled", True) else 1,
                    False,
                )
                if not ok:
                    return
                send["gain"] = gain
                send["pre_fader"] = position == "Pre-fader"
                send["enabled"] = state == "Enabled"
                self._commit(routing)
            routing.clear()
            routing.update(self._routing())
            refill()

        def remove_selected():
            item = items.currentItem()
            if item is None:
                return
            kind, identifier = item.data(Qt.UserRole)
            if kind == "track":
                routing.setdefault("track_outputs", {}).pop(identifier, None)
            elif kind == "send":
                routing["sends"] = [
                    send for send in routing.get("sends", []) if send.get("id") != identifier
                ]
            elif kind == "bus":
                routing["buses"] = [
                    bus for bus in routing.get("buses", []) if bus.get("id") != identifier
                ]
                outputs = routing.setdefault("track_outputs", {})
                for track_id, target in list(outputs.items()):
                    if target == identifier:
                        outputs.pop(track_id, None)
                routing["sends"] = [
                    send
                    for send in routing.get("sends", [])
                    if send.get("source") != identifier and send.get("target") != identifier
                ]
                for bus in routing.get("buses", []):
                    if bus.get("output") == identifier:
                        bus["output"] = "master"
            for key in ("track_outputs", "sends", "buses"):
                if not routing.get(key):
                    routing.pop(key, None)
            self._commit(routing)
            routing.clear()
            routing.update(self._routing())
            refill()

        edit.clicked.connect(edit_selected)
        remove.clicked.connect(remove_selected)
        refill()
        dialog.resize(760, 540)
        dialog.exec()

    def show_latency_report(self):
        engine = self.window.engine
        instrument = engine.external.instrument
        effect = engine.external.effect

        def describe(name, plugin, bridge):
            if plugin is None:
                return f"{name}: none"
            info = getattr(plugin, "info", {})
            plugin_name = info.get("name", name) if isinstance(info, dict) else name
            intrinsic = plugin_path_latency_samples(plugin, include_live_bridge=False)
            total = plugin_path_latency_samples(plugin, include_live_bridge=bridge)
            return (
                f"{name}: {plugin_name}\n"
                f"  intrinsic {intrinsic} samples / {intrinsic / engine.sr * 1000.0:.2f} ms\n"
                f"  live path {total} samples / {total / engine.sr * 1000.0:.2f} ms"
            )

        pdc = getattr(getattr(engine, "plugin_pdc", None), "delay_samples", 0)
        text = "\n\n".join(
            (
                describe("Instrument", instrument, True),
                describe("Master effect", effect, True),
                f"Dry-track compensation: {pdc} samples / {pdc / engine.sr * 1000.0:.2f} ms",
            )
        )
        QMessageBox.information(self.window, "Plugin latency / PDC", text)
        return text
