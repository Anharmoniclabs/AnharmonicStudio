"""Controller integration tests against disposable repositories; no Codex or device calls."""

import importlib.util
import json
import os
from pathlib import Path
import subprocess

import pytest


SPEC = importlib.util.spec_from_file_location(
    "anharmonic_agent", Path(__file__).with_name("anharmonic_agent.py")
)
agent = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(agent)


@pytest.fixture
def repo(tmp_path):
    root = tmp_path / "source"
    (root / "mpclab").mkdir(parents=True)
    (root / ".venv/bin").mkdir(parents=True)
    (root / "mpclab/engine.py").write_text("# committed engine\n")
    agent.run_command(["git", "init", "-q"], root)
    agent.run_command(["git", "add", "mpclab/engine.py"], root)
    agent.run_command(
        [
            "git",
            "-c",
            "user.name=Test",
            "-c",
            "user.email=test@localhost",
            "-c",
            "commit.gpgsign=false",
            "-c",
            "core.hooksPath=/dev/null",
            "commit",
            "-qm",
            "Initial",
        ],
        root,
    )
    (root / "mpclab/engine.py").write_text("# user's uncommitted engine\n")
    (root / "mpclab/untracked.py").write_text("# user's new feature\n")
    (root / "library").mkdir()
    (root / "library/private.wav").write_bytes(b"private audio")
    (root / "projects").mkdir()
    (root / "projects/private.json").write_text('{"private": true}')
    return root


def candidate(repo, tmp_path):
    run_dir = tmp_path / "run"
    workspace = run_dir / "workspace"
    before, head = agent.snapshot(repo, workspace)
    (workspace / "mpclab/engine.py").write_text("# user's uncommitted engine\n# refined envelope\n")
    patch = run_dir / "change.patch"
    changed, after = agent.make_patch(workspace, before, head, patch)
    return run_dir, workspace, before, after, changed, patch


def publish(repo, parts):
    run_dir, workspace, before, after, changed, patch = parts
    agent.integrate(repo, workspace, before, after, changed, patch, run_dir, run_dir / "PAUSED")


def test_snapshot_uses_current_untracked_source_without_private_media(repo, tmp_path):
    workspace = tmp_path / "worker"
    before, _ = agent.snapshot(repo, workspace)
    assert "mpclab/untracked.py" in before
    assert (workspace / "mpclab/engine.py").read_text() == "# user's uncommitted engine\n"
    assert list((workspace / "library").iterdir()) == []
    assert list((workspace / "projects").iterdir()) == []
    assert (workspace / ".venv").resolve() == repo / ".venv"


def test_integrates_patch_preserving_user_edits_index_and_media(repo, tmp_path):
    index_before = (repo / ".git/index").read_bytes()
    parts = candidate(repo, tmp_path)
    publish(repo, parts)
    assert (
        repo / "mpclab/engine.py"
    ).read_text() == "# user's uncommitted engine\n# refined envelope\n"
    assert (repo / "mpclab/untracked.py").read_text() == "# user's new feature\n"
    assert (repo / ".git/index").read_bytes() == index_before
    assert (repo / "library/private.wav").read_bytes() == b"private audio"
    assert (parts[0] / "before-changed-files.tar.gz").is_file()


def test_addition_integrates_without_touching_source_index(repo, tmp_path):
    run_dir, workspace, before, _, _, patch = candidate(repo, tmp_path)
    index_before = (repo / ".git/index").read_bytes()
    (workspace / "mpclab/new_control.py").write_text("# new control\n")
    head = agent.run_command(["git", "rev-parse", "HEAD"], workspace).stdout.strip()
    changed, after = agent.make_patch(workspace, before, head, patch)
    publish(repo, (run_dir, workspace, before, after, changed, patch))
    assert (repo / "mpclab/new_control.py").read_text() == "# new control\n"
    assert (repo / ".git/index").read_bytes() == index_before


def test_concurrent_source_edit_holds_entire_candidate(repo, tmp_path):
    parts = candidate(repo, tmp_path)
    (repo / "mpclab/untracked.py").write_text("# owner changed this during the run\n")
    with pytest.raises(RuntimeError, match="Source changed"):
        publish(repo, parts)
    assert (repo / "mpclab/engine.py").read_text() == "# user's uncommitted engine\n"
    assert "owner changed" in (repo / "mpclab/untracked.py").read_text()


