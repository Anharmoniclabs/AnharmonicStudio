"""An identified native release must contain only its committed build inputs."""

import subprocess

import pytest

from scripts.build_release import release_source_commit


def test_release_rejects_modified_staged_and_untracked_source(tmp_path):
    def git(*args):
        return subprocess.check_output(["git", *args], cwd=tmp_path, text=True).strip()

    git("init", "-q")
    git("config", "user.name", "Release test")
    git("config", "user.email", "release-test@example.invalid")
    source = tmp_path / "mpclab" / "fixture.py"
    source.parent.mkdir()
    source.write_text("version = 1\n")
    git("add", "mpclab")
    git("commit", "-qm", "fixture")
    assert release_source_commit(tmp_path) == git("rev-parse", "HEAD")

    # Personal settings outside build inputs are not packaged and need not block.
    (tmp_path / "workflow-bindings.json").write_text("{}")
    assert release_source_commit(tmp_path) == git("rev-parse", "HEAD")
    source.write_text("version = 2\n")
    with pytest.raises(ValueError, match="Commit source"):
        release_source_commit(tmp_path)
    git("add", "mpclab")
    with pytest.raises(ValueError, match="Commit source"):
        release_source_commit(tmp_path)
    git("commit", "-qm", "update")
    (source.parent / "untracked.py").write_text("untracked = True\n")
    with pytest.raises(ValueError, match="Commit source"):
        release_source_commit(tmp_path)
