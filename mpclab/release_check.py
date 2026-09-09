"""Exercise an installed application's resources, UI and export without hardware."""

import gc
import json
import os
from pathlib import Path
import tempfile
from unittest.mock import patch
import weakref


def plugin_runtime_probe(connection, specification, sample_rate):
    """Exercise the shipped host libraries and spawned audio pipe without hardware."""
    import numpy as np
    import pedalboard_native
    import rtmidi
    from .plugin_host import receive_packet, send_packet

    try:
        gain = pedalboard_native.Gain(gain_db=-6.020599913)
        send_packet(connection, {"effect": True, "midi_apis": rtmidi.get_compiled_api()})
        request, raw = receive_packet(connection)
        audio = np.frombuffer(raw, dtype="<f4").reshape(request["frames"], 2)
        output = gain.process(audio.T.copy(), sample_rate)
        send_packet(connection, {"frames": len(audio)}, output.T)
    finally:
        connection.close()


def main():
    # This command creates only offscreen Qt objects in its own process.
    os.environ["QT_QPA_PLATFORM"] = "offscreen"
    import numpy as np
    import soundfile as sf
    from PySide6.QtCore import QCoreApplication, QEvent, QSettings
    from PySide6.QtWidgets import QApplication
    from shiboken6 import isValid

    from .dsp import probe, to_wav
    from .engine import Engine
    from .export import ExportJob
    from .model import Project
    from .native_dsp import NATIVE, STATUS
    from .runtime_paths import RESOURCE_ROOT
    from .plugin_host import IsolatedPlugin
    from .ui import main_window

    class IsolatedSettings:
        IniFormat = QSettings.IniFormat
        UserScope = QSettings.UserScope

        def __init__(self, *args):
            self.values = {"audio/setup_complete": True}

        def value(self, name, default=None):
            return self.values.get(name, default)

        def setValue(self, name, value):
            self.values[name] = value

    if NATIVE is None:
        raise RuntimeError(f"Native audio helper did not load: {STATUS}")
    for asset in (
        "branding/anharmonic-studios.svg",
        "branding/anharmonic-header.svg",
        "orchestra/manifest.json",
        "ui/check-dark.svg",
    ):
        if not (RESOURCE_ROOT / "assets" / asset).is_file():
            raise RuntimeError(f"Missing bundled resource: {asset}")

    app = QApplication.instance() or QApplication([])
    result = {"native_dsp": STATUS, "qt_platform": app.platformName(), "audio_devices_opened": 0}
    host = IsolatedPlugin({}, worker=plugin_runtime_probe)
    try:
        output = host.render(np.ones((128, 2), np.float32), 128)
        if not np.allclose(output, 0.5, atol=1e-5):
            raise RuntimeError("Isolated plugin runtime produced invalid audio")
        result["isolated_plugin_runtime"] = True
        result["midi_apis"] = host.info["midi_apis"]
    finally:
        host.close()
    with tempfile.TemporaryDirectory(prefix="anharmonic-self-check-") as directory:
        root = Path(directory)
        with (
            patch.object(Engine, "start", lambda self: None),
            patch.object(main_window, "QSettings", IsolatedSettings),
        ):
            window = main_window.MainWindow(root, restore_session=False)
            engine_ref = weakref.ref(window.engine)
            wave = (np.sin(np.arange(4800) * 0.08) * 0.2).astype(np.float32)
            clip = window.library.add_audio(np.column_stack((wave, wave)), "Release check")
            window.project.pads[0].sample_id = clip.id
            window.project.pads[0].end = clip.duration
            window.project.pattern().steps[0] = {0: 1.0, 8: 0.7}
            saved = root / "projects/check.json"
            window.project.save(saved)
            project = Project.load(saved)
            if project.pads[0].sample_id != clip.id:
                raise RuntimeError("Project save/load lost its sample")
            destination = root / "exports/check.wav"
            job = ExportJob(project, window.library, destination, mode="pattern", tail=0)
            failures, successes = [], []
            job.failed.connect(failures.append)
            job.succeeded.connect(lambda *args: successes.append(args))
            job._run()  # Real export subprocess, including the frozen executable path.
            if failures or not successes:
                raise RuntimeError(f"Export failed: {failures}")
            audio, rate = sf.read(destination)
            if rate != 48000 or not np.isfinite(audio).all() or np.max(np.abs(audio)) < 0.01:
                raise RuntimeError("Exported audio is silent, invalid or at the wrong rate")
            converted = root / "converted.wav"
            to_wav(destination, converted, sr=44100)
            if probe(converted)["sample_rate"] != 44100:
                raise RuntimeError("FFmpeg import/resampling failed")
            window.resize(1280, 800)
            window.show()
            app.processEvents()
            if window.grab().isNull():
                raise RuntimeError("Qt failed to render the workstation")
            window._dirty = False
            if not window.close():
                raise RuntimeError("Disposable workstation refused to close")
            QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
            app.processEvents()
            QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
            if isValid(window):
                raise RuntimeError("Closed Qt window was retained")
            del window
            gc.collect()
            if engine_ref() is not None:
                raise RuntimeError("Closed workstation retained its audio engine")
            result.update(
                project_roundtrip=True, subprocess_export=True, ffmpeg=True, ui_lifetime=True
            )
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