def test_pause_after_worker_completion_prevents_integration(repo, tmp_path):
    parts = candidate(repo, tmp_path)
    (parts[0] / "PAUSED").touch()
    with pytest.raises(RuntimeError, match="Paused"):
        publish(repo, parts)
    assert (repo / "mpclab/engine.py").read_text() == "# user's uncommitted engine\n"


@pytest.mark.parametrize(
    "path",
    [
        "automation/controller.py",
        ".agents/skills/agent/SKILL.md",
        ".github/workflows/release.yml",
        "docs/agent/DIRECTION.md",
    ],
)
def test_protected_changes_are_held(repo, tmp_path, path):
    parts = candidate(repo, tmp_path)
    run_dir, workspace, before, _, _, patch = parts
    target = workspace / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("changed\n")
    head = agent.run_command(["git", "rev-parse", "HEAD"], workspace).stdout.strip()
    changed, after = agent.make_patch(workspace, before, head, patch)
    with pytest.raises(RuntimeError, match="configuration or release workflow"):
        publish(repo, (run_dir, workspace, before, after, changed, patch))
    assert not (repo / path).exists()


def test_deleted_source_is_held(repo, tmp_path):
    parts = candidate(repo, tmp_path)
    run_dir, workspace, before, _, _, patch = parts
    (workspace / "mpclab/untracked.py").unlink()
    head = agent.run_command(["git", "rev-parse", "HEAD"], workspace).stdout.strip()
    changed, after = agent.make_patch(workspace, before, head, patch)
    with pytest.raises(RuntimeError, match="File deletion"):
        publish(repo, (run_dir, workspace, before, after, changed, patch))
    assert (repo / "mpclab/untracked.py").exists()


def test_candidate_mutation_after_verification_is_held(repo, tmp_path):
    parts = candidate(repo, tmp_path)
    (parts[1] / "mpclab/engine.py").write_text("# unverified edit\n")
    with pytest.raises(RuntimeError, match="Candidate changed"):
        publish(repo, parts)


def test_symlink_cannot_export_or_replace_external_content(repo, tmp_path):
    outside = tmp_path / "private.txt"
    outside.write_text("private")
    (repo / "mpclab/external.py").symlink_to(outside)
    with pytest.raises(ValueError, match="symlink"):
        agent.inventory(repo)
    assert outside.read_text() == "private"


def test_failed_quality_never_applies_and_third_failure_pauses(repo, tmp_path, monkeypatch):
    state = tmp_path / "state"
    original_run = subprocess.run

    def fake_codex(command, **kwargs):
        if "--output-last-message" not in command:
            return original_run(command, **kwargs)
        workspace = Path(kwargs["cwd"])
        (workspace / "mpclab/engine.py").write_text("# broken candidate\n")
        result_path = Path(command[command.index("--output-last-message") + 1])
        result_path.write_text(json.dumps({"status": "completed", "summary": "Candidate"}))
        return subprocess.CompletedProcess(command, 0)

    def failed_verification(*args):
        raise RuntimeError("A real gate failed")

    monkeypatch.setattr(agent, "preflight", lambda *args: {})
    monkeypatch.setattr(agent.subprocess, "run", fake_codex)
    monkeypatch.setattr(agent, "verify", failed_verification)
    for _ in range(3):
        assert agent.cycle(repo, state) == 1
        assert (repo / "mpclab/engine.py").read_text() == "# user's uncommitted engine\n"
    assert (state / "PAUSED").exists()
    assert json.loads((state / "status.json").read_text())["state"] == "paused"
    assert agent.cycle(repo, state) == 0


@pytest.mark.parametrize("original_mode", [0o600, 0o640, 0o644, 0o750, 0o755])
def test_restrictive_umask_preserves_existing_modes_after_editor_replacement(
    repo, tmp_path, original_mode
):
    source = repo / "mpclab/engine.py"
    source.chmod(original_mode)
    parts = candidate(repo, tmp_path)
    run_dir, workspace, before, _, _, patch = parts
    (workspace / "mpclab/engine.py").chmod(0o644)
    head = agent.run_command(["git", "rev-parse", "HEAD"], workspace).stdout.strip()
    changed, after = agent.make_patch(workspace, before, head, patch)
    index_before = (repo / ".git/index").read_bytes()
    previous_umask = os.umask(0o077)
    try:
        publish(repo, (run_dir, workspace, before, after, changed, patch))
    finally:
        os.umask(previous_umask)
    assert source.stat().st_mode & 0o777 == original_mode
    assert (workspace / "mpclab/engine.py").stat().st_mode & 0o777 == original_mode
    assert source.read_text().endswith("# refined envelope\n")
    assert (repo / ".git/index").read_bytes() == index_before
    assert agent.inventory(repo) == after


