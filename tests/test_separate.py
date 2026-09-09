"""Stem-engine contracts and its current-window integration."""

from __future__ import annotations

import tempfile
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pytest
import soundfile as sf
from PySide6.QtWidgets import QApplication

from mpclab import separate
from mpclab.audio_kernel import AUDIO_SAMPLE_RATE
from mpclab.engine import Engine
from mpclab.ui.main_window import MainWindow


def test_models_have_stable_labels_and_stem_lists():
    assert separate.MODELS["htdemucs"]["stems"] == ("drums", "bass", "other", "vocals")
    assert separate.MODELS["htdemucs_6s"]["stems"][-2:] == ("guitar", "piano")
    assert all(info["label"] and info["detail"] for info in separate.MODELS.values())


def test_separator_rejects_bad_requests_before_starting_a_thread(tmp_path):
    source = tmp_path / "song.wav"
    source.touch()
    engine = separate.Separator(tmp_path / "jobs")
    with pytest.raises(ValueError):
        engine.start(source, model="not-a-model")
    with pytest.raises(ValueError):
        engine.start(source, two_stems="guitar")
    with pytest.raises(FileNotFoundError):
        engine.start(tmp_path / "missing.wav")
    assert engine.jobs == {}


def test_cancel_marks_a_queued_job_without_touching_other_processes(tmp_path):
    engine = separate.Separator(tmp_path / "jobs")
    job = separate.Job("job1", "song", "htdemucs")
    engine.jobs[job.id] = job
    assert engine.cancel(job.id)
    assert job.state == "cancelled"
    assert not engine.cancel(job.id)


def test_stem_drawer_and_library_adoption():
    app = QApplication.instance() or QApplication([])
    with tempfile.TemporaryDirectory() as folder:
        root = Path(folder)
        with (
            patch.object(Engine, "start", lambda _engine: None),
            patch.object(separate, "available", return_value=True),
        ):
            window = MainWindow(root)
        window.show()
        app.processEvents()

        assert not window.browser.sep_toggle.isChecked()
        assert not window.browser.sep_panel.isVisible()
        window.browser.sep_toggle.click()
        assert window.browser.sep_toggle.isChecked()
        assert window.browser.sep_panel.isVisible()
        assert window.browser.sep_btn.isEnabled()

        job = separate.Job("finished", "My Song", "htdemucs", state="done")
        job.source_clip = "source-clip"
        out = window.separator.out_root / job.id
        out.mkdir(parents=True)
        audio = np.zeros((480, 2), dtype=np.float32)
        for stem in ("vocals", "other", "bass", "drums"):
            path = out / f"{stem}.wav"
            sf.write(path, audio, AUDIO_SAMPLE_RATE)
            job.stems[stem] = path.name

        ids = window.adopt_stems(job)
        clips = [window.library.clips[clip_id] for clip_id in ids]
        assert [clip.stem for clip in clips] == ["drums", "bass", "other", "vocals"]
        assert all(clip.kind == "stem" for clip in clips)
        assert all(clip.parent == "source-clip" for clip in clips)
        assert not out.exists()

        window.close()
