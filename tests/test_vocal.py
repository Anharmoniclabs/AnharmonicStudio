from __future__ import annotations

import sys
import threading
import types

import numpy as np
import pytest

from mpclab.model import Project, VocalSettings
from mpclab.vocal import (
    ProcessingCancelled,
    VocalRecorder,
    allowed_notes,
    analyze_pitch,
    detect_key,
    render_autotune,
)


def _tone(frequency: float, seconds: float = 0.35, sr: int = 48_000):
    t = np.arange(int(seconds * sr), dtype=np.float32) / sr
    mono = 0.25 * np.sin(2 * np.pi * frequency * t)
    return np.column_stack((mono, mono)).astype(np.float32)


def test_vocal_settings_round_trip_with_the_project():
    project = Project()
    project.vocal.key = "F#"
    project.vocal.scale = "minor"
    project.vocal.retune_ms = 7.5
    project.vocal_record.count_in_bars = 2
    project.vocal_record.mixer_track = 6
    project.vocal_record.input_latency_ms = 17.25

    loaded = Project.from_dict(project.to_dict())

    assert loaded.vocal.key == "F#"
    assert loaded.vocal.scale == "minor"
    assert loaded.vocal.retune_ms == pytest.approx(7.5)
    assert loaded.vocal_record.count_in_bars == 2
    assert loaded.vocal_record.mixer_track == 6
    assert loaded.vocal_record.input_latency_ms == pytest.approx(17.25)


def test_scale_quantizer_keeps_only_notes_in_the_selected_key():
    settings = VocalSettings(key="C", scale="major", low_note=60, high_note=72)
    notes = allowed_notes(settings).astype(int).tolist()
    assert notes == [60, 62, 64, 65, 67, 69, 71, 72]


def test_pitch_analysis_finds_a_sung_a_and_quantizes_an_out_of_key_note():
    settings = VocalSettings(key="C", scale="major", low_note=48, high_note=84)
    analysis = analyze_pitch(_tone(369.994), settings)
    voiced = analysis.confidence > 0.35

    assert np.any(voiced)
    assert np.nanmedian(analysis.detected_midi[voiced]) == pytest.approx(66, abs=0.2)
    # F# is outside C major, so every confident frame lands on F or G.
    assert set(np.rint(analysis.target_midi[voiced]).astype(int)) <= {65, 67}


def test_transpose_shifts_targets_even_in_chromatic_mode():
    settings = VocalSettings(scale="chromatic", transpose=12, low_note=48, high_note=84)
    analysis = analyze_pitch(_tone(220.0), settings)
    voiced = analysis.confidence > 0.35
    assert np.nanmedian(analysis.target_midi[voiced]) == pytest.approx(69, abs=0.1)


def test_autotune_render_is_finite_stereo_and_preserves_take_length():
    source = _tone(220.0, seconds=0.24)
    settings = VocalSettings(key="A", scale="minor", retune_ms=0, highpass_hz=70, compression=0.4)

    rendered, analysis = render_autotune(source, settings)

    assert rendered.shape == source.shape
    assert rendered.dtype == np.float32
    assert np.isfinite(rendered).all()
    assert np.max(np.abs(rendered)) <= 0.981
    assert analysis.voiced_fraction > 0.5


def test_hard_tune_moves_an_out_of_key_pitch_to_the_target_note():
    source = _tone(369.994, seconds=0.5)  # F#4
    settings = VocalSettings(
        key="C",
        scale="major",
        retune_ms=0,
        humanize=0,
        formant=0,
        highpass_hz=20,
        deesser=0,
        compression=0,
        presence_db=0,
        gate_db=-80,
    )

    rendered, before = render_autotune(source, settings)
    after = analyze_pitch(rendered, settings)
    voiced_before = before.confidence > 0.35
    voiced_after = after.confidence > 0.35

    assert np.nanmedian(before.detected_midi[voiced_before]) == pytest.approx(66, abs=0.2)
    assert np.nanmedian(after.detected_midi[voiced_after]) == pytest.approx(67, abs=0.25)


def test_key_detection_reports_a_supported_scale():
    settings = VocalSettings(key="C", scale="chromatic", low_note=48, high_note=84)
    analysis = analyze_pitch(_tone(440.0), settings)
    key, scale, confidence = detect_key(analysis)

    assert key in ("A", "D", "E")  # one note is intentionally ambiguous
    assert scale in ("major", "minor")
    assert confidence > 0.5


