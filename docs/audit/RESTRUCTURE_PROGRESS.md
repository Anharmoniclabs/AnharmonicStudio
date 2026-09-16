# Restructure Progress

## Audit-first policy

- [x] Fetch `main` and record the exact starting SHA.
- [x] Read the required release, build, distribution, repository-audit, and
  bug-bounty documents.
- [x] Inspect open PRs, PR #3, and recent Actions failures.
- [x] Record the baseline before structural changes.
- [x] Produce the ownership/dependency map and migration order.
- [ ] Complete locked-toolchain quality gates on Python 3.12.
- [ ] Repair and rerun all Source Checks and native matrix workflows.
- [x] Introduce explicit recording session facades behind compatibility APIs.
- [ ] Replace runtime persistence wrappers with one schema pipeline.

## Evidence

- Starting SHA: `8ff5651d90bc737edc4bbbbf28f0ef7deffd11f1`.
- Local `compileall`: pass.
- Local website validator: pass.
- Local browser unit tests: 47 passed.
- Local Ruff and full Python/Qt tests: blocked by missing `uv`, Ruff, pytest,
  project dependencies, and Python 3.12 in this container.
- GitHub Source Checks failed only on the five known lint anchors; the current
  worktree contains their minimal repairs.
- GitHub Linux bundle independently fails frozen startup because
  `mpclab/prism_performances.json` is not included in the PyInstaller bundle.

## Tranche 2 recording consolidation

- Starting SHA: `8ff5651d90bc737edc4bbbbf28f0ef7deffd11f1`.
- PR #3 was reconciled, not merged: its recording behavior is present on main,
  while its broad stale feature bundle was intentionally excluded.
- Added `mpclab/recording.py` with explicit audio, vocal, MIDI, and sampler/event
  session contracts and the shared capture state machine.
- `TrackCapture` now selects the semantic session at arm time. Event sessions do
  not expose or initialize a microphone recorder; audio sessions own recorder
  start/stop/recovery calls.
- Added `tests/test_recording_contracts.py` for event isolation, input-channel
  forwarding, decode recovery, and cancellation.
- Existing loop, punch, take rotation, comping, keyboard, external MIDI, sampler,
  and vocal tests remain the compatibility proof surface.

Remaining debt is deliberately bounded: audio enumeration is not yet centralized
behind one manager, `TrackCapture` retains compatibility lifecycle flags, and
`recording_workflows.py` still decorates it for advanced modes.

## Tranche 3 persistence consolidation

- Recording checkpoint before this tranche: `65b88a75bf4fbf486d849ed3356557c44e61d211`.
- Added `mpclab/project_schema.py` as the authoritative extension registry.
- `Project` now owns workflow, plugin-chain, automation-mode, marker, MIDI-source,
  and track-folder fields with explicit defaults.
- Legacy persistence installers remain callable compatibility hooks but no longer
  monkey-patch `Project.to_dict/from_dict`.
- Added schema roundtrip and malformed-boundary regression coverage in
  `tests/test_project_schema.py`.
- Full persistence tests are pending the Python 3.12 dependency environment.
- Current validation: Ruff lint, persistence-file formatting, compileall, browser
  unit tests (47), and website validation pass. Focused Python persistence tests
  are blocked in this container by missing `PySide6`/NumPy dependencies; the
  repository-wide format check still reports unrelated pre-existing drift.

## Tranche 3.5 runtime verification

- Report: `docs/audit/RUNTIME_VERIFICATION_2026-09-16.md`.
- Locked Python 3.12 environment and native helpers were provisioned successfully.
- Pure persistence/schema selection passed 32 tests; full Qt/recording execution
  remains blocked by missing system libraries (`libGL.so.1`, `libatk-1.0.so.0`).
- No Tranche 4 work should begin until a root-capable CI-equivalent environment
  runs the full Qt, Chromium, offscreen startup, and packaging gates.

## Hard boundary

No production modules have been moved, renamed, deleted, or broadly rewritten in
this pass. PR #3 remains the recording implementation to reconcile before any
recording architecture movement.