from types import SimpleNamespace

import numpy as np

from mpclab.model import Clip
from mpclab.ui.audio_clip_actions import (
    _resample_exact,
    make_audio_unique,
    match_audio_to_project_tempo,
    normalize_clip_gain,
    source_bpm,
    use_full_source,
)
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
    def __init__(self, audio, sr=48000, name="Loop 120 BPM", bpm=None):
        self.sr = sr
        self._audio = {"source": np.asarray(audio, dtype=np.float32)}
        self.clips = {
            "source": SimpleNamespace(
                id="source",
                name=name,
                duration=len(audio) / sr,
                sample_rate=sr,
                bpm=bpm,
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
            bpm=None,
        )
        self.clips[ref] = meta
        return meta


class _Engine:
    def __init__(self):
        self.preloads = 0

    def preload_project_audio(self, project):
        self.preloads += 1


class _Owner:
    def __init__(self, audio, bpm=120.0, sr=48000, name="Loop 120 BPM", source_bpm=None):
        self.changed = _Signal()
        self.refreshes = 0
        self.updates = 0
        self.snapshots = 0
        self.app = SimpleNamespace(
            project=SimpleNamespace(bpm=bpm),
            library=_Library(audio, sr, name=name, bpm=source_bpm),
            engine=_Engine(),
            status=_Status(),
        )

    def refresh(self):
        self.refreshes += 1

    def update(self):
        self.updates += 1

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


def test_advanced_resample_handles_empty_audio_without_shape_breakage():
    result = _resample_exact(np.empty((0, 2), dtype=np.float32), 32)
    assert result.shape == (32, 2)
    assert np.all(result == 0.0)


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


def test_source_bpm_prefers_metadata_then_filename():
    assert source_bpm(SimpleNamespace(bpm=128.0, name="Loop 90 BPM")) == 128.0
    assert source_bpm(SimpleNamespace(bpm=None, name="PG - Early Morning - 140 BPM Gm")) == 140.0
    assert source_bpm(SimpleNamespace(bpm=None, name="Untitled loop")) is None


def test_match_project_tempo_infers_musical_length_from_source_bpm():
    sr = 1000
    source = np.column_stack((np.ones(8000, dtype=np.float32), np.ones(8000, dtype=np.float32)))
    owner = _attach_snapshot(_Owner(source, bpm=90.0, sr=sr, name="Four Bar Loop 120 BPM"))
    clip = Clip(kind="audio", ref="source", length_beats=16.0, source_length=8.0)

    match_audio_to_project_tempo(owner, clip)

    assert clip.length_beats == 16.0
    assert np.isclose(clip.source_length, 16.0 * 60.0 / 90.0)
    assert owner.app.library._audio[clip.ref].shape == (10667, 2)
    assert owner.app.library.clips[clip.ref].parent == "source"
    assert "120" in owner.app.status.messages[-1][0]
    assert "90" in owner.app.status.messages[-1][0]


def test_make_unique_copies_only_current_trimmed_region():
    sr = 1000
    source = np.column_stack((np.arange(5000, dtype=np.float32), np.arange(5000, dtype=np.float32)))
    owner = _attach_snapshot(_Owner(source, sr=sr))
    clip = Clip(kind="audio", ref="source", offset=1.0, source_length=2.0, length_beats=4.0)

    make_audio_unique(owner, clip)

    assert clip.ref == "render1"
    assert clip.offset == 0.0
    assert clip.source_length == 2.0
    assert owner.app.library._audio[clip.ref].shape == (2000, 2)
    assert owner.app.library.clips[clip.ref].parent == "source"


def test_normalize_clip_gain_is_nondestructive_and_capped():
    source = np.full((1000, 2), 0.25, dtype=np.float32)
    owner = _attach_snapshot(_Owner(source, sr=1000))
    clip = Clip(kind="audio", ref="source", source_length=1.0, gain=0.2)

    normalize_clip_gain(owner, clip)

    assert np.isclose(clip.gain, 4.0)
    assert clip.ref == "source"
    assert owner.app.library._next == 0
    assert owner.updates == 1


def test_use_full_source_resets_trim_and_natural_timeline_length():
    source = np.ones((3000, 2), dtype=np.float32)
    owner = _attach_snapshot(_Owner(source, bpm=120.0, sr=1000))
    clip = Clip(kind="audio", ref="source", offset=1.0, source_length=1.0, length_beats=9.0, loop=True)

    use_full_source(owner, clip)

    assert clip.offset == 0.0
    assert clip.source_length == 3.0
    assert clip.length_beats == 6.0
    assert clip.loop is False
