"""Release audio integrity and browser availability for the original expansion packs."""

import hashlib
import json

import numpy as np
import soundfile as sf

from mpclab.library import Library
from mpclab.prism import EFFECT_PRESETS
from mpclab.runtime_paths import RESOURCE_ROOT
from scripts.build_prism_packs import PACKS


def test_expansion_audio_and_browser(tmp_path):
    library = Library(tmp_path / "library", include_bundled=True)
    library.scan()
    hashes = set()
    for folder, name in PACKS:
        root = RESOURCE_ROOT / "assets/sample-packs" / folder
        manifest = json.loads((root / "manifest.json").read_text())
        assert len(manifest["sounds"]) == 24
        clips = [clip for clip in library.clips.values() if clip.pack == name]
        assert len(clips) == 24
        by_path = {clip.source_path: clip for clip in clips}
        for entry in manifest["sounds"]:
            path = root / entry["file"]
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            assert digest == entry["sha256"] and digest not in hashes
            hashes.add(digest)
            info = sf.info(path)
            assert info.samplerate == 48000 and info.subtype == "PCM_24"
            audio, _ = sf.read(path, always_2d=True)
            assert audio.shape == (entry["frames"], entry["channels"])
            assert np.isfinite(audio).all()
            assert np.abs(audio.mean(axis=0)).max() < 1e-6
            assert 0.70 < np.max(np.abs(audio)) < 0.71
            assert np.max(np.abs(audio[[0, -1]])) < 1e-6
            assert by_path[str(path.resolve())].root_note == entry["root_midi"]
    ids = set(library.clips)
    library.scan()
    assert set(library.clips) == ids


def test_effect_presets_use_supported_track_controls():
    from mpclab.model import TrackFX

    for settings in EFFECT_PRESETS.values():
        assert set(settings) <= TrackFX.__dataclass_fields__.keys()
        assert all(0 <= value <= 1 for value in settings.values())
