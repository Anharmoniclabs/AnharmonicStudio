# Runtime Verification: 2026-09-16

## Identity

- Tested SHA: `65b88a75bf4fbf486d849ed3356557c44e61d211`
- Branch: `main`
- Worktree: contains uncommitted Tranche 3 persistence edits and prior audit/CI edits
- OS: Ubuntu 24.04.4 LTS, x86_64
- System Python: 3.12.3
- `uv`: 0.12.12

## Locked environment

`uv sync --locked --python /usr/bin/python3.12 --group dev` passed.

Installed versions:

- PySide6 6.11.1
- NumPy 2.2.6
- pytest 9.1.1
- soundfile 0.14.0
- sounddevice 0.5.5
- python-rtmidi 1.5.8
- pedalboard 0.9.24
- Ruff 0.16.5
- psutil 7.2.2
- verovio 5.7.0
- onnxruntime 1.20.1
- Playwright 1.58.0
- PyInstaller 6.16.0

## Native builds

- `scripts/build_native.py`: PASS
- `scripts/build_rubberband.py`: PASS
- `bash scripts/build-native.sh`: PASS
- Native C++ tests: 2/2 passed (`portable_core`, `lv2_control_shadow`)

## Focused suites

- Core persistence/schema/migration/IO/browser-interchange selection: **32 passed**.
- Broader isolated persistence selection: **55 passed, 1 failed**.
- The remaining failure is pre-existing and unrelated to Tranche 3 persistence:
  `test_migrated_and_custom_ids_produce_identical_callback_audio` reaches an
  existing `ExternalDSP.render_instrument` argument/signature mismatch in
  `engine_mixing.py`.
- Recording/Qt selection: blocked during collection because PySide6 cannot load
  `libGL.so.1` from this non-root container.
- Full Python/Qt suite: blocked by the same `libGL.so.1` system dependency.

## Static and browser gates

- Ruff lint: PASS.
- Touched persistence-file formatting: PASS.
- Repository-wide formatting: BLOCKED by 30 pre-existing unrelated formatting
  files; no formatting-only bulk rewrite was applied.
- `compileall`: PASS.
- Browser JavaScript unit tests: **47 passed**.
- Website validator: PASS.
- Chromium tests: blocked; Playwright browser exits because `libatk-1.0.so.0`
  is unavailable.
- Offscreen application self-check: blocked because PySide6 cannot load
  `libGL.so.1`.
- Linux bundle: blocked before build because the required bundled Prism pack was
  not staged. Building that pack would create unrelated generated artifacts and
  was not performed in this persistence-only verification tranche.

## Failure classification

- Missing `libGL.so.1`: ENVIRONMENT / system dependency. The container user is
  non-root, so CI's apt provisioning cannot be reproduced here.
- Missing `libatk-1.0.so.0`: ENVIRONMENT / system dependency for Chromium.
- Missing Prism bundle staging: ENVIRONMENT/packaging prerequisite for this local
  invocation; CI's quality workflow stages Prism before invoking the Linux bundle.
- `ExternalDSP.render_instrument` signature mismatch: PRE-EXISTING DEFECT,
  outside recording/persistence changes.
- Repository-wide Ruff formatting drift: PRE-EXISTING DEFECT/WORKTREE STATE,
  unrelated to this tranche.

## Tranche-specific finding and fix

The first persistence run exposed a real Tranche 3 regression: schema validation
imported `workflow_organization.py`, which imported `PySide6.QInputDialog` at
module import time. Plain headless `Project.to_dict()` therefore required Qt.
The UI import was made lazy inside the organization attachment boundary.

The next run exposed an absent-field default bug: missing `track_folders` was
passed as `{}` instead of `[]`. The schema default was corrected. The core
persistence selection then passed 32/32.

## Verification decision

- Tranche 2 recording: **not fully verified**; focused Qt/recording tests are
  blocked by missing system libraries.
- Tranche 3 persistence: **not fully verified**; core pure-Python persistence
  coverage passes, but full Python/Qt execution is blocked and one broader
  selection contains a pre-existing engine failure.
- Tranche 4: **not safe to begin** until a root-capable/CI-equivalent Ubuntu
  environment runs the full Qt recording and persistence suites, offscreen
  startup, Chromium, and packaging gates.
