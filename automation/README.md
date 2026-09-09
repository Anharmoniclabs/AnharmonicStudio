# Anharmonic Studio agent

This is a local continuous development agent using the installed Codex CLI and the existing Codex model/authentication configuration. The owner requested overnight refinement with passes running one after another across the whole DAW. Each pass researches, implements and verifies one useful slice; the next starts immediately after a successful integration. A loop-wide file lock prevents overlapping workers. The timer starts the service at 22:00 local time if needed; an already running loop continues, including past morning, until paused or blocked by its failure/prerequisite/artifact policy. The computer must remain awake with Codex network/account access available. Normal account usage applies; the computer is not woken.

The project skill is [anharmonic-studio](../.agents/skills/anharmonic-studio/SKILL.md). The [continuous workflow and coverage ledger](../docs/agent/CONTINUOUS_WORKFLOW.md), [direction](../docs/agent/DIRECTION.md), [reading library](../docs/agent/RESEARCH.md), [rubric](../docs/agent/RUBRIC.md), [backlog](../docs/agent/BACKLOG.md), and [memory](../docs/agent/MEMORY.md) keep work targeted and persistent. The controller rotates through recovery/recording, tracks/engine, MIDI, plugins/routing, sound design, arrangement/sampling, mixing/delivery, and UI/accessibility. Session-loss/audio regressions and failed gates override the rotation.

## What a cycle does

1. Takes a stable copy of current source, including uncommitted/untracked code and the native C++ sources/configuration. Generated native builds are excluded. Personal `library/`, `projects/`, `exports/`, private application settings, and the original Git index are not copied or edited.
2. Starts a new noninteractive Codex session in that copy, using a workspace-write sandbox, web search, and no unattended escalation. The model and reasoning settings come from the existing Codex configuration. No additional agents are spawned.
3. Saves the result, patch, and manifests. Runs independent lock/native-build/ABI/lint/format/test/fallback/DSP gates. The native ABI smoke loads symbols without constructing an engine or opening audio/MIDI. The source virtual environment supplies existing dependencies; packages are not installed by this runner.
4. Applies the verified patch to the original source only if the copied source still matches and no pause was requested. Saves prior changed files and preserves existing modes across restrictive service umasks. The user's Git index is untouched. Changes take effect on the next normal DAW launch; no running app is restarted.
5. Records focus and advances the rotation after an attempted pass. Successful passes continue immediately; held passes retain candidates/logs and use a 60-second pause-aware backoff. Three consecutive failures/held results pause the loop. Prerequisites and artifact limits can also hold work. The artifact limit is 2 GiB; archive reviewed runs before resuming if reached.

One service plus a lock held across the continuous loop prevents overlapping cycles, including another one-shot invocation. Offscreen Qt, isolated settings, synthetic media, disabled hardware tests, and instructions against application/process control protect the current desktop session. Ordinary source edits can integrate automatically. Agent/controller/direction changes, release workflow changes, file deletions, new unavailable dependencies, failed checks, and concurrent source edits remain held for inspection.

Applying a multi-file patch is not a filesystem-wide transaction. The controller checks the complete source before application, runs `git apply --check`, checks again, and verifies the result. Avoid editing the same files during the brief apply phase; a race or interrupted application requires inspecting the retained patch/backup. No controller can coordinate with editors that do not participate in its lock.

## Use

From `/home/al/Projects/mpc-lab`:

```bash
python3 automation/anharmonic_agent.py status
python3 automation/anharmonic_agent.py pause
python3 automation/anharmonic_agent.py resume
python3 automation/anharmonic_agent.py check
```

The installed `anharmonic-agent` command is a shortcut from any directory, for example `anharmonic-agent status` or `anharmonic-agent pause`.

Pause prevents future cycles and integration from a currently running cycle; it does not kill a process. A current worker finishes and the loop exits without integration. Resume clears pause and permits the next start. After verifying the target is this dedicated service, start immediately with:

```bash
systemctl --user start --no-block anharmonic-studio-agent.service
```

The service uses `python3 automation/anharmonic_agent.py run --continuous`. For an
explicit single cycle use `run` without `--continuous`; the same lock still applies.
If a paused worker is still finishing, resume allows that existing loop to continue.

The timer/service definitions live here and are installed in `~/.config/systemd/user/`. Inspect scheduling with `systemctl --user list-timers anharmonic-studio-agent.timer`. To disable future scheduling, use `systemctl --user disable --now anharmonic-studio-agent.timer`; that command targets only the timer. Never stop or restart the shared Codex application/server to manage this worker.

Detailed state is in `~/.local/state/anharmonic-studio-agent/status.json`, with durable focus rotation in `rotation.json`. Each `runs/<UTC timestamp>/` contains the worker result, event stream, stderr, quality log when reached, patch, source manifests, and prior changed files after successful integration. Held candidates keep their workspace. Successful candidates discard their scratch checkout; their patch and changed-file backup remain. These local logs may contain source code and should stay private.

Edit `docs/agent/DIRECTION.md` and the coverage ledger to steer musical priorities. The timer controls when a stopped loop is started; it does not insert gaps between passes in an active loop. Do not change the active chat application, its process, or its workspace.

## Validate the controller

```bash
.venv/bin/python -m pytest -q automation/test_anharmonic_agent.py
.venv/bin/ruff check automation
.venv/bin/ruff format --check automation
systemd-analyze --user verify automation/anharmonic-studio-agent.service automation/anharmonic-studio-agent.timer
```

The controller tests use temporary miniature repositories and cover preservation of uncommitted/untracked source, media exclusion, concurrent edits, pause, protected paths, deletions, symlinks, and verified integration. They do not call Codex, touch audio hardware, or launch windows.
