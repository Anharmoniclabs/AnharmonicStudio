"""Validate the shipped audio, musical tuning, and relocatable factory library."""

import hashlib
import json
import shutil

import numpy as np
import soundfile as sf

from mpclab.crates import sample_group
from mpclab.factory import install_kit, groove
from mpclab.library import Library
from mpclab.runtime_paths import RESOURCE_ROOT

PACK = RESOURCE_ROOT / "assets/drums/trap-foundry"


def test_shipped_audio_format_fades_headroom_and_tuning():
    manifest = json.loads((PACK / "manifest.json").read_text())
    assert len(manifest["sounds"]) == 64
    hashes = set()
    for sound in manifest["sounds"]:
        path = PACK / sound["file"]
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        assert digest == sound["sha256"] and digest not in hashes
        hashes.add(digest)
        info = sf.info(path)
        assert info.samplerate == 48000 and info.subtype == "PCM_24"
        data, sr = sf.read(path, always_2d=True)
        assert np.isfinite(data).all() and 0.5 < np.abs(data).max() < 0.85
        assert np.abs(data[:2]).max() == 0 and np.abs(data[-2:]).max() == 0
        assert np.sqrt(np.mean(data**2)) > 0.005
        if sound["root_midi"] is not None:
            # Measure the sustained fundamental, after the intentional attack dive.
            segment = data[sr : 3 * sr, 0] * np.hanning(2 * sr)
            spectrum = np.abs(np.fft.rfft(segment))
            frequencies = np.fft.rfftfreq(len(segment), 1 / sr)
            fundamental = frequencies[np.argmax(spectrum)]
            expected = 440 * 2 ** ((sound["root_midi"] - 69) / 12)
            assert abs(fundamental - expected) < 0.6


def test_pack_is_automatic_rescannable_and_relocatable(tmp_path):
    library = Library(tmp_path / "library", include_bundled=True)
    ids = set(library.clips)
    assert len(ids) == 64
    library.scan()
    assert set(library.clips) == ids
    moved = tmp_path / "moved-pack"
    shutil.copytree(PACK, moved)
    relocated = Library(tmp_path / "other")
    relocated._scan_pack(moved, "Anharmonic Trap Foundry", identity="anharmonic-trap-foundry-v1")
    assert set(relocated.clips) == ids
    bass = [c for c in library.clips.values() if c.root_note is not None]
    assert len(bass) == 12
    assert all(sample_group(c) == ("melodic", "Bass") for c in bass)
    kit = install_kit(library, 3)
    assert len(kit) == 8 and len({c.id for c in kit}) == 8
    assert all(c.pack == "Anharmonic Trap Foundry" for c in kit)
    assert groove(3, 16).name == "Trap Foundry groove"
