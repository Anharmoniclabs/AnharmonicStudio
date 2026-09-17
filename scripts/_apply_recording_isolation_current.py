from pathlib import Path


def replace_once(path: str, old: str, new: str, label: str) -> None:
    target = Path(path)
    text = target.read_text()
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{label}: expected one match, found {count}")
    target.write_text(text.replace(old, new, 1))


# Dedicated vocal capture owns exactly one explicitly selected physical input.
replace_once(
    "mpclab/ui/vocal_recording.py",
    '''    rec.input_device = str(owner.input_box.currentData() or "")\n    rec.input_gain_db = owner.input_gain.value()\n''',
    '''    rec.input_device = str(owner.input_box.currentData() or "")\n    if hasattr(owner, "input_channel"):\n        rec.input_channels = [int(owner.input_channel.currentData() or 0)]\n    rec.input_gain_db = owner.input_gain.value()\n''',
    "persist vocal input channel",
)

replace_once(
    "mpclab/ui/vocal_recording.py",
    '''\ndef scan_inputs(owner):\n''',
    '''\ndef _populate_input_channels(owner):\n    if not hasattr(owner, "input_channel"):\n        return\n    rec = owner.app.project.vocal_record\n    key = str(owner.input_box.currentData() or "")\n    selected = next((item for item in owner._inputs if item["key"] == key), None)\n    if selected is None:\n        try:\n            _inputs, default = input_device_inventory()\n        except Exception:\n            default = None\n        selected = next((item for item in owner._inputs if item["index"] == default), None)\n    channels = max(1, int(selected.get("channels", 1))) if selected is not None else 1\n    wanted = rec.input_channels[0] if rec.input_channels else 0\n    owner.input_channel.blockSignals(True)\n    owner.input_channel.clear()\n    for channel in range(channels):\n        owner.input_channel.addItem(f"Channel {channel + 1}", channel)\n    index = owner.input_channel.findData(wanted)\n    owner.input_channel.setCurrentIndex(index if index >= 0 else 0)\n    owner.input_channel.blockSignals(False)\n\n\ndef input_device_changed(owner, *_):\n    if owner._syncing:\n        return\n    _populate_input_channels(owner)\n    _record_settings_changed(owner)\n\n\ndef scan_inputs(owner):\n''',
    "vocal channel population helpers",
)

replace_once(
    "mpclab/ui/vocal_recording.py",
    '''    owner.input_box.setCurrentIndex(max(0, index))\n    owner.input_box.blockSignals(False)\n    owner.record_status.setText(f"{len(inputs)} microphone input(s) available")\n''',
    '''    owner.input_box.setCurrentIndex(max(0, index))\n    owner.input_box.blockSignals(False)\n    _populate_input_channels(owner)\n    owner.record_status.setText(f"{len(inputs)} microphone input(s) available")\n''',
    "refresh vocal channels after device scan",
)

replace_once(
    "mpclab/ui/vocal_recording.py",
    '''            input_channels=tuple(rec.input_channels[:2]),\n''',
    '''            input_channels=(int(rec.input_channels[0]) if rec.input_channels else 0,),\n''',
    "force dedicated vocal mono channel",
)

replace_once(
    "mpclab/ui/vocal_layout.py",
    '''    owner.input_box = QComboBox()\n    owner.input_box.addItem("System default input", "")\n    owner.input_box.setMinimumWidth(260)\n    owner.input_box.currentIndexChanged.connect(owner._record_settings_changed)\n    grid.addWidget(owner.input_box, 0, 1, 1, 3)\n''',
    '''    owner.input_box = QComboBox()\n    owner.input_box.addItem("System default input", "")\n    owner.input_box.setMinimumWidth(220)\n    owner.input_box.currentIndexChanged.connect(owner._input_device_changed)\n    grid.addWidget(owner.input_box, 0, 1, 1, 2)\n    owner.input_channel = QComboBox()\n    owner.input_channel.addItem("Channel 1", 0)\n    owner.input_channel.setAccessibleName("Vocal input channel")\n    owner.input_channel.setToolTip(\n        "Choose the physical interface input where your microphone is connected."\n    )\n    owner.input_channel.currentIndexChanged.connect(owner._record_settings_changed)\n    grid.addWidget(owner.input_channel, 0, 3)\n''',
    "vocal channel selector UI",
)

replace_once(
    "mpclab/ui/vocals.py",
    '''    def scan_inputs(self):\n        return vocal_recording.scan_inputs(self)\n\n    def toggle_recording(self):\n''',
    '''    def scan_inputs(self):\n        return vocal_recording.scan_inputs(self)\n\n    def _input_device_changed(self, *args):\n        return vocal_recording.input_device_changed(self, *args)\n\n    def toggle_recording(self):\n''',
    "vocal device-change wrapper",
)

