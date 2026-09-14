"""Fresh export processes must restore the same project state as the desktop."""

from dataclasses import asdict
import json
import os
from pathlib import Path
import subprocess
import sys

import numpy as np
import pytest
import soundfile as sf

from mpclab.library import Library
from mpclab.model import Project


ROOT = Path(__file__).resolve().parents[1]
WORKER = """
import json
from pathlib import Path
import sys
import types

def no_device(*args, **kwargs):
    raise AssertionError('Export must not open a physical audio device')

sys.modules['sounddevice'] = types.SimpleNamespace(**{
    name: no_device for name in ('InputStream', 'OutputStream', 'Stream',
                                'RawInputStream', 'RawOutputStream', 'RawStream')
})

from mpclab import export as exporter
render = exporter.render_export

def observe(project, *args, **kwargs):
    assert not any(name.startswith('mpclab.ui') for name in sys.modules)
    Path(sys.argv[2]).write_text(json.dumps(project.to_dict()))
    return render(project, *args, **kwargs)

exporter.render_export = observe
from mpclab.export_worker import main
main(sys.argv[1])
"""


def run_worker(tmp_path, document, library, *, name):
    destination = tmp_path / f"{name}.wav"
    snapshot = tmp_path / f"{name}-loaded.json"
    specification = tmp_path / f"{name}.json"
    specification.write_text(
        json.dumps(
            {
                "project": document,
                "root": str(library.root),
                "rate": library.sr,
                "clips": {key: asdict(value) for key, value in library.clips.items()},
                "cached": {},
                "destination": str(destination),
                "options": {"mode": "pattern", "tail": 0},
            }
        )
    )
    result = subprocess.run(
        [sys.executable, "-c", WORKER, str(specification), str(snapshot)],
        cwd=ROOT,
        env=dict(
            os.environ,
            MPC_NATIVE_DSP="0",
            QT_QPA_PLATFORM="offscreen",
            OPENBLAS_NUM_THREADS="1",
            OMP_NUM_THREADS="1",
        ),
        capture_output=True,
        text=True,
        timeout=30,
        check=True,
    )
    return destination, snapshot, [json.loads(line) for line in result.stdout.splitlines()]


@pytest.fixture
def worker_project(tmp_path):
    library = Library(tmp_path / "library")
    audio = (np.sin(np.arange(4800) * 0.08) * 0.25).astype(np.float32)
    clip = library.add_audio(np.column_stack((audio, audio)), "Export state")
    project = Project()
    project.pads[0].sample_id = clip.id
    project.pads[0].end = clip.duration
    project.pattern().steps[0] = {0: 1.0}
    return project.to_dict(), library


def test_fresh_worker_preserves_sidecars_and_renders_muted_group(tmp_path, worker_project):
    document, library = worker_project
    baseline, _, messages = run_worker(tmp_path, document, library, name="baseline")
    assert messages[-1][0] == "succeeded"
    audio, rate = sf.read(baseline)
    assert rate == 48000 and np.max(np.abs(audio)) > 0.01

    document["workflow"] = {
        "groups": [
            {
                "id": "muted-group",
                "name": "Muted group",
                "members": [document["tracks"][0]["id"]],
                "gain": 1.0,
                "mute": True,
            }
        ]
    }
    document["pro_daw"] = {
        "plugin_chains": {
            "master": [
                {
                    "path": "/unavailable/Bypassed.vst3",
                    "plugin_name": "Bypassed",
                    "parameters": {},
                    "state": "",
                    "bypass": True,
                }
            ]
        }
    }
    destination, snapshot, messages = run_worker(tmp_path, document, library, name="muted")
    assert messages[-1][0] == "succeeded"
    loaded = json.loads(snapshot.read_text())
    assert loaded.get("workflow") == document["workflow"]
    assert loaded.get("pro_daw") == document["pro_daw"]
    audio, rate = sf.read(destination)
    assert rate == 48000 and len(audio) > 0
    assert np.max(np.abs(audio)) == 0, "Fresh export ignored the muted workflow group"


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("pro_daw", {"plugin_chains": {"master": [{"path": "invalid.wav"}]}}, "VST3"),
        ("workflow", {"unknown": []}, "workflow"),
        ("anharmonic_bundle", 1, "browser Project + audio bundle"),
    ],
)
def test_fresh_worker_rejects_incompatible_state(tmp_path, worker_project, field, value, message):
    document, library = worker_project
    document[field] = value
    destination, _, messages = run_worker(tmp_path, document, library, name="invalid")
    assert messages[-1][0] == "failed"
    assert message in messages[-1][1]
    assert not destination.exists()


def test_headless_install_allows_later_idempotent_desktop_hooks():
    script = """
import sys
import types
sys.modules['sounddevice'] = types.SimpleNamespace(OutputStream=object)
from mpclab import workflow_mixing
from mpclab.engine import Engine

workflow_mixing.install_advanced_track_controls(device_controllers=False)
headless_init = Engine.__init__
workflow_mixing.install_advanced_track_controls(device_controllers=False)
assert Engine.__init__ is headless_init
assert not any(name.startswith('mpclab.ui') for name in sys.modules)

from mpclab.ui.devices import DevicesController
original_loaded = DevicesController._plugin_loaded
workflow_mixing.install_advanced_track_controls()
desktop_loaded = DevicesController._plugin_loaded
assert desktop_loaded is not original_loaded
workflow_mixing.install_advanced_track_controls()
assert DevicesController._plugin_loaded is desktop_loaded
assert Engine.__init__ is headless_init
"""
    subprocess.run(
        [sys.executable, "-c", script],
        cwd=ROOT,
        env=dict(os.environ, MPC_NATIVE_DSP="0", QT_QPA_PLATFORM="offscreen"),
        capture_output=True,
        text=True,
        timeout=30,
        check=True,
    )
