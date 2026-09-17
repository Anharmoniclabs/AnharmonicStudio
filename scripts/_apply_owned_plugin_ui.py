from pathlib import Path


def replace_once(path: str, old: str, new: str, label: str) -> None:
    target = Path(path)
    text = target.read_text()
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{label}: expected one match, found {count}")
    target.write_text(text.replace(old, new, 1))


replace_once(
    "mpclab/ui/devices.py",
    '''    def load_plugin(self, slot, specification, *, save=True):
        if slot not in self._slot_generation or self._closed:
            return
        self._generation += 1
        generation = self._generation
        self._slot_generation[slot] = generation
        specification = copy.deepcopy(specification)
        self._pending_loads[slot] = (generation, specification, save)
        self._continue_load()
''',
    '''    def _plugin_key(self, slot, instrument_id=None):
        if slot == "effect":
            return "effect"
        if slot != "instrument":
            raise ValueError("plugin slot must be instrument or effect")
        return "instrument" if instrument_id is None else f"instrument:{instrument_id}"

    def load_plugin(self, slot, specification, *, save=True, instrument_id=None):
        if self._closed:
            return
        if slot == "instrument" and instrument_id is not None:
            if instrument_id not in {item.id for item in self.app.project.instruments}:
                raise ValueError("hosted plugin target instrument does not exist")
        key = self._plugin_key(slot, instrument_id)
        self._generation += 1
        generation = self._generation
        self._slot_generation[key] = generation
        specification = copy.deepcopy(specification)
        self._pending_loads[key] = (generation, slot, instrument_id, specification, save)
        self._continue_load()
''',
    "owned load entry",
)

replace_once(
    "mpclab/ui/devices.py",
    '''        slot = next(iter(self._pending_loads))
        generation, specification, save = self._pending_loads.pop(slot)
        self._loading = True
''',
    '''        key = next(iter(self._pending_loads))
        generation, slot, instrument_id, specification, save = self._pending_loads.pop(key)
        self._loading = True
''',
    "owned pending pop",
)

replace_once(
    "mpclab/ui/devices.py",
    '''                    target.plugin_finished.emit(
                        generation, slot, bridge, (specification, save), error
                    )
''',
    '''                    target.plugin_finished.emit(
                        generation,
                        slot,
                        bridge,
                        (specification, save, instrument_id, key),
                        error,
                    )
''',
    "owned signal payload",
)