@pytest.mark.parametrize("mode", [0o600, 0o644, 0o700, 0o755])
def test_restrictive_umask_restores_new_file_permissions_and_executable_bit(repo, tmp_path, mode):
    run_dir, workspace, before, _, _, patch = candidate(repo, tmp_path)
    added = workspace / "mpclab/new_tool.py"
    added.write_text("#!/usr/bin/env python3\n# verified helper\n")
    added.chmod(mode)
    head = agent.run_command(["git", "rev-parse", "HEAD"], workspace).stdout.strip()
    changed, after = agent.make_patch(workspace, before, head, patch)
    previous_umask = os.umask(0o077)
    try:
        publish(repo, (run_dir, workspace, before, after, changed, patch))
    finally:
        os.umask(previous_umask)
    assert (repo / "mpclab/new_tool.py").stat().st_mode & 0o777 == mode
    assert agent.inventory(repo) == after


def test_content_mismatch_is_held_before_permissions_are_restored(repo, tmp_path, monkeypatch):
    source = repo / "mpclab/engine.py"
    source.chmod(0o644)
    parts = candidate(repo, tmp_path)
    real_command = agent.run_command

    def interfere_after_apply(args, cwd, **kwargs):
        result = real_command(args, cwd, **kwargs)
        if args[:2] == ["git", "apply"] and "--check" not in args:
            source.write_text("# concurrent edit during application\n")
            source.chmod(0o600)
        return result

    monkeypatch.setattr(agent, "run_command", interfere_after_apply)
    with pytest.raises(RuntimeError, match="content verification failed"):
        publish(repo, parts)
    assert source.stat().st_mode & 0o777 == 0o600
    assert source.read_text() == "# concurrent edit during application\n"
    assert (parts[0] / "before-changed-files.tar.gz").exists()


def test_native_sources_are_copied_but_generated_builds_and_binary_symlinks_are_excluded(
    repo, tmp_path
):
    sources = {
        "native/CMakeLists.txt": "project(test)\n",
        "native/include/anharmonic/engine.hpp": "// declaration\n",
        "native/src/engine.cpp": "// implementation\n",
        "native/tests/render.cpp": "// regression\n",
        "native/cmake/Options.cmake": "# build configuration\n",
    }
    for relative, content in sources.items():
        target = repo / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content)
    for relative in (
        "native/build/CMakeCache.txt",
        "native/cmake-build-debug/generated.cpp",
        "native/CMakeFiles/compiler.cpp",
        "native/libanharmonic_native.so.0",
        "native/engine.o",
        "native/CMakeCache.txt",
        "native/.agent-runtime/fixture.cpp",
    ):
        target = repo / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"generated")
    (repo / "native/build/libanharmonic_native.so").symlink_to(tmp_path / "outside.so")
    workspace = tmp_path / "snapshot"
    before, _ = agent.snapshot(repo, workspace)
    assert {path for path in before if path.startswith("native/")} == set(sources)
    assert not (workspace / "native/build").exists()
    for relative, content in sources.items():
        assert (workspace / relative).read_text() == content


def test_native_source_symlink_is_still_rejected(repo, tmp_path):
    (repo / "native/src").mkdir(parents=True)
    (repo / "native/src/engine.cpp").symlink_to(tmp_path / "external.cpp")
    with pytest.raises(ValueError, match="symlink"):
        agent.inventory(repo)


def test_native_gate_is_required_for_native_changes_when_dependencies_are_missing(
    repo, tmp_path, monkeypatch
):
    (repo / "native").mkdir()
    (repo / "native/CMakeLists.txt").write_text("project(test)\n")
    monkeypatch.setattr(agent, "native_prerequisite_error", lambda: "Missing Lilv")
    monkeypatch.setattr(agent, "quality_commands", lambda _repo: [])
    log = tmp_path / "quality.log"
    with pytest.raises(RuntimeError, match="native changes require this gate"):
        agent.verify(repo, repo, log, ["native/src/engine.cpp"])
    agent.verify(repo, repo, log, ["mpclab/ui/theme.py"])
    assert "Native gate unavailable: Missing Lilv" in log.read_text()