def test_empty_recorder_stop_returns_a_stereo_buffer():
    recorder = VocalRecorder()
    audio = recorder.stop()
    assert audio.shape == (0, 2)
    assert audio.dtype == np.float32


class _FakeInputStream:
    def __init__(self, owner, **kwargs):
        self.owner = owner
        self.callback = kwargs["callback"]
        self.closed = False
        owner.streams.append(self)

    def start(self):
        if self.owner.start_error is not None:
            raise self.owner.start_error

    def stop(self):
        pass

    def close(self):
        self.closed = True

    def push(self, mono: np.ndarray):
        data = np.asarray(mono, dtype=np.float32).reshape(-1, 1)
        self.callback(
            data,
            len(data),
            None,
            types.SimpleNamespace(input_overflow=False, __bool__=lambda self: False),
        )


class _FakeSoundDevice(types.ModuleType):
    def __init__(self, start_error=None):
        super().__init__("sounddevice")
        self.streams = []
        self.start_error = start_error

    def InputStream(self, **kwargs):
        return _FakeInputStream(self, **kwargs)


def _install_fake_input(monkeypatch, start_error=None):
    fake = _FakeSoundDevice(start_error)
    monkeypatch.setitem(sys.modules, "sounddevice", fake)
    return fake


def test_recorder_retains_pcm24_wav_until_committed(monkeypatch, tmp_path):
    fake = _install_fake_input(monkeypatch)
    recorder = VocalRecorder(sample_rate=1_000, blocksize=4, temp_dir=tmp_path)
    recorder.start(gain_db=6.0206)
    path = recorder.temporary_path

    fake.streams[0].push(np.array([0.1, -0.2, 0.3, -0.4]))
    audio = recorder.stop()

    assert audio.shape == (4, 2)
    assert audio[:, 0] == pytest.approx([0.2, -0.4, 0.6, -0.8], abs=2e-6)
    assert audio[:, 1] == pytest.approx(audio[:, 0], abs=1e-7)
    assert path is not None and path.exists()
    with pytest.raises(RuntimeError, match="previous take"):
        recorder.start()
    recorder.commit()
    assert not path.exists()
    assert list(tmp_path.iterdir()) == []


def test_recorder_pause_omits_audio_and_elapsed_time(monkeypatch, tmp_path):
    fake = _install_fake_input(monkeypatch)
    recorder = VocalRecorder(sample_rate=1_000, blocksize=100, temp_dir=tmp_path)
    recorder.start()
    stream = fake.streams[0]
    stream.push(np.ones(100) * 0.1)
    recorder.paused = True
    stream.push(np.ones(300) * 0.9)
    recorder.paused = False
    stream.push(np.ones(100) * 0.2)

    assert recorder.elapsed == pytest.approx(0.2)
    audio = recorder.stop()
    assert len(audio) == 200
    assert recorder.elapsed == pytest.approx(0.2)
    assert np.max(audio[:100]) == pytest.approx(0.1, abs=2e-6)
    assert np.max(audio[100:]) == pytest.approx(0.2, abs=2e-6)


def test_recorder_discard_and_start_failure_clean_temp_files(monkeypatch, tmp_path):
    fake = _install_fake_input(monkeypatch)
    recorder = VocalRecorder(temp_dir=tmp_path)
    recorder.start()
    path = recorder.temporary_path
    fake.streams[0].push(np.ones(32) * 0.1)
    recorder.discard()
    assert path is not None and not path.exists()

    _install_fake_input(monkeypatch, RuntimeError("device vanished"))
    with pytest.raises(RuntimeError, match="device vanished"):
        recorder.start()
    assert recorder.temporary_path is None
    assert list(tmp_path.iterdir()) == []


def test_recorder_queue_is_bounded_and_drops_instead_of_blocking(monkeypatch, tmp_path):
    fake = _install_fake_input(monkeypatch)
    recorder = VocalRecorder(sample_rate=1_000, blocksize=4, queue_blocks=2, temp_dir=tmp_path)
    release_writer = threading.Event()
    monkeypatch.setattr(recorder, "_write_capture", release_writer.wait)
    recorder.start()

    for _ in range(3):
        fake.streams[0].push(np.ones(4) * 0.1)

    assert recorder._queue is not None
    assert recorder._queue.maxsize == 2
    assert recorder.overruns == 1
    release_writer.set()
    assert recorder.elapsed == pytest.approx(0.012)
    recorder.discard()
    assert list(tmp_path.iterdir()) == []