# Song audio-input recording is dry and must never instantiate the vocal monitor/tuner.
replace_once(
    "mpclab/ui/track_recording.py",
    '''        self.project = self.app.project\n        self.settings = replace(self.project.vocal_record)\n        self.start_beat = float(self.app.engine.beat)\n''',
    '''        self.project = self.app.project\n        self.settings = replace(\n            self.project.vocal_record, monitor=False, corrected_monitor=False\n        )\n        self.start_beat = float(self.app.engine.beat)\n''',
    "dry song capture settings snapshot",
)

replace_once(
    "mpclab/ui/track_recording.py",
    '''                self.recorder.sample_rate = app.engine.sr\n                self.recorder.blocksize = app.engine.blocksize\n                from ..autotune.live import LiveMonitor, MonitorRoute\n\n                route = MonitorRoute(self.recorder, app.engine, self.settings.monitor_gain)\n                self.session.start(\n                    device=device,\n                    gain_db=self.settings.input_gain_db,\n                    input_channels=self.settings.input_channels,\n                    monitor_callback=route if self.settings.monitor else None,\n                )\n                if self.settings.monitor and self.settings.corrected_monitor:\n                    try:\n                        self._live_monitor = LiveMonitor(\n                            self.project.vocal, self.recorder.sample_rate, route\n                        )\n                        self.recorder.monitor_callback = self._live_monitor.push\n                    except Exception as exc:\n                        self._cue_error = f"Corrected cue unavailable; monitoring dry · {exc}"\n                self._capture_sample_rate = self.recorder.sample_rate\n''',
    '''                self.recorder.sample_rate = app.engine.sr\n                self.recorder.blocksize = app.engine.blocksize\n                self.session.start(\n                    device=device,\n                    gain_db=self.settings.input_gain_db,\n                    input_channels=self.settings.input_channels,\n                    monitor_callback=None,\n                )\n                self._capture_sample_rate = self.recorder.sample_rate\n''',
    "remove vocal monitor from song capture",
)

# Regression: Song input must remain dry even if Vocal monitoring is enabled globally.
replace_once(
    "tests/test_track_recording.py",
    '''\n\n@pytest.mark.parametrize("source", ["notes", "sampler"])\ndef test_synth_and_sample_notes_keep_off_grid_timing_in_song(window, source):\n''',
    '''\n\ndef test_song_audio_capture_does_not_inherit_vocal_monitor(window, monkeypatch):\n    _data, calls = mock_audio(window, monkeypatch)\n    window.project.vocal_record.monitor = True\n    window.project.vocal_record.corrected_monitor = True\n    arm(window)\n    window.btn_rec.click()\n    window.engine._process_commands()\n    assert window.track_capture.settings.monitor is False\n    assert window.track_capture.settings.corrected_monitor is False\n    assert calls == [(None, 0.0, None)]\n    window.stop_all()\n\n\n@pytest.mark.parametrize("source", ["notes", "sampler"])\ndef test_synth_and_sample_notes_keep_off_grid_timing_in_song(window, source):\n''',
    "song dry capture regression",
)

# Regression: Vocal device/channel state is device-specific and capture is mono.
panel_test = Path("tests/test_vocal_panel.py")
panel_text = panel_test.read_text()
marker = "def test_vocal_input_channel_selector_owns_one_physical_channel"
if marker not in panel_text:
    panel_text += '''\n\ndef test_vocal_input_channel_selector_owns_one_physical_channel(panel, monkeypatch):\n    from mpclab.ui import vocal_recording\n\n    devices = [\n        {\n            "index": 3,\n            "key": "test/interface",\n            "name": "Test Interface",\n            "label": "Test Interface · 4 in",\n            "channels": 4,\n        }\n    ]\n    monkeypatch.setattr(vocal_recording, "input_device_inventory", lambda: (devices, 3))\n    panel.app.project.vocal_record.input_device = "test/interface"\n    panel.app.project.vocal_record.input_channels = [2]\n    panel.scan_inputs()\n\n    assert panel.input_channel.count() == 4\n    assert panel.input_channel.currentData() == 2\n    panel.input_channel.setCurrentIndex(3)\n    assert panel.app.project.vocal_record.input_channels == [3]\n\n    starts = []\n    monkeypatch.setattr(panel.capture_session, "arm", lambda *_args: None)\n    monkeypatch.setattr(panel.capture_session, "start", lambda **kwargs: starts.append(kwargs))\n    panel._start_capture()\n    assert starts and starts[0]["input_channels"] == (3,)\n'''
    panel_test.write_text(panel_text)