# Replace the whole completion/removal/sync block so all state transitions use
# the same stable target key and old-project legacy behavior remains unchanged.
path = Path("mpclab/ui/devices.py")
text = path.read_text()
start = text.index("    def _plugin_loaded(self, generation, slot, bridge, saved, error):\n")
end = text.index("\n    def shutdown(self):\n", start)
new_block = '''    def _plugin_loaded(self, generation, slot, bridge, saved, error):
        self._loading = False
        specification, save, instrument_id, key = saved
        if generation != self._slot_generation.get(key) or self._closed:
            if bridge is not None:
                bridge.close()
            self._continue_load()
            return
        if error:
            if not save:
                unavailable = UnavailablePlugin(error)
                if slot == "instrument" and instrument_id is not None:
                    old = self.app.engine.external.set_instrument(instrument_id, unavailable)
                    if old is not None:
                        old.close()
                else:
                    setattr(self.app.engine.external, slot, unavailable)
            self.plugin_status = "Plugin unavailable · " + error
            registry_key = next(
                (
                    p.id
                    for p in self.registry.candidates.values()
                    if p.path == specification["path"]
                ),
                None,
            )
            if registry_key:
                self.registry.quarantine(registry_key, error)
                try:
                    self.registry.save()
                except OSError:
                    pass
            self.changed.emit()
            self._continue_load()
            return
        if save:
            self.app.snapshot()
            if slot == "instrument" and instrument_id is not None:
                self.app.project.instrument_plugins[instrument_id] = specification
            else:
                self.app.project.plugins[slot] = specification
            self.app._set_dirty(True)
        if slot == "instrument" and instrument_id is not None:
            old = self.app.engine.external.set_instrument(instrument_id, bridge)
        else:
            old = getattr(self.app.engine.external, slot)
            setattr(self.app.engine.external, slot, bridge)
        if old is not None:
            old.close()
        if slot == "instrument":
            self.app.panic_synth()
            self.app.piano_roll.select_channel(instrument_id)
        target = ""
        if instrument_id is not None:
            instrument = self.app.project.instrument(instrument_id)
            target = f" · {instrument.name}"
        self.plugin_status = (
            f"{bridge.info['name']} ready{target} · monitoring adds "
            f"{2 * bridge.blocksize / self.app.engine.sr * 1000:.1f} ms"
        )
        self.changed.emit()
        self._continue_load()

    def remove_plugin(self, slot, instrument_id=None):
        key = self._plugin_key(slot, instrument_id)
        self._generation += 1
        self._slot_generation[key] = self._generation
        self._pending_loads.pop(key, None)
        self.app.snapshot()
        if slot == "instrument" and instrument_id is not None:
            self.app.project.instrument_plugins.pop(instrument_id, None)
            old = self.app.engine.external.remove_instrument(instrument_id)
        else:
            self.app.project.plugins.pop(slot, None)
            old = getattr(self.app.engine.external, slot)
            setattr(self.app.engine.external, slot, None)
        self.app._set_dirty(True)
        if old is not None:
            old.close()
        self.app.engine.cmds.put(("synthpanic",))
        self.plugin_status = "Plugin removed"
        self.changed.emit()

    def sync_project(self):
        self._generation += 1
        self._slot_generation = dict.fromkeys(self._slot_generation, self._generation)
        self._pending_loads.clear()
        self.app.engine.midi.release()
        self.app.engine.external.close()
        self.app.engine.cmds.put(("synthpanic",))
        for slot, spec in self.app.project.plugins.items():
            if not spec.get("bypass"):
                setattr(self.app.engine.external, slot, UnavailablePlugin("Loading plugin…"))
                self.load_plugin(slot, spec, save=False)
        for instrument_id, spec in self.app.project.instrument_plugins.items():
            if spec.get("bypass"):
                continue
            self.app.engine.external.set_instrument(
                instrument_id, UnavailablePlugin("Loading plugin…")
            )
            self.load_plugin(
                "instrument", spec, save=False, instrument_id=instrument_id
            )
'''
path.write_text(text[:start] + new_block + text[end:])

replace_once(
    "mpclab/ui/devices.py",
    '''        description = QLabel(
            "Load a native VST3 instrument or master effect. Settings save with the song.\\nOther formats are listed with their compatibility status."
        )
        description.setWordWrap(True)
        pl.addWidget(description)
''',
    '''        description = QLabel(
            "Load native VST3 instruments into independent project instruments or use the legacy synth slot. "
            "Master effects remain global. Settings save with the song.\\nOther formats are listed with their compatibility status."
        )
        description.setWordWrap(True)
        pl.addWidget(description)
        target_row = QHBoxLayout()
        target_row.addWidget(QLabel("Instrument target"))
        self.instrument_target = QComboBox()
        self.instrument_target.currentIndexChanged.connect(self._refresh_parameters)
        target_row.addWidget(self.instrument_target, 1)
        pl.addLayout(target_row)
''',
    "instrument target ui",
)

replace_once(
    "mpclab/ui/devices.py",
    '''        remove = QPushButton("Remove plugin")
        remove.clicked.connect(lambda: controller.remove_plugin(self.slot.currentText()))
''',
    '''        remove = QPushButton("Remove plugin")
        remove.clicked.connect(
            lambda: controller.remove_plugin(
                self.slot.currentText(),
                self.instrument_target.currentData()
                if self.slot.currentText() == "instrument"
                else None,
            )
        )
''',
    "targeted remove ui",
)

replace_once(
    "mpclab/ui/devices.py",
    '''        self.controller.registry.restore(candidate.id)
        self.slot.setCurrentText(slot)
        self.controller.load_plugin(slot, {"path": candidate.path})
''',
    '''        self.controller.registry.restore(candidate.id)
        self.slot.setCurrentText(slot)
        instrument_id = self.instrument_target.currentData() if slot == "instrument" else None
        self.controller.load_plugin(
            slot, {"path": candidate.path}, instrument_id=instrument_id
        )
''',
    "targeted load ui",
)