def test_writer_failure_preserves_recovery_until_explicit_discard(monkeypatch, tmp_path):
    _install_fake_input(monkeypatch)
    monkeypatch.setattr(
        "mpclab.vocal.sf.SoundFile",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("disk full")),
    )
    recorder = VocalRecorder(temp_dir=tmp_path)
    recorder.start()

    with pytest.raises(RuntimeError, match="temporary vocal capture failed"):
        recorder.stop()
    assert recorder.temporary_path.is_file()
    recorder.discard()
    assert recorder.temporary_path is None
    assert list(tmp_path.iterdir()) == []


def test_pitch_analysis_progress_is_monotonic_and_cancellable():
    progress = []
    analysis = analyze_pitch(_tone(220.0, seconds=0.2), VocalSettings(), progress=progress.append)
    assert analysis.voiced_fraction > 0
    assert progress == sorted(progress)
    assert progress[-1] == 1.0

    cancel = threading.Event()

    def cancelling_progress(value):
        if value > 0.1:
            cancel.set()

    with pytest.raises(ProcessingCancelled):
        analyze_pitch(
            _tone(220.0, seconds=1.0),
            VocalSettings(),
            progress=cancelling_progress,
            cancelled=cancel.is_set,
        )


def test_autotune_progress_covers_all_phases_and_cancels_without_result():
    progress = []
    render_autotune(_tone(220.0, seconds=0.2), VocalSettings(), progress=progress.append)
    assert progress == sorted(progress)
    assert progress[0] == 0.0
    assert progress[-1] == 1.0
    assert any(0.3 <= value <= 0.4 for value in progress)
    assert any(value >= 0.9 for value in progress)

    cancel = threading.Event()

    def cancelling_progress(value):
        if value >= 0.32:
            cancel.set()

    with pytest.raises(ProcessingCancelled):
        render_autotune(
            _tone(220.0, seconds=1.0),
            VocalSettings(),
            progress=cancelling_progress,
            cancelled=cancel.is_set,
        )


@pytest.mark.parametrize("trailing", [False, True])
def test_dropped_capture_blocks_keep_the_timeline(monkeypatch, tmp_path, trailing):
    fake = _install_fake_input(monkeypatch)
    recorder = VocalRecorder(sample_rate=1000, blocksize=4, queue_blocks=2, temp_dir=tmp_path)
    release = threading.Event()
    original = recorder._write_capture

    def delayed_writer():
        release.wait()
        original()

    monkeypatch.setattr(recorder, "_write_capture", delayed_writer)
    recorder.start()
    try:
        stream = fake.streams[0]
        stream.push(np.full(4, 0.1))
        stream.push(np.full(4, 0.2))
        stream.push(np.full(6, 0.3))
        assert recorder.dropped_frames == 6
        release.set()
        recorder._queue.join()
        if not trailing:
            stream.push(np.full(4, 0.4))
        audio = recorder.stop()
        assert len(audio) == (14 if trailing else 18)
        np.testing.assert_allclose(audio[8:14], 0)
        if not trailing:
            np.testing.assert_allclose(audio[14:], 0.4, atol=2e-6)
        assert recorder.elapsed == len(audio) / 1000
    finally:
        release.set()
        recorder.discard()


def test_failed_decode_can_be_retried_without_losing_original(monkeypatch, tmp_path):
    from mpclab import vocal

    fake = _install_fake_input(monkeypatch)
    recorder = VocalRecorder(temp_dir=tmp_path)
    recorder.start()
    fake.streams[0].push(np.full(64, 0.2))
    original = vocal.read_stereo
    monkeypatch.setattr(vocal, "read_stereo", lambda *_: (_ for _ in ()).throw(MemoryError()))
    with pytest.raises(RuntimeError, match="recovery file"):
        recorder.stop()
    assert recorder.temporary_path.is_file()
    monkeypatch.setattr(vocal, "read_stereo", original)
    np.testing.assert_allclose(recorder.stop(), 0.2, atol=2e-6)
    recorder.commit()


def test_monitor_failure_does_not_abort_dry_recording(monkeypatch, tmp_path):
    fake = _install_fake_input(monkeypatch)
    recorder = VocalRecorder(temp_dir=tmp_path)
    recorder.start(monitor_callback=lambda _: (_ for _ in ()).throw(RuntimeError("route lost")))
    fake.streams[0].push(np.full(32, 0.2))
    np.testing.assert_allclose(recorder.stop(), 0.2, atol=2e-6)
    assert recorder.monitor_errors == 1
    recorder.commit()
