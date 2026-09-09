"""Bounded decoded storage without realtime cache eviction or truncated previews."""

import numpy as np
import pytest
import soundfile as sf

from mpclab.audio_storage import read_stereo
from mpclab.library import Library


def test_forward_reverse_share_heap_budget_without_evicting_sources(tmp_path):
    library = Library(tmp_path, sample_rate=8000, audio_budget_bytes=800)
    source = np.linspace(-0.5, 0.5, 100, dtype=np.float32)
    clips = [library.add_audio(source, str(index)) for index in range(4)]
    assert library.audio_heap_bytes <= 800
    for clip in clips:
        forward = library.cached_audio(clip.id)
        reverse = library.reversed_audio(clip.id)
        assert forward is not None
        np.testing.assert_allclose(forward[:, 0], source, atol=2e-6)
        np.testing.assert_allclose(reverse, forward[::-1])
        assert library.cached_reversed_audio(clip.id) is reverse
    assert library.audio_heap_bytes <= 800
    assert isinstance(library.audio(clips[-1].id), np.memmap)
    # Releasing a cache reference must not invalidate an already playing voice.
    held = library.cached_audio(clips[-1].id)
    library._audio.clear()
    np.testing.assert_allclose(held[:, 0], source, atol=2e-6)


def test_disk_decode_uses_blocks_and_preserves_stereo(monkeypatch, tmp_path):
    source = np.linspace(-0.5, 0.5, 150000, dtype=np.float32)
    path = tmp_path / "source.wav"
    sf.write(path, source, 48000, subtype="PCM_24")
    monkeypatch.setattr(sf, "read", lambda *_a, **_k: pytest.fail("unbounded read"))
    result, rate = read_stereo(path, 64, tmp_path)
    assert isinstance(result, np.memmap)
    assert rate == 48000
    np.testing.assert_allclose(result[:, 0], source, atol=2e-6)
    np.testing.assert_array_equal(result[:, 0], result[:, 1])


@pytest.mark.parametrize("length", [1, 80, 256, 259])
def test_waveform_keeps_short_clips_and_last_partial_bucket(tmp_path, length):
    library = Library(tmp_path, audio_budget_bytes=0)
    data = np.zeros(length, np.float32)
    data[-1] = 0.5
    clip = library.add_audio(data, "tail")
    peaks = library.peaks(clip.id)
    assert len(peaks) == (length + 255) // 256
    assert peaks[-1, 1] == pytest.approx(0.5)
    overview = library.overview(clip.id, buckets=7)
    assert np.isfinite(overview).all()
    assert overview[-1] == pytest.approx(1.0)


def test_resampled_decode_is_also_file_backed(tmp_path):
    source = tmp_path / "source.wav"
    sf.write(source, np.zeros((800, 2), np.float32), 8000)
    library = Library(tmp_path / "library", sample_rate=16000, audio_budget_bytes=0)
    clip = library.import_file(source)
    # A linked non-normalized source exercises decode-time conversion too.
    clip.source_path = str(source)
    data = library.audio(clip.id)
    assert data.shape == (1600, 2)
    assert isinstance(data, np.memmap)
    assert library.audio_heap_bytes == 0


def test_large_peak_buckets_keep_extrema_across_decode_blocks(tmp_path):
    library = Library(tmp_path, audio_budget_bytes=0)
    data = np.zeros(150000, np.float32)
    data[65536] = -0.7
    data[-1] = 0.6
    clip = library.add_audio(data, "large bucket")
    peaks = library.peaks(clip.id, bucket=100000)
    np.testing.assert_allclose(peaks, [[-0.7, 0], [0, 0.6]], atol=2e-6)


def test_full_mapping_disk_fails_without_losing_library_source(tmp_path, monkeypatch):
    import errno
    from mpclab import audio_storage

    library = Library(tmp_path / "library")
    clip = library.add_audio(np.full(32, 0.2, np.float32), "preserve me")
    library._audio.clear()
    library.audio_budget_bytes = 0

    def disk_full(*_args):
        raise OSError(errno.ENOSPC, "No space left on device")

    monkeypatch.setattr(audio_storage.os, "posix_fallocate", disk_full, raising=False)
    with pytest.raises(OSError, match="No space"):
        library.audio(clip.id)
    data, _ = sf.read(library.wav_path(clip.id))
    np.testing.assert_allclose(data, 0.2, atol=2e-6)
    assert not list(library._storage_dir.iterdir())


def test_generated_slice_is_detached_from_mutable_caller_storage(tmp_path):
    library = Library(tmp_path, audio_budget_bytes=64)
    source = np.full((10000, 2), 0.2, np.float32)
    clip = library.add_audio(source[:8], "small cut")
    source.fill(0.9)
    np.testing.assert_allclose(library.audio(clip.id), 0.2)
    assert library.audio(clip.id).base is None
    assert library.audio_heap_bytes == 64