def test_native_build_and_abi_gate_precede_existing_checks_without_engine_creation(
    repo, tmp_path, monkeypatch
):
    (repo / "native").mkdir()
    (repo / "native/CMakeLists.txt").write_text("project(test)\n")
    commands = []
    monkeypatch.setattr(agent, "native_prerequisite_error", lambda: None)
    monkeypatch.setattr(agent, "quality_commands", lambda _repo: [["existing-gate"]])
    monkeypatch.setattr(agent, "run_command", lambda args, *a, **kw: commands.append(args))
    agent.verify(repo, repo, tmp_path / "quality.log", ["native/src/engine.cpp"])
    assert commands[0][:3] == ["cmake", "-S", "native"]
    assert ".agent-runtime/native-build" in commands[0]
    assert commands[1] == ["cmake", "--build", ".agent-runtime/native-build", "--parallel", "2"]
    assert "ctypes.CDLL" in commands[2][-1]
    assert "anh_engine_create(" not in commands[2][-1]
    assert "sounddevice" not in commands[2][-1]
    assert commands[-1] == ["existing-gate"]


def mock_worker(monkeypatch, prompts, after_worker=None):
    original_run = subprocess.run

    def fake_codex(command, **kwargs):
        if "--output-last-message" not in command:
            return original_run(command, **kwargs)
        workspace = Path(kwargs["cwd"])
        target = workspace / "mpclab/engine.py"
        target.write_text(target.read_text() + "# bounded musical improvement\n")
        target.chmod(0o644)
        prompts.append(kwargs["input"])
        result_path = Path(command[command.index("--output-last-message") + 1])
        result_path.write_text(
            json.dumps({"status": "completed", "summary": "Synthetic candidate"})
        )
        if after_worker:
            after_worker()
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(agent, "preflight", lambda *args: {})
    monkeypatch.setattr(agent.subprocess, "run", fake_codex)


def test_continuous_passes_integrate_sequentially_rotate_and_resume_durably(
    repo, tmp_path, monkeypatch
):
    state = tmp_path / "state"
    prompts = []
    mock_worker(monkeypatch, prompts)
    monkeypatch.setattr(agent, "verify", lambda *args: None)
    actual_cycle = agent._cycle

    def pause_after_three(*args):
        result = actual_cycle(*args)
        if len(prompts) == 3:
            (state / "PAUSED").touch()
        return result

    monkeypatch.setattr(agent, "_cycle", pause_after_three)
    monkeypatch.setattr(
        agent, "wait_before_retry", lambda *args: pytest.fail("Success must not wait")
    )
    assert agent.run_cycles(repo, state, continuous=True) == 0
    assert len(prompts) == 3
    assert (repo / "mpclab/engine.py").read_text().count("bounded musical improvement") == 3
    for index, prompt in enumerate(prompts):
        assert f"This pass focuses on {agent.FOCUS_AREAS[index]}" in prompt
    rotation = agent.read_json(state / "rotation.json")
    assert rotation["next_focus_index"] == 3
    assert rotation["attempted_passes"] == 3
    assert all((run / "change.patch").exists() for run in (state / "runs").iterdir())
    assert all(not (run / "workspace").exists() for run in (state / "runs").iterdir())
    (state / "PAUSED").unlink()
    assert agent.cycle(repo, state) == 0
    assert f"This pass focuses on {agent.FOCUS_AREAS[3]}" in prompts[-1]
    assert agent.read_json(state / "rotation.json")["attempted_passes"] == 4


def test_continuous_failures_back_off_then_pause_with_evidence(repo, tmp_path, monkeypatch):
    state = tmp_path / "state"
    prompts = []
    waits = []
    mock_worker(monkeypatch, prompts)

    def failed_gate(*args):
        raise RuntimeError("Reproducible signal regression")

    monkeypatch.setattr(agent, "verify", failed_gate)
    monkeypatch.setattr(
        agent, "wait_before_retry", lambda _state, seconds: waits.append(seconds) or True
    )
    assert agent.run_cycles(repo, state, continuous=True) == 1
    assert len(prompts) == 3
    assert waits == [agent.FAILURE_BACKOFF_SECONDS] * 2
    assert (state / "PAUSED").exists()
    status = agent.read_json(state / "status.json")
    assert status["consecutive_failures"] == 3
    assert status["reason"] == "Reproducible signal regression"
    assert "Reproducible signal regression" in prompts[1]
    assert (repo / "mpclab/engine.py").read_text() == "# user's uncommitted engine\n"
    assert all((run / "workspace").exists() for run in (state / "runs").iterdir())


