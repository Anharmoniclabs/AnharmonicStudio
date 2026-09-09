"""Accessible MIDI setup and isolated external-plugin loading."""

from __future__ import annotations

import copy
import json
from pathlib import Path
import threading
import time
import weakref

import numpy as np
from PySide6.QtCore import QObject, QTimer, Signal, Qt
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)
from shiboken6 import isValid

from ..midi_devices import MidiRouter, MidiService, controller_settings
from ..plugin_host import IsolatedPlugin, LivePlugin, UnavailablePlugin
from ..plugin_registry import PluginRegistry, compatibility, default_plugin_paths, discover_plugins
from .window_client import WindowClient, emit_if_alive


class DevicesController(WindowClient, QObject):
    changed = Signal()
    scan_finished = Signal(object, str)
    plugin_finished = Signal(int, str, object, object, str)

    def __init__(self, window):
        super().__init__(window)
        self.app = window
        self.service = MidiService()
        self.registry = PluginRegistry(window.root / "library/_plugin_registry.json")
        try:
            self.registry.load()
        except (OSError, ValueError, TypeError):
            pass
        self.router = MidiRouter(
            window.play_selected_note,
            window.release_selected_note,
            window._pad_pressed,
            window._pad_released,
            lambda: window.pads.bank,
            self._control,
        )
        # Bound methods in the router are replaced with weak dispatchers so child
        # controls cannot retain the entire closed workstation.
        reference = weakref.ref(window)

        def dispatch(name):
            def call(*args):
                target = reference()
                if target is not None and isValid(target):
                    return getattr(target, name)(*args)

            return call

        self.router.note_on = dispatch("play_selected_note")
        self.router.note_off = dispatch("release_selected_note")
        self.router.pad_on = dispatch("_pad_pressed")
        self.router.pad_off = dispatch("_pad_released")
        self.router.bank = lambda: reference().pads.bank if reference() is not None else 0
        self.router.expression = self._expression
        try:
            config = json.loads(str(window.settings.value("midi/controllers", "{}")))
            self.router.settings = controller_settings(config)
            disabled = json.loads(str(window.settings.value("midi/disabled", "[]")))
            if isinstance(disabled, list):
                self.service.disabled = {key for key in disabled[:128] if isinstance(key, str)}
        except (ValueError, TypeError):
            pass
        self.extra_paths = []
        try:
            self.extra_paths = json.loads(str(window.settings.value("plugins/paths", "[]")))
            if not isinstance(self.extra_paths, list) or any(
                not isinstance(p, str) for p in self.extra_paths
            ):
                self.extra_paths = []
        except ValueError:
            pass
        self.dialog = None
        self.scanning = False
        self.scan_error = ""
        self.plugin_status = "Choose a plugin to load"
        self.audio_devices = []
        self._generation = 0
        self._slot_generation = {"instrument": 0, "effect": 0}
        self._loading = False
        self._pending_loads = {}
        self._closed = False
        self._last_ports = ()
        self._last_refresh = 0.0
        self.scan_finished.connect(self._scanned)
        self.plugin_finished.connect(self._plugin_loaded)
        self.timer = QTimer(self)
        self.timer.timeout.connect(self._tick)
        self.timer.start(8)
        self.scanner = QTimer(self)
        self.scanner.timeout.connect(self.scan)
        self.scanner.setInterval(10000)
        # Tests/renderers never enumerate or open real device ports automatically.
        if QApplication.instance().platformName() != "offscreen":
            self.service.start()
            self.scanner.start()
            QTimer.singleShot(0, self, self.scan)
            QTimer.singleShot(0, self, self.sync_project)
        self.destroyed.connect(self.service.close)
        self.destroyed.connect(window.engine.external.close)

    def persist(self):
        self.app.settings.setValue("midi/controllers", json.dumps(self.router.settings))
        self.app.settings.setValue("midi/disabled", json.dumps(sorted(self.service.disabled)))
        self.app.settings.setValue("plugins/paths", json.dumps(self.extra_paths))

    def _control(self, target, value):
        app = self.app
        if app is None:
            return
        if target == "play" and value >= 64 and not app.engine.playing:
            app.toggle_play()
        elif target == "stop" and value >= 64:
            app.stop_all()
        elif target == "record" and value >= 64:
            app.btn_rec.toggle()
        elif target == "master":
            app.master_slider.setValue(round(value / 127 * 100))
        elif target.startswith("track:"):
            try:
                index = int(target.split(":")[1])
                if 0 <= index < len(app.project.tracks):
                    app.project.tracks[index].gain = value / 127
                    app._set_dirty(True)
                    app.mixer.sync()
            except (ValueError, IndexError):
                pass

    def _expression(self, message):
        app = self.app
        if app is not None:
            app.engine.cmds.put(("midiexpression", message))

    def _tick(self):
        if self._closed:
            return
        for event in self.service.drain():
            self.router.handle(*event)
        if self.router.learned:
            self.router.learned = None
            self.persist()
        if self.service.ports != self._last_ports:
            before = {port.id for port in self._last_ports if port.connected}
            now = {port.id for port in self.service.ports if port.connected}
            for key in before - now:
                self.router.release_port(key)
            added = [p.name for p in self.service.ports if p.id in now - before]
            if added:
                self.app.status.showMessage("MIDI connected · " + ", ".join(added), 5000)
            self._last_ports = self.service.ports
        if time.monotonic() - self._last_refresh >= 0.25:
            self._last_refresh = time.monotonic()
            for slot in ("instrument", "effect"):
                bridge = getattr(self.app.engine.external, slot)
                if bridge is not None and bridge.error:
                    state = "silenced" if slot == "instrument" else "bypassed"
                    self.plugin_status = f"{slot.title()} {state} · {bridge.error}"
            self.changed.emit()

    def show(self):
        if self.dialog is None:
            self.dialog = DevicesDialog(self, self.app)
            self.dialog.finished.connect(self._dialog_closed)
        self.dialog.show()
        self.dialog.raise_()
        self.scan()

    def _dialog_closed(self, _result):
        self.dialog = None

    def scan(self):
        self.service.rescan()
        if self.scanning or self._closed:
            return
        self.scanning = True
        paths = [*default_plugin_paths(), *(Path(p) for p in self.extra_paths)]
        reference = weakref.ref(self)
        hardware = QApplication.instance().platformName() != "offscreen"
        if hardware:
            self.service.start()

        def work():
            devices = []
            candidates = []
            error = ""
            try:
                candidates = discover_plugins(paths)
            except Exception as exc:
                error = str(exc)
            if hardware:
                try:
                    import sounddevice as sd
                    from .main_window import pipewire_output_inventory

                    devices = [
                        f"{d['name']} · {int(d['max_input_channels'])} inputs / {int(d['max_output_channels'])} outputs"
                        for d in sd.query_devices()
                    ]
                    devices.extend(p["label"] for p in pipewire_output_inventory())
                except Exception as exc:
                    error = str(exc)
            target = reference()
            if target is not None:
                emit_if_alive(target, "scan_finished", (candidates, devices), error)

        threading.Thread(target=work, name="Device and plugin scanner", daemon=True).start()

    def _scanned(self, result, error):
        if self._closed:
            return
        candidates, self.audio_devices = result
        self.registry.replace_candidates(candidates)
        try:
            self.registry.save()
        except OSError as exc:
            error = str(exc)
        self.scan_error = error
        self.scanning = False
        self.changed.emit()

    def load_plugin(self, slot, specification, *, save=True):
        if slot not in self._slot_generation or self._closed:
            return
        self._generation += 1
        generation = self._generation
        self._slot_generation[slot] = generation
        specification = copy.deepcopy(specification)
        self._pending_loads[slot] = (generation, specification, save)
        self._continue_load()

    def _continue_load(self):
        if self._loading or self._closed or not self._pending_loads:
            return
        slot = next(iter(self._pending_loads))
        generation, specification, save = self._pending_loads.pop(slot)
        self._loading = True
        self.plugin_status = "Loading plugin…"
        reference = weakref.ref(self)
        rate, frames = self.app.engine.sr, self.app.engine.blocksize

        def work():
            bridge = None
            plugin = None
            error = ""
            try:
                plugin = IsolatedPlugin(specification, rate)
                if not plugin.info.get(slot):
                    raise ValueError(f"This plugin is not an {slot}")
                # Warm caches while the previous instrument/effect stays playable.
                silence = np.zeros((frames, 2), np.float32)
                for i in range(3):
                    plugin.render(None if slot == "instrument" else silence, frames, reset=i == 0)
                specification["state"] = plugin.info["state"]
                bridge = LivePlugin(plugin, frames)
            except Exception as exc:
                error = str(exc)
                if plugin is not None:
                    plugin.close()
            target = reference()
            try:
                if target is not None and isValid(target) and not target._closed:
                    target.plugin_finished.emit(
                        generation, slot, bridge, (specification, save), error
                    )
                    return
            except RuntimeError:
                pass
            if bridge is not None:
                bridge.close()

        threading.Thread(target=work, name="Plugin loader", daemon=True).start()

    def _plugin_loaded(self, generation, slot, bridge, saved, error):
        self._loading = False
        if generation != self._slot_generation[slot] or self._closed:
            if bridge is not None:
                bridge.close()
            self._continue_load()
            return
        specification, save = saved
        if error:
            if not save:
                setattr(self.app.engine.external, slot, UnavailablePlugin(error))
            self.plugin_status = "Plugin unavailable · " + error
            key = next(
                (
                    p.id
                    for p in self.registry.candidates.values()
                    if p.path == specification["path"]
                ),
                None,
            )
            if key:
                self.registry.quarantine(key, error)
                try:
                    self.registry.save()
                except OSError:
                    pass
            self.changed.emit()
            self._continue_load()
            return
        if save:
            self.app.snapshot()
            self.app.project.plugins[slot] = specification
            self.app._set_dirty(True)
        old = getattr(self.app.engine.external, slot)
        setattr(self.app.engine.external, slot, bridge)
        if old is not None:
            old.close()
        if slot == "instrument":
            self.app.panic_synth()
            self.app.piano_roll.select_channel(None)
        self.plugin_status = f"{bridge.info['name']} ready · monitoring adds {2 * bridge.blocksize / self.app.engine.sr * 1000:.1f} ms"
        self.changed.emit()
        self._continue_load()

    def remove_plugin(self, slot):
        self._generation += 1
        self._slot_generation[slot] = self._generation
        self._pending_loads.pop(slot, None)
        self.app.snapshot()
        self.app.project.plugins.pop(slot, None)
        self.app._set_dirty(True)
        old = getattr(self.app.engine.external, slot)
        setattr(self.app.engine.external, slot, None)
        if old is not None:
            old.close()
        self.app.engine.cmds.put(("synthpanic",))
        self.plugin_status = "Plugin removed"
        self.changed.emit()

    def sync_project(self):
        self._generation += 1
        self._slot_generation = dict.fromkeys(self._slot_generation, self._generation)
        self._pending_loads.clear()
        self.router.release_port()
        self.app.engine.external.close()
        self.app.engine.cmds.put(("synthpanic",))
        for slot, spec in self.app.project.plugins.items():
            if not spec.get("bypass"):
                setattr(self.app.engine.external, slot, UnavailablePlugin("Loading plugin…"))
                self.load_plugin(slot, spec, save=False)

    def shutdown(self):
        if self._closed:
            return
        self._closed = True
        self._pending_loads.clear()
        self._generation += 1
        self.timer.stop()
        self.scanner.stop()
        self.router.release_port()
        self.service.close()
        self.app.engine.external.close()


