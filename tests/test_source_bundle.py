"""Release source ZIPs use committed bytes and portable Git file modes."""

import hashlib
from pathlib import Path
import subprocess
import zipfile

from scripts.build_source_bundle import build_bundle


def test_committed_source_is_independent_of_checkout_endings_and_permissions(tmp_path):
    def git(*args):
        return subprocess.check_output(["git", *args], cwd=tmp_path, text=True).strip()

    git("init", "-q")
    git("config", "user.name", "Bundle test")
    git("config", "user.email", "bundle-test@example.invalid")
    git("config", "core.autocrlf", "false")
    source = tmp_path / "mpclab" / "example.py"
    source.parent.mkdir()
    source.write_bytes(b"first\nsecond\n")
    launcher = tmp_path / "run.sh"
    launcher.write_bytes(b"#!/bin/sh\nexit 0\n")
    git("add", "mpclab", "run.sh")
    git("update-index", "--chmod=+x", "run.sh")
    git("commit", "-qm", "source")

    first = tmp_path / "first.zip"
    first_hash = build_bundle(tmp_path, first, committed_only=True)
    source.write_bytes(b"first\r\nsecond\r\n")
    source.chmod(0o755)
    launcher.chmod(0o644)
    (source.parent / "untracked.py").write_bytes(b"untracked\n")
    second = tmp_path / "second.zip"
    assert build_bundle(tmp_path, second, committed_only=True) == first_hash
    assert hashlib.sha256(second.read_bytes()).hexdigest() == first_hash
    with zipfile.ZipFile(second) as bundle:
        assert bundle.read("AnharmonicStudio/mpclab/example.py") == b"first\nsecond\n"
        assert bundle.getinfo("AnharmonicStudio/mpclab/example.py").external_attr >> 16 == 0o100644
        assert bundle.getinfo("AnharmonicStudio/run.sh").external_attr >> 16 == 0o100755
        assert not any("untracked" in name for name in bundle.namelist())

    # Non-release source snapshots still include local edits for development.
    working = tmp_path / "working.zip"
    build_bundle(tmp_path, working)
    with zipfile.ZipFile(working) as bundle:
        assert bundle.read("AnharmonicStudio/mpclab/example.py") == b"first\r\nsecond\r\n"
        assert bundle.read("AnharmonicStudio/mpclab/untracked.py") == b"untracked\n"


def test_builtin_drums_ship_while_private_recordings_remain_excluded(tmp_path):
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    (tmp_path / ".gitignore").write_bytes(
        (Path(__file__).resolve().parents[1] / ".gitignore").read_bytes()
    )
    drum = tmp_path / "assets/drums/trap-foundry/Kicks/AH_Kick.wav"
    drum.parent.mkdir(parents=True)
    drum.write_bytes(b"original first-party drum")
    for relative in ("recordings/take.wav", "assets/drums/private.wav", "library/voice.wav"):
        private = tmp_path / relative
        private.parent.mkdir(parents=True, exist_ok=True)
        private.write_bytes(b"private recording")
    destination = tmp_path / "source.zip"
    build_bundle(tmp_path, destination)
    with zipfile.ZipFile(destination) as bundle:
        assert bundle.read("AnharmonicStudio/assets/drums/trap-foundry/Kicks/AH_Kick.wav") == (
            b"original first-party drum"
        )
        assert not any("private.wav" in name or "take.wav" in name for name in bundle.namelist())
        assert not any(name.startswith("AnharmonicStudio/library/") for name in bundle.namelist())
