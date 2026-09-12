from types import SimpleNamespace

import numpy as np

from mpclab.model import Clip
from mpclab.ui.playlist_edits import _resample_audio, fit_audio_to_bars


class _Signal:
    def __init__(self):
        self.count = 0

    def emit(self):
        self.count += 1


class _Status:
    def __init__(self):
        self.messages = []

    def showMessage(self, text, timeout=0):
        self.messages.append((text, timeout))


class _Library:
    def __init__(self, audio, sr=48000):
        self.sr = sr
        self._audio = {"source": np.asarray(audio, dtype=np.float32)}
        self.clips = {
            "source": SimpleNamespace(
                id="source",
                name="Loop 120 BPM",
                duration=len(audio) / sr,
                sample_rate=sr,
            )
        }
        self._next = 0

    def audio(self, ref):
        return self._audio.get(ref)

    def add_audio(self, data, name, kind="render", parent=None):
        self._next += 1
        ref = f"render{self._next}"
        self._audio[ref] = np.asarray(data, dtype=np.float32)
        meta = SimpleNamespace(
            id=ref,
            name=name,
            kind=kind,
            parent=parent,
            duration=len(data) / self.sr,
            sample_rate=self.sr,
        )
        self.clips[ref] = meta
        return meta


class _Engine:
    def __init__(self):
        self.preloads = 0

    def preload_project_audio(self, project):
        self.preloads += 1


class _Owner:
    def __init__(self, audio, bpm=120.0, sr=48000):
        self.changed = _Signal()
        self.refreshes = 0
        self.snapshots = 0
        self.app = SimpleNamespace(
            project=SimpleNamespace(bpm=bpm),
            library=_Library(audio, sr),
            engine=_Engine(),
            status=_Status(),
        )

    def refresh(self):
        self.refreshes += 1


    def _snapshot(self):
        self.snapshots += 1


def _attach_snapshot(owner):
    owner.app.snapshot = owner._snapshot
    return owner


def test_resample_audio_returns_exact_stereo_length():
    source = np.linspace(-1.0, 1.0, 11, dtype=np.float32)
    result = _resample_audio(source, 37)
    assert result.shape == (37, 2)
    assert result.dtype == np.float32
    assert np.isclose(result[0, 0], -1.0)
    assert np.isclose(result[-1, 1], 1.0)


def test_fit_audio_to_four_bars_renders_real_audio_at_project_tempo():
    sr = 1000
    source = np.column_stack(
        (
            np.linspace(-1.0, 1.0, 3000, dtype=np.float32),
            np.linspace(1.0, -1.0, 3000, dtype=np.float32),
        )
    )
    owner = _attach_snapshot(_Owner(source, bpm=120.0, sr=sr))
    clip = Clip(
        kind="audio",
        ref="source",
        start_beat=4.0,
        length_beats=6.0,
        offset=0.5,
        source_length=2.0,
        loop=True,
        reverse=True,
    )

    result = fit_audio_to_bars(owner, clip, 4)

    assert result is clip
    assert clip.ref == "render1"
    assert clip.length_beats == 16.0
    assert clip.offset == 0.0
    assert clip.source_length == 8.0
    assert clip.loop is False
    assert clip.reverse is False
    assert owner.app.library.clips[clip.ref].parent == "source"
    assert owner.app.library._audio[clip.ref].shape == (8000, 2)
    assert owner.snapshots == 1
    assert owner.changed.count == 1
    assert owner.refreshes == 1
    assert owner.app.engine.preloads == 1
    assert "4 bars" in owner.app.status.messages[-1][0]
    assert "Resample" in owner.app.status.messages[-1][0]