class DevicesDialog(QDialog):
    def __init__(self, controller, parent):
        super().__init__(parent)
        self.controller = controller
        self.setAttribute(Qt.WA_DeleteOnClose, True)
        self.setWindowTitle("Devices & Plugins")
        self.resize(800, 660)
        self.setMinimumSize(560, 440)
        layout = QVBoxLayout(self)
        self.tabs = QTabWidget()
        layout.addWidget(self.tabs)
        self._ports = None
        self._plugins = None
        self._parameter_key = None
        self.parameters = {}
        midi = QWidget()
        ml = QVBoxLayout(midi)
        hint = QLabel(
            "Connect a USB MIDI keyboard or pad controller. Available inputs connect automatically.\nFor MPC hardware, enable its MIDI/controller mode first."
        )
        hint.setWordWrap(True)
        ml.addWidget(hint)
        self.port_list = QListWidget()
        self.port_list.setAccessibleName("MIDI inputs")
        self.port_list.currentItemChanged.connect(self._port_selected)
        ml.addWidget(self.port_list)
        form = QFormLayout()
        self.enabled = QCheckBox("Connect this MIDI input")
        self.enabled.toggled.connect(self._enabled)
        form.addRow(self.enabled)
        self.mode = QComboBox()
        self.mode.addItems(["Keys + drum channel", "Keys", "Pads"])
        self.mode.currentTextChanged.connect(self._mode_changed)
        form.addRow("Play", self.mode)
        self.base_note = QSpinBox()
        self.base_note.setRange(0, 112)
        self.base_note.setValue(36)
        self.base_note.valueChanged.connect(self._mode_changed)
        form.addRow("First pad MIDI note", self.base_note)
        self.learn_target = QComboBox()
        for label, value in [
            ("Master volume", "master"),
            ("Play", "play"),
            ("Stop", "stop"),
            ("Record", "record"),
        ]:
            self.learn_target.addItem(label, value)
        for index in range(16):
            self.learn_target.addItem(f"Pad {index + 1}", f"pad:{index}")
        for index in range(8):
            self.learn_target.addItem(f"Track {index + 1} volume", f"track:{index}")
        learn_row = QHBoxLayout()
        learn_row.addWidget(self.learn_target, 1)
        learn = QPushButton("MIDI Learn")
        learn.clicked.connect(self._learn)
        learn_row.addWidget(learn)
        cancel = QPushButton("Cancel learn")
        cancel.clicked.connect(lambda: setattr(controller.router, "learn", None))
        learn_row.addWidget(cancel)
        form.addRow(learn_row)
        ml.addLayout(form)
        self.activity = QLabel()
        self.activity.setWordWrap(True)
        ml.addWidget(self.activity)
        panic = QPushButton("Release held notes")
        panic.clicked.connect(lambda: controller.router.release_port())
        ml.addWidget(panic)
        self.tabs.addTab(midi, "MIDI controllers")

        plugins = QWidget()
        pl = QVBoxLayout(plugins)
        description = QLabel(
            "Load a native VST3 instrument or master effect. Settings save with the song.\nOther formats are listed with their compatibility status."
        )
        description.setWordWrap(True)
        pl.addWidget(description)
        self.plugin_list = QListWidget()
        self.plugin_list.setAccessibleName("Discovered plugins")
        pl.addWidget(self.plugin_list, 1)
        buttons = QHBoxLayout()
        folder = QPushButton("Add plugin folder…")
        folder.clicked.connect(self._folder)
        buttons.addWidget(folder)
        for label, slot in (("Load instrument", "instrument"), ("Load master effect", "effect")):
            button = QPushButton(label)
            button.clicked.connect(lambda _checked=False, slot=slot: self._load(slot))
            buttons.addWidget(button)
        pl.addLayout(buttons)
        self.slot = QComboBox()
        self.slot.addItems(["instrument", "effect"])
        self.slot.currentIndexChanged.connect(self._refresh_parameters)
        row = QHBoxLayout()
        row.addWidget(QLabel("Loaded slot"))
        row.addWidget(self.slot)
        remove = QPushButton("Remove plugin")
        remove.clicked.connect(lambda: controller.remove_plugin(self.slot.currentText()))
        row.addWidget(remove)
        pl.addLayout(row)
        self.parameter_scroll = QScrollArea()
        self.parameter_scroll.setWidgetResizable(True)
        pl.addWidget(self.parameter_scroll, 1)
        apply = QPushButton("Apply plugin parameters")
        apply.clicked.connect(self._apply_parameters)
        pl.addWidget(apply)
        self.plugin_status = QLabel()
        self.plugin_status.setWordWrap(True)
        pl.addWidget(self.plugin_status)
        self.tabs.addTab(plugins, "Plugins")

        audio = QWidget()
        al = QVBoxLayout(audio)
        self.audio_list = QListWidget()
        self.audio_list.setAccessibleName("Audio interfaces")
        al.addWidget(self.audio_list)
        setup = QPushButton("Choose audio input, output and buffer…")
        setup.clicked.connect(controller.app.show_audio_setup)
        al.addWidget(setup)
        self.tabs.addTab(audio, "Audio interfaces")
        bottom = QHBoxLayout()
        scan = QPushButton("Scan now")
        scan.clicked.connect(controller.scan)
        bottom.addWidget(scan)
        bottom.addStretch(1)
        close = QPushButton("Close")
        close.clicked.connect(self.accept)
        bottom.addWidget(close)
        layout.addLayout(bottom)
        controller.changed.connect(self.refresh)
        self.refresh()

    def _selected_port(self):
        item = self.port_list.currentItem()
        return item.data(Qt.UserRole) if item is not None else None

    def _port_selected(self, *_args):
        key = self._selected_port()
        settings = self.controller.router.settings.get(key, {})
        for control in (self.enabled, self.mode, self.base_note):
            control.blockSignals(True)
            control.setEnabled(key is not None)
        self.enabled.setChecked(key is not None and key not in self.controller.service.disabled)
        self.mode.setCurrentText(settings.get("mode", "Keys + drum channel"))
        self.base_note.setValue(int(settings.get("pad_base", 36)))
        for control in (self.enabled, self.mode, self.base_note):
            control.blockSignals(False)

    def _enabled(self, checked):
        key = self._selected_port()
        if key is not None:
            self.controller.service.enable(key, checked)
            if not checked:
                self.controller.router.release_port(key)
            self.controller.persist()

    def _mode_changed(self, *_args):
        key = self._selected_port()
        if key is not None:
            self.controller.router.release_port(key)
            settings = self.controller.router.settings.setdefault(key, {})
            settings.update(mode=self.mode.currentText(), pad_base=self.base_note.value())
            self.controller.persist()

    def _learn(self):
        key = self._selected_port()
        if key is not None:
            self.controller.router.learn = (key, self.learn_target.currentData())

    def _folder(self):
        folder = QFileDialog.getExistingDirectory(self, "Plugin folder")
        if folder and folder not in self.controller.extra_paths:
            self.controller.extra_paths.append(folder)
            self.controller.persist()
            self.controller.scan()

    def _load(self, slot):
        item = self.plugin_list.currentItem()
        if item is None:
            return
        candidate = self.controller.registry.candidates[item.data(Qt.UserRole)]
        status = compatibility(candidate)
        if status != "Ready to load":
            self.controller.plugin_status = status
            self.refresh()
            return
        self.controller.registry.restore(candidate.id)
        self.slot.setCurrentText(slot)
        self.controller.load_plugin(slot, {"path": candidate.path})

    def _refresh_parameters(self, *_args):
        slot = self.slot.currentText()
        bridge = getattr(self.controller.app.engine.external, slot)
        key = (slot, id(bridge))
        if key == self._parameter_key:
            return
        self._parameter_key = key
        page = QWidget()
        form = QFormLayout(page)
        self.parameters = {}
        if bridge is not None:
            for name, info in bridge.info.get("parameters", {}).items():
                box = QDoubleSpinBox()
                box.setRange(0, 1)
                box.setSingleStep(0.01)
                box.setDecimals(4)
                box.setValue(info["value"])
                box.setToolTip("Normalized value from 0 to 1 · " + info.get("display", ""))
                box.setAccessibleName(info["name"])
                form.addRow(info["name"], box)
                self.parameters[name] = box
        else:
            form.addRow(QLabel("No plugin loaded in this slot"))
        self.parameter_scroll.setWidget(page)

    def _apply_parameters(self):
        slot = self.slot.currentText()
        spec = copy.deepcopy(self.controller.app.project.plugins.get(slot))
        if spec is not None:
            spec["parameters"] = {name: box.value() for name, box in self.parameters.items()}
            self.controller.load_plugin(slot, spec)

    def refresh(self):
        controller = self.controller
        ports = controller.service.ports
        if ports != self._ports:
            selected = self._selected_port()
            self._ports = ports
            self.port_list.clear()
            for port in ports:
                state = "Connected" if port.connected else (port.error or "Not connected")
                item = QListWidgetItem(f"{port.name} · {state}")
                item.setData(Qt.UserRole, port.id)
                self.port_list.addItem(item)
                if port.id == selected:
                    self.port_list.setCurrentItem(item)
            if self.port_list.currentRow() < 0 and self.port_list.count():
                self.port_list.setCurrentRow(0)
            self._port_selected()
        self.activity.setText(
            "Move a knob or press the chosen pad to learn it"
            if controller.router.learn
            else (controller.service.error or controller.router.last_event)
        )
        plugins = [
            (p.id, p.name, compatibility(p), controller.registry.quarantined.get(p.id, ""))
            for p in controller.registry.candidates.values()
        ]
        if plugins != self._plugins:
            current = self.plugin_list.currentItem()
            selected = current.data(Qt.UserRole) if current else None
            self._plugins = plugins
            self.plugin_list.clear()
            for key, name, status, failure in plugins:
                item = QListWidgetItem(
                    f"{name} · {('Previous load failed: ' + failure) if failure else status}"
                )
                item.setData(Qt.UserRole, key)
                self.plugin_list.addItem(item)
                if key == selected:
                    self.plugin_list.setCurrentItem(item)
            if self.plugin_list.currentRow() < 0 and self.plugin_list.count():
                self.plugin_list.setCurrentRow(0)
        self.plugin_status.setText(
            controller.plugin_status
            + (" · Scanning…" if controller.scanning else "")
            + (" · " + controller.scan_error if controller.scan_error else "")
        )
        devices = controller.audio_devices
        if [self.audio_list.item(i).text() for i in range(self.audio_list.count())] != devices:
            self.audio_list.clear()
            self.audio_list.addItems(devices)
        self._refresh_parameters()