replace_once(
    "mpclab/ui/devices.py",
    '''    def _refresh_parameters(self, *_args):
        slot = self.slot.currentText()
        bridge = getattr(self.controller.app.engine.external, slot)
        key = (slot, id(bridge))
''',
    '''    def _refresh_parameters(self, *_args):
        slot = self.slot.currentText()
        instrument_id = self.instrument_target.currentData() if slot == "instrument" else None
        bridge = (
            self.controller.app.engine.external.instrument_for(instrument_id)
            if slot == "instrument"
            else self.controller.app.engine.external.effect
        )
        key = (slot, instrument_id, id(bridge))
''',
    "target parameter page",
)

replace_once(
    "mpclab/ui/devices.py",
    '''    def _apply_parameters(self):
        slot = self.slot.currentText()
        spec = copy.deepcopy(self.controller.app.project.plugins.get(slot))
        if spec is not None:
            spec["parameters"] = {name: box.value() for name, box in self.parameters.items()}
            self.controller.load_plugin(slot, spec)
''',
    '''    def _apply_parameters(self):
        slot = self.slot.currentText()
        instrument_id = self.instrument_target.currentData() if slot == "instrument" else None
        if slot == "instrument" and instrument_id is not None:
            spec = copy.deepcopy(self.controller.app.project.instrument_plugins.get(instrument_id))
        else:
            spec = copy.deepcopy(self.controller.app.project.plugins.get(slot))
        if spec is not None:
            spec["parameters"] = {name: box.value() for name, box in self.parameters.items()}
            self.controller.load_plugin(slot, spec, instrument_id=instrument_id)
''',
    "target parameter apply",
)

# Refresh the target selector whenever project instruments change.
replace_once(
    "mpclab/ui/devices.py",
    '''    def refresh(self):
        controller = self.controller
        track_count = len(controller.app.project.tracks)
''',
    '''    def refresh(self):
        controller = self.controller
        targets = [(None, "Legacy synth slot")] + [
            (instrument.id, instrument.name) for instrument in controller.app.project.instruments
        ]
        if targets != getattr(self, "_instrument_targets", None):
            selected = self.instrument_target.currentData()
            self._instrument_targets = targets
            self.instrument_target.blockSignals(True)
            self.instrument_target.clear()
            for instrument_id, name in targets:
                self.instrument_target.addItem(name, instrument_id)
            index = self.instrument_target.findData(selected)
            self.instrument_target.setCurrentIndex(max(0, index))
            self.instrument_target.blockSignals(False)
            self._parameter_key = None
        self.instrument_target.setEnabled(self.slot.currentText() == "instrument")
        track_count = len(controller.app.project.tracks)
''',
    "refresh instrument targets",
)

# Make runtime error status aware of independently owned instrument bridges.
replace_once(
    "mpclab/ui/devices.py",
    '''            for slot in ("instrument", "effect"):
                bridge = getattr(self.app.engine.external, slot)
                if bridge is not None and bridge.error:
                    state = "silenced" if slot == "instrument" else "bypassed"
                    self.plugin_status = f"{slot.title()} {state} · {bridge.error}"
            self.changed.emit()
''',
    '''            for slot in ("instrument", "effect"):
                bridge = getattr(self.app.engine.external, slot)
                if bridge is not None and bridge.error:
                    state = "silenced" if slot == "instrument" else "bypassed"
                    self.plugin_status = f"{slot.title()} {state} · {bridge.error}"
            for instrument_id in self.app.engine.external.active_instrument_ids():
                bridge = self.app.engine.external.instrument_for(instrument_id)
                if bridge is not None and bridge.error:
                    instrument = self.app.project.instrument(instrument_id)
                    self.plugin_status = f"{instrument.name} silenced · {bridge.error}"
            self.changed.emit()
''',
    "owned plugin error status",
)

