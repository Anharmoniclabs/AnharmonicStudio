from pathlib import Path
import zipfile

import numpy as np
import pytest
import soundfile as sf

from scripts.install_sample_pack import extract_pack, main, safe_members


def make_wav(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    sf.write(path, np.zeros(480, dtype=np.float32), 48000)


def make_zip(path: Path, members: dict[str, bytes]):
    with zipfile.ZipFile(path, "w") as archive:
        for name, data in members.items():
            archive.writestr(name, data)


def test_safe_members_rejects_parent_escape(tmp_path):
    archive_path = tmp_path / "bad.zip"
    make_zip(archive_path, {"../escape.wav": b"x"})
    with zipfile.ZipFile(archive_path) as archive:
        with pytest.raises(ValueError, match="unsafe ZIP member"):
            safe_members(archive)


def test_extract_pack_never_overwrites_existing_file(tmp_path):
    archive_path = tmp_path / "pack.zip"
    make_zip(archive_path, {"Kicks/kick.wav": b"new", "README.txt": b"readme"})
    destination = tmp_path / "pack"
    target = destination / "Kicks" / "kick.wav"
    target.parent.mkdir(parents=True)
    target.write_bytes(b"keep")

    files, audio = extract_pack(archive_path, destination)

    assert (files, audio) == (2, 1)
    assert target.read_bytes() == b"keep"
    assert (destination / "README.txt").read_bytes() == b"readme"


def test_dry_run_writes_nothing(tmp_path):
    archive_path = tmp_path / "pack.zip"
    make_zip(archive_path, {"Drums/kick.wav": b"fake"})
    destination = tmp_path / "out"

    assert extract_pack(archive_path, destination, dry_run=True) == (1, 1)
    assert not destination.exists()


def test_main_extracts_real_audio_and_registers_pack(tmp_path):
    source = tmp_path / "kick.wav"
    make_wav(source)
    archive_path = tmp_path / "Trap Pack.zip"
    with zipfile.ZipFile(archive_path, "w") as archive:
        archive.write(source, "Drums/Kick.wav")

    repo = tmp_path / "repo"
    (repo / "mpclab").mkdir(parents=True)
    # main() only uses this marker to ensure the destination looks like a checkout.
    (repo / "mpclab" / "library.py").write_text("marker")
    pack_root = tmp_path / "packs"

    # Patch Library inside the imported installer module so the test exercises
    # ZIP handling and registration wiring without importing a fake repo package.
    import scripts.install_sample_pack as installer
    from mpclab.library import Library

    real_library = Library
    installer.Library = lambda _root: real_library(tmp_path / "library")
    try:
        result = main(
            [
                str(archive_path),
                "--repo",
                str(repo),
                "--pack-root",
                str(pack_root),
                "--name",
                "Trap Genesis",
            ]
        )
    finally:
        installer.Library = real_library

    assert result == 0
    extracted = pack_root / "Trap Pack" / "Drums" / "Kick.wav"
    assert extracted.is_file()
    library = real_library(tmp_path / "library")
    clips = [clip for clip in library.clips.values() if clip.pack == "Trap Genesis"]
    assert len(clips) == 1
    assert Path(clips[0].source_path) == extracted.resolve()
