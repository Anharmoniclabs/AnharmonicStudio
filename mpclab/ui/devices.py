"""Accessible MIDI setup and isolated external-plugin loading."""

from __future__ import annotations

import copy
import platform
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
    QSpinBox,
    QTabWidget,
    QVBoxLayout,
    QScrollArea,
    QWidget,
)
from shiboken6 import isValid

from ..midi_devices import MidiService, controller_settings
from ..midi_output import MidiOutputService
from ..device_profiles import PROFILES, connection_help
from ..plugin_host import IsolatedPlugin, LivePlugin, UnavailablePlugin
from ..plugin_registry import PluginRegistry, compatibility, default_plugin_paths, discover_plugins
from .window_client import WindowClient, emit_if_alive


class DevicesController(WindowClient, QObject):
    changed = Signal()
    scan_finished = Signal(object, str)
    plugin_finished = Signal(int, str, object, object, str)

    def _channel_destination(self, note, _port, channel):
        ids = tuple(i.id for i in self.app.project.instruments if i.midi_channel == channel)
        if not ids and self.app.studio.selected == self.app.TAB_SYNTH:
            # Match musical typing: Instruments owns unassigned MIDI keys.
            # The router retains this destination until release, even if the
            # player changes workspace or selected instrument while holding it.
            ids = (self.app.project.selected_instrument,)
        return ("routed", (channel, note, ids)) if ids else ("note", note)

    def _channel_note_on(self, destination, velocity):
        channel, note, ids = destination
        for instrument_id in ids:
            self.app.play_synth_note(note, velocity, instrument_id=instrument_id, channel=channel)

    def _channel_note_off(self, destination):
        channel, note, ids = destination
        for instrument_id in ids:
            self.app.release_synth_note(note, instrument_id=instrument_id)

    def __init__(self, window):
        super().__init__(window)
        self.app = window
        self.service = MidiService()
        self.output = MidiOutputService()
        self.output.select(
            str(window.settings.value("midi/output", "") or ""),
            str(window.settings.value("midi/output_clock", "false")).lower() == "true",
        )
        window.engine.midi.output = self.output
        try:
            window.engine.synth_polyphony = min(
                64, max(8, int(window.settings.value("midi/polyphony", 32)))
            )
        except (ValueError, TypeError):
            window.engine.synth_polyphony = 32
        self.registry = PluginRegistry(window.root / "library/_plugin_registry.json")
        try:
            self.registry.load()
        except (OSError, ValueError, TypeError):
            pass
        self.router = window.engine.midi.router
        self.router.resolve_note = self._channel_destination
        self.router.channel_note_on = self._channel_note_on
        self.router.channel_note_off = self._channel_note_off
        window.engine.midi.source = self.service
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
            self.output.start()
            self.scanner.start()
            QTimer.singleShot(0, self, self.scan)
            QTimer.singleShot(0, self, self.sync_project)
        self.destroyed.connect(self.service.close)
        self.destroyed.connect(self.output.close)
        self.destroyed.connect(window.engine.external.close)

    def persist(self):
        self.router.settings = controller_settings(self.router.settings)
        if any(config.get("clock") for config in self.router.settings.values()):
            self.output.clock_enabled = False
        self.app.settings.setValue("midi/controllers", json.dumps(self.router.settings))
        self.app.settings.setValue("midi/output", self.output.selected)
        self.app.settings.setValue("midi/output_clock", self.output.clock_enabled)
        self.app.settings.setValue("midi/polyphony", self.app.engine.synth_polyphony)
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
        performance = self.app.engine.midi
        performance.route = (
            self.app.piano_roll.target_pad,
            getattr(self.app.piano_roll, "target_instrument", None)
            or self.app.project.selected_instrument,
            self.app.pads.bank,
        )
        while performance.notifications:
            kind, target, value = performance.notifications.popleft()
            if kind == "note":
                self.app.synth_panel.keyboard.set_note_active(target, value)
            elif target == "record":
                self._control(target, value)
            elif target in ("bank_next", "bank_previous"):
                self.app.pads.bank = performance.route[2]
                self.app.pads.update()
        if self.router.learned:
            self.router.learned = None
            self.persist()
        if self.service.ports != self._last_ports:
            before = {port.id for port in self._last_ports if port.connected}
            now = {port.id for port in self.service.ports if port.connected}
            for key in before - now:
                self.app.engine.midi.release(key)
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
            for instrument in self.app.project.instruments:
                bridge = self.app.engine.external_for(instrument.id).instrument
                if bridge is not None and bridge.error:
                    self.plugin_status = f"{instrument.name} silenced · {bridge.error}"
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
            outputs = []
            candidates = []
            error = ""
            try:
                candidates = discover_plugins(paths)
            except Exception as exc:
                error = str(exc)
            if hardware:
                try:
                    import sounddevice as sd
                    from .main_window import pipewire_output_inventory, output_device_inventory

                    devices = [
                        f"{d['name']} · {int(d['max_input_channels'])} inputs / {int(d['max_output_channels'])} outputs"
                        for d in sd.query_devices()
                    ]
                    devices.extend(p["label"] for p in pipewire_output_inventory())
                    outputs, _default_output = output_device_inventory()
                except Exception as exc:
                    error = str(exc)
            target = reference()
            if target is not None:
                emit_if_alive(target, "scan_finished", (candidates, devices, outputs), error)

        threading.Thread(target=work, name="Device and plugin scanner", daemon=True).start()

    def _scanned(self, result, error):
        if self._closed:
            return
        candidates, self.audio_devices = result[:2]
        if len(result) > 2 and not error:
            self._recover_output(result[2])
        self.registry.replace_candidates(candidates)
        try:
            self.registry.save()
        except OSError as exc:
            error = str(exc)
        self.scan_error = error
        self.scanning = False
        self.changed.emit()

    def _recover_output(self, outputs):
        app = self.app
        if app.track_capture.busy or app.vocal_panel.recorder.recording:
            return
        key = app._audio_output_key
        if not key:
            return
        matches = [item for item in outputs if item["key"] == key]
        if len(matches) != 1:
            if getattr(app.engine.stream, "active", True) is False:
                app.engine.stop()
                app._audio_start_error = "Selected output disconnected; waiting for reconnect"
            return
        device = matches[0]["index"]
        if app.engine.stream is None or not getattr(app.engine.stream, "active", True):
            try:
                app.engine.stop()
                app.engine.start(device=device)
                app._audio_start_error = None
                app.status.showMessage(f"Audio reconnected · {matches[0]['name']}", 4000)
            except Exception as exc:
                app._audio_start_error = str(exc)

    def _plugin_target(self, slot):
        """Resolve one plugin slot to its bridge object and saved-spec location.

        ``slot`` is either the legacy string "instrument"/"effect" (the
        project-wide default instrument and master effect), or a tuple
        ``("instrument", instrument_id)`` naming one stable, independent
        instrument's own plugin instance.
        """
        if isinstance(slot, tuple):
            instrument_id = slot[1]
            engine = self.app.engine

            def get_bridge():
                return engine.external_for(instrument_id).instrument

            def set_bridge(value):
                engine.external_for(instrument_id).instrument = value

            def get_spec():
                instrument = next(
                    (i for i in self.app.project.instruments if i.id == instrument_id), None
                )
                return instrument.plugin if instrument else None

            def set_spec(value):
                instrument = next(
                    (i for i in self.app.project.instruments if i.id == instrument_id), None
                )
                if instrument is not None:
                    instrument.plugin = value

            return get_bridge, set_bridge, get_spec, set_spec

        def get_bridge():
            return getattr(self.app.engine.external, slot)

        def set_bridge(value):
            setattr(self.app.engine.external, slot, value)

        def get_spec():
            return self.app.project.plugins.get(slot)

        def set_spec(value):
            if value is None:
                self.app.project.plugins.pop(slot, None)
            else:
                self.app.project.plugins[slot] = value

        return get_bridge, set_bridge, get_spec, set_spec

    def load_instrument_plugin(self, instrument_id, specification, *, save=True):
        """Load a plugin into one stable instrument's own independent slot."""
        self.load_plugin(("instrument", instrument_id), specification, save=save)

    def remove_instrument_plugin(self, instrument_id):
        self.remove_plugin(("instrument", instrument_id))

    def load_plugin(self, slot, specification, *, save=True):
        valid = slot in ("instrument", "effect") or (
            isinstance(slot, tuple) and len(slot) == 2 and slot[0] == "instrument"
        )
        if not valid or self._closed:
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
        is_instrument = slot == "instrument" or (isinstance(slot, tuple) and slot[0] == "instrument")

        def work():
            bridge = None
            plugin = None
            error = ""
            try:
                plugin = IsolatedPlugin(specification, rate)
                if not plugin.info.get("instrument" if is_instrument else "effect"):
                    raise ValueError(f"This plugin is not an {'instrument' if is_instrument else 'effect'}")
                # Warm caches while the previous instrument/effect stays playable.
                silence = np.zeros((frames, 2), np.float32)
                for i in range(3):
                    plugin.render(None if is_instrument else silence, frames, reset=i == 0)
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
        if generation != self._slot_generation.get(slot) or self._closed:
            if bridge is not None:
                bridge.close()
            self._continue_load()
            return
        specification, save = saved
        get_bridge, set_bridge, get_spec, set_spec = self._plugin_target(slot)
        is_instrument = slot == "instrument" or (isinstance(slot, tuple) and slot[0] == "instrument")
        if error:
            if not save:
                set_bridge(UnavailablePlugin(error))
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
            set_spec(specification)
            self.app._set_dirty(True)
        old = get_bridge()
        set_bridge(bridge)
        if old is not None:
            old.close()
        if is_instrument:
            self.app.panic_synth()
            if slot == "instrument":
                self.app.piano_roll.select_channel(None)
        self.plugin_status = f"{bridge.info['name']} ready · monitoring adds {2 * bridge.blocksize / self.app.engine.sr * 1000:.1f} ms"
        self.changed.emit()
        self._continue_load()

    def remove_plugin(self, slot):
        self._generation += 1
        self._slot_generation[slot] = self._generation
        self._pending_loads.pop(slot, None)
        get_bridge, set_bridge, _get_spec, set_spec = self._plugin_target(slot)
        self.app.snapshot()
        set_spec(None)
        self.app._set_dirty(True)
        old = get_bridge()
        set_bridge(None)
        if old is not None:
            old.close()
        self.app.engine.cmds.put(("synthpanic",))
        self.plugin_status = "Plugin removed"
        self.changed.emit()

    def sync_project(self):
        self._generation += 1
        self._slot_generation = {"instrument": self._generation, "effect": self._generation}
        self._pending_loads.clear()
        self.app.engine.midi.release()
        self.app.engine.external.close()
        for external in self.app.engine.external_instruments.values():
            external.close()
        self.app.engine.external_instruments.clear()
        self.app.engine.cmds.put(("synthpanic",))
        for slot, spec in self.app.project.plugins.items():
            if not spec.get("bypass"):
                setattr(self.app.engine.external, slot, UnavailablePlugin("Loading plugin…"))
                self.load_plugin(slot, spec, save=False)
        for instrument in self.app.project.instruments:
            spec = instrument.plugin
            if spec and not spec.get("bypass"):
                self.app.engine.external_for(instrument.id).instrument = UnavailablePlugin(
                    "Loading plugin…"
                )
                self.load_instrument_plugin(instrument.id, spec, save=False)

    def shutdown(self):
        if self._closed:
            return
        self._closed = True
        self._pending_loads.clear()
        self._generation += 1
        self.timer.stop()
        self.scanner.stop()
        self.app.engine.midi.release()
        self.service.close()
        self.output.close()
        self.app.engine.external.close()
        for external in self.app.engine.external_instruments.values():
            external.close()
        self.app.engine.external_instruments.clear()


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
            "Connect a keyboard or pads, select a sound, and play. The activity indicator confirms incoming notes."
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
        self.profile = QComboBox()
        for key, profile in PROFILES.items():
            self.profile.addItem(profile["name"], key)
        self.profile.activated.connect(self._profile_changed)
        form.addRow("Device role", self.profile)
        self.transpose = QSpinBox()
        self.transpose.setRange(-48, 48)
        self.transpose.setSuffix(" semitones")
        self.channel = QComboBox()
        self.channel.addItem("All channels", -1)
        for number in range(16):
            self.channel.addItem(str(number + 1), number)
        self.velocity = QComboBox()
        for curve in ("linear", "soft", "hard", "fixed"):
            self.velocity.addItem(curve.title(), curve)
        self.encoder = QComboBox()
        self.encoder.addItem("Absolute knobs", "absolute")
        self.encoder.addItem("Relative encoders (two's complement)", "relative")
        self.takeover = QCheckBox("Pick up the current value before changing it")
        self.follow_clock = QCheckBox("Follow this device's tempo and transport")
        self.transport_enabled = QCheckBox("Accept transport buttons")
        for label, control in (
            ("Transpose", self.transpose),
            ("Input channel", self.channel),
            ("Touch", self.velocity),
            ("Knobs", self.encoder),
            ("Soft takeover", self.takeover),
            ("Sync input", self.follow_clock),
            ("Transport", self.transport_enabled),
        ):
            form.addRow(label, control)
            if isinstance(control, QComboBox):
                control.currentIndexChanged.connect(self._extended_settings)
            elif isinstance(control, QSpinBox):
                control.valueChanged.connect(self._extended_settings)
            else:
                control.toggled.connect(self._extended_settings)
        self.polyphony = QComboBox()
        for count in (8, 16, 32, 64):
            self.polyphony.addItem(str(count), count)
        self.polyphony.setCurrentIndex(
            self.polyphony.findData(controller.app.engine.synth_polyphony)
        )
        self.polyphony.currentIndexChanged.connect(self._polyphony_changed)
        form.addRow("Instrument voices", self.polyphony)
        self.output_port = QComboBox()
        self.output_port.addItem("No MIDI output", "")
        self.output_clock = QCheckBox("Send tempo and transport to this output")
        self.output_port.activated.connect(self._output_changed)
        self.output_clock.toggled.connect(self._output_changed)
        form.addRow("MIDI output", self.output_port)
        form.addRow("Sync output", self.output_clock)
        self.connection_hint = QLabel(connection_help("keys", platform.system()))
        self.connection_hint.setWordWrap(True)
        form.addRow(self.connection_hint)

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
        for index in range(len(controller.app.project.tracks)):
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
        panic.clicked.connect(lambda: controller.app.engine.midi.release())
        ml.addWidget(panic)
        midi_scroll = QScrollArea()
        midi_scroll.setWidgetResizable(True)
        midi_scroll.setWidget(midi)
        self.tabs.addTab(midi_scroll, "MIDI controllers")

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

    def _profile_changed(self, *_args):
        profile = PROFILES[self.profile.currentData()]
        self.mode.setCurrentText(profile["mode"])
        self.base_note.setValue(profile["pad_base"])
        self.connection_hint.setText(connection_help(self.profile.currentData(), platform.system()))

    def _polyphony_changed(self, *_args):
        self.controller.app.engine.synth_polyphony = self.polyphony.currentData()
        self.controller.persist()

    def _output_changed(self, *_args):
        clock = self.output_clock.isChecked()
        if clock:
            for config in self.controller.router.settings.values():
                config["clock"] = False
            self.follow_clock.setChecked(False)
            self.controller.app.engine.midi.clock.source = None
        self.controller.output.select(self.output_port.currentData(), clock)
        self.controller.persist()

    def _extended_settings(self, *_args):
        key = self._selected_port()
        if key is None:
            return
        self.controller.app.engine.midi.release(key)
        settings = self.controller.router.settings.setdefault(key, {})
        if self.follow_clock.isChecked():
            for config in self.controller.router.settings.values():
                config["clock"] = False
            self.controller.output.clock_enabled = False
        settings.update(
            transpose=self.transpose.value(),
            channel=self.channel.currentData(),
            velocity_curve=self.velocity.currentData(),
            encoder=self.encoder.currentData(),
            soft_takeover=self.takeover.isChecked(),
            clock=self.follow_clock.isChecked(),
            transport=self.transport_enabled.isChecked(),
        )
        self.controller.app.engine.midi.clock.source = None
        self.controller.persist()

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
        for widget, field, default in (
            (self.transpose, "transpose", 0),
            (self.channel, "channel", -1),
            (self.velocity, "velocity_curve", "linear"),
            (self.encoder, "encoder", "absolute"),
            (self.takeover, "soft_takeover", False),
            (self.follow_clock, "clock", False),
            (self.transport_enabled, "transport", True),
        ):
            widget.blockSignals(True)
            widget.setEnabled(key is not None)
            value = settings.get(field, default)
            if isinstance(widget, QComboBox):
                widget.setCurrentIndex(max(0, widget.findData(value)))
            elif isinstance(widget, QSpinBox):
                widget.setValue(value)
            else:
                widget.setChecked(value)
            widget.blockSignals(False)
        for control in (self.enabled, self.mode, self.base_note):
            control.blockSignals(False)

    def _enabled(self, checked):
        key = self._selected_port()
        if key is not None:
            self.controller.service.enable(key, checked)
            if not checked:
                self.controller.app.engine.midi.release(key)
            self.controller.persist()

    def _mode_changed(self, *_args):
        key = self._selected_port()
        if key is not None:
            self.controller.app.engine.midi.release(key)
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
        track_count = len(controller.app.project.tracks)
        if self.learn_target.count() != 20 + track_count:
            selected_target = self.learn_target.currentData()
            self.learn_target.blockSignals(True)
            while self.learn_target.count() > 20:
                self.learn_target.removeItem(self.learn_target.count() - 1)
            for index in range(track_count):
                self.learn_target.addItem(f"Track {index + 1} volume", f"track:{index}")
            self.learn_target.setCurrentIndex(max(0, self.learn_target.findData(selected_target)))
            self.learn_target.blockSignals(False)
        output_ports = tuple((p.id, p.name) for p in controller.output.ports)
        if output_ports != getattr(self, "_output_ports", None):
            self._output_ports = output_ports
            self.output_port.blockSignals(True)
            self.output_port.clear()
            self.output_port.addItem("No MIDI output", "")
            for identifier, name in output_ports:
                self.output_port.addItem(name, identifier)
            if (
                controller.output.selected
                and self.output_port.findData(controller.output.selected) < 0
            ):
                self.output_port.addItem(
                    "Remembered output — disconnected", controller.output.selected
                )
            self.output_port.setCurrentIndex(
                max(0, self.output_port.findData(controller.output.selected))
            )
            self.output_port.blockSignals(False)
        self.output_clock.blockSignals(True)
        self.output_clock.setChecked(controller.output.clock_enabled)
        self.output_clock.blockSignals(False)
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