def test_manual_pause_after_worker_retains_patch_without_gate_or_failure(
    repo, tmp_path, monkeypatch
):
    state = tmp_path / "state"
    mock_worker(monkeypatch, [], after_worker=lambda: (state / "PAUSED").touch())
    monkeypatch.setattr(
        agent, "verify", lambda *args: pytest.fail("Paused candidate must not run gates")
    )
    assert agent.run_cycles(repo, state, continuous=True) == 0
    status = agent.read_json(state / "status.json")
    assert status["state"] == "held"
    assert status["consecutive_failures"] == 0
    assert (Path(status["run_dir"]) / "change.patch").exists()
    assert (Path(status["run_dir"]) / "workspace").exists()
    assert (repo / "mpclab/engine.py").read_text() == "# user's uncommitted engine\n"


def test_continuous_lock_blocks_one_shot_during_pass_and_failure_backoff(
    repo, tmp_path, monkeypatch
):
    state = tmp_path / "state"
    attempts = []

    def held_pass(*args):
        attempts.append("pass")
        assert agent.cycle(repo, state) == 0
        return 1

    def pause_in_backoff(*args):
        assert agent.cycle(repo, state) == 0
        (state / "PAUSED").touch()
        return False

    monkeypatch.setattr(agent, "_cycle", held_pass)
    monkeypatch.setattr(agent, "wait_before_retry", pause_in_backoff)
    assert agent.run_cycles(repo, state, continuous=True) == 0
    assert attempts == ["pass"]


def test_failure_wait_observes_pause_with_short_checks(tmp_path, monkeypatch):
    sleeps = []

    def pause(seconds):
        sleeps.append(seconds)
        (tmp_path / "PAUSED").touch()

    monkeypatch.setattr(agent.time, "sleep", pause)
    assert not agent.wait_before_retry(tmp_path, 60)
    assert sleeps == [1.0]


def test_controller_restores_callers_umask_and_strips_native_library_override(
    repo, tmp_path, monkeypatch
):
    previous_umask = os.umask(0o027)
    try:
        state = tmp_path / "state"
        state.mkdir()
        (state / "PAUSED").touch()
        assert agent.cycle(repo, state) == 0
        assert os.umask(0o027) == 0o027
    finally:
        os.umask(previous_umask)
    monkeypatch.setenv("ANHARMONIC_NATIVE_LIBRARY", "/outside/live.so")
    monkeypatch.setenv("ANHARMONIC_NATIVE_BUILD_DIR", "/outside/build")
    environment = agent.isolated_environment(tmp_path)
    assert "ANHARMONIC_NATIVE_LIBRARY" not in environment
    assert "ANHARMONIC_NATIVE_BUILD_DIR" not in environment


def test_rotation_wraps_after_all_focus_areas(repo, tmp_path, monkeypatch):
    state = tmp_path / "state"
    agent.write_json(
        state / "rotation.json",
        {"next_focus_index": len(agent.FOCUS_AREAS) - 1, "attempted_passes": 7},
    )
    focuses = []
    monkeypatch.setattr(
        agent, "_cycle", lambda _repo, _state, focus, number: focuses.append((focus, number)) or 0
    )
    assert agent.cycle(repo, state) == 0
    assert agent.cycle(repo, state) == 0
    assert focuses == [(agent.FOCUS_AREAS[-1], 8), (agent.FOCUS_AREAS[0], 9)]


def test_paused_controller_does_not_advance_rotation_or_start_worker(repo, tmp_path, monkeypatch):
    state = tmp_path / "state"
    state.mkdir()
    (state / "PAUSED").touch()
    monkeypatch.setattr(agent, "_cycle", lambda *args: pytest.fail("Paused worker must not start"))
    assert agent.run_cycles(repo, state, continuous=True) == 0
    assert not (state / "rotation.json").exists()