# Adapt PDC hooks to the extended loader signatures/targets.
path = Path("mpclab/workflow_plugin_pdc.py")
text = path.read_text()
start = text.index("    def refresh(controller, slot: str | None = None) -> int:\n")
end = text.index("\n    DevicesController._plugin_loaded = plugin_loaded\n", start)
new_hooks = '''    def refresh(controller, slot: str | None = None, instrument_id=None) -> int:
        engine = controller.app.engine
        prepare = getattr(engine, "prepare_plugin_latency", None)
        delay = prepare() if prepare is not None else 0
        if slot is not None:
            plugin = (
                engine.external.instrument_for(instrument_id)
                if slot == "instrument"
                else getattr(engine.external, slot, None)
            )
            if plugin is not None and not getattr(plugin, "error", ""):
                samples = plugin_path_latency_samples(plugin, include_live_bridge=True)
                ms = samples / engine.sr * 1000.0
                if slot == "instrument":
                    label = plugin.info.get("name", "Instrument")
                    if instrument_id is not None:
                        label += f" · {controller.app.project.instrument(instrument_id).name}"
                    controller.plugin_status = (
                        f"{label} ready · {ms:.1f} ms isolated path · "
                        f"PDC {delay / engine.sr * 1000.0:.1f} ms"
                    )
                else:
                    controller.plugin_status = (
                        f"{plugin.info.get('name', 'Effect')} ready · {ms:.1f} ms master path"
                    )
        return delay

    def plugin_loaded(controller, generation, slot, bridge, saved, error):
        result = original_loaded(controller, generation, slot, bridge, saved, error)
        instrument_id = saved[2] if len(saved) > 2 else None
        active = (
            controller.app.engine.external.instrument_for(instrument_id)
            if slot == "instrument"
            else getattr(controller.app.engine.external, slot, None)
        )
        if not error and active is bridge:
            refresh(controller, slot, instrument_id)
            controller.changed.emit()
        return result

    def remove_plugin(controller, slot, instrument_id=None):
        result = original_remove(controller, slot, instrument_id)
        refresh(controller)
        return result

    def sync_project(controller):
        result = original_sync(controller)
        refresh(controller)
        return result
'''
path.write_text(text[:start] + new_hooks + text[end:])

# Add independent-loader regressions to the existing Qt controller suite.
path = Path("tests/test_devices_ui.py")
text = path.read_text()
text += '''\n\ndef test_owned_instrument_plugins_load_and_remove_independently(window, monkeypatch):\n    class Plugin:\n        def __init__(self, specification, rate):\n            self.info = {\n                "name": specification["path"],\n                "instrument": True,\n                "state": "",\n            }\n            self.closed = False\n\n        def render(self, *args, **kwargs):\n            return np.zeros((128, 2), np.float32)\n\n        def close(self):\n            self.closed = True\n\n    monkeypatch.setattr(devices, "IsolatedPlugin", Plugin)\n    monkeypatch.setattr(\n        devices,\n        "LivePlugin",\n        lambda p, n: SimpleNamespace(\n            info=p.info, blocksize=n, error="", close=p.close\n        ),\n    )\n    first = window.project.add_instrument("First", window.project.synth)\n    second = window.project.add_instrument("Second", window.project.synth)\n    controller = window.devices\n    controller.load_plugin(\n        "instrument", {"path": "first.vst3"}, instrument_id=first.id\n    )\n    controller.load_plugin(\n        "instrument", {"path": "second.vst3"}, instrument_id=second.id\n    )\n    until(lambda: not controller._loading and not controller._pending_loads)\n\n    assert window.engine.external.instrument_for(first.id).info["name"] == "first.vst3"\n    assert window.engine.external.instrument_for(second.id).info["name"] == "second.vst3"\n    assert set(window.project.instrument_plugins) == {first.id, second.id}\n\n    controller.remove_plugin("instrument", first.id)\n    assert window.engine.external.instrument_for(first.id) is None\n    assert window.engine.external.instrument_for(second.id) is not None\n    assert set(window.project.instrument_plugins) == {second.id}\n\n\ndef test_devices_dialog_exposes_stable_instrument_plugin_targets(window):\n    owned = window.project.add_instrument("External Rack", window.project.synth)\n    dialog = devices.DevicesDialog(window.devices, window)\n    dialog.refresh()\n    assert dialog.instrument_target.findData(None) >= 0\n    assert dialog.instrument_target.itemText(dialog.instrument_target.findData(owned.id)) == "External Rack"\n    dialog.close()\n'''
path.write_text(text)
