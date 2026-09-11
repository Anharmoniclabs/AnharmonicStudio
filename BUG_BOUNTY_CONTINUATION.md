# Anharmonic Studio Bug-Bounty Continuation Gate

This file is the durable handoff for the ongoing Anharmonic Studio hardening campaign.

## Resume instruction

If a future chat is asked to **continue the Anharmonic bug bounty**, first read this file, issue #9, open pull requests, and `RELEASE_AUDIT.md`. Continue from the first safe unchecked software-only tranche below. Do not claim physical-device, platform, commercial-plugin, or real-hardware acceptance without evidence from those environments.

## Operating rule

Work in small, reviewable tranches. Each fix should include reproducible regression coverage when practical. Do not combine unrelated high-risk engine, host, web, and release changes into one giant pull request. Prefer: reproduce -> test -> patch -> CI -> merge -> update this ledger. When a PR is intentionally stacked, merge its base first and retarget the dependent PR to `main` immediately.

## Completed / landed

- [x] Routing/bus/send graph and plugin delay compensation foundation.
- [x] Loop/punch recording and automatic take rotation.
- [x] Non-destructive take comping / swipe editing.
- [x] Serial insert plugin chains across track/bus/master paths.
- [x] Read/Write/Touch/Latch automation modes.
- [x] Standalone browser DAW foundation and project interchange hardening.
- [x] Browser/mobile Safari audio-import compatibility fix (#24, main `c486299`).
- [x] Durable continuation gate (#26, main `132ebf9`).
- [x] MIDI expression channels preserved for MPE-capable paths (#29, main `c968678`).
- [x] Workflow keymap persistence bounded and atomically saved (#32, main `663da56`).
- [x] Premium roadmap issue #9 refreshed to distinguish completed software from remaining qualification.

## Active hardening dependency graph

- [ ] PR #30 `bug-bounty/automation-curves`: smooth automation evaluator + parser hardening. The first implementation accidentally replaced the mature automation panel; it has been corrected to preserve the production panel and add smooth curves additively. Await fresh Source Checks + Portable C++ results on head `b1290f5`.
- [ ] PR #33 `bug-bounty/recovery-archive-atomicity`: transactional project/history recovery archive with rollback if the second move fails. Portable C++ is green; Source Checks is still running.
- [ ] PR #36 `bug-bounty/bounded-project-load-clean`: intentionally stacked on #33. Bounded project/history JSON reads, explicit UTF-8/JSON errors, bounded autosave recovery, and atomic-save regressions. After #33 merges, retarget #36 to `main`.
- [ ] PR #34 `bug-bounty/current-release-status-clean`: concise `CURRENT_RELEASE_STATUS.md`. Original gates were green; branch was synchronized with current main by merge commit `3b28008`; await fresh gates then merge.
- [ ] PR #35 `bug-bounty/mobile-layout-clean`: mobile safe areas, `dvh`, horizontal controls, scroll containment, 40px touch targets and layout tests. Original gates were green; branch synchronized to current main by merge commit `bec7912`; await fresh gates then merge.
- [ ] PR #38 `bug-bounty/web-engine-disclosure`: intentionally stacked on #35. Persistent `WEB ENGINE · NO DESKTOP PLUGINS / NATIVE DSP` disclosure. After #35 merges, retarget #38 to `main`.
- [ ] PR #37 `bug-bounty/pattern-step-validation`: malformed nested pattern-step maps fail as explicit `ValueError`; includes seeded nested-step fuzzing. Await gates, then merge if green.
- [ ] PR #23 capability foundations: large/high-regression surface. Do not blindly merge; integrate qualified sub-tranches only.

## Superseded branches / PRs

- PR #27 superseded by clean mobile-layout PR #35.
- PR #31 superseded by clean release-status PR #34.
- PR #28 old bounded-loader branch is superseded conceptually by stacked PR #36; close #28 once #36 is safely established against `main`.

## Software-only tranches ChatGPT can continue implementing

### Reliability and project safety
- [x] Keymap persistence: bounded input + atomic fsync/replace.
- [ ] Merge recovery archive atomicity (#33).
- [ ] Merge bounded desktop project/history loaders (#36).
- [ ] Expand malformed-project fuzzing beyond nested pattern steps.
- [ ] Add more save/load interruption and autosave failure-path regressions.
- [ ] Harden disk/permission/storage failure reporting where testable in CI.
- [ ] Expand plugin subprocess failure/recovery tests.
- [ ] Add longer CI soak tests with memory/thread/file-descriptor accounting where runner budgets permit.

### Browser/mobile
- [ ] Merge responsive mobile safe-area/layout hardening (#35).
- [ ] Merge persistent unsupported-native-processing disclosure (#38).
- [ ] Continue browser/native project-field parity tests.
- [ ] Add browser marker/cue/region editing after native marker work is safely integrated.
- [ ] Continue large-session memory-budget and failure-path tests.

### DAW editing/workflow
- [ ] Merge additive smooth automation curves (#30), preserving all current automation UI/contracts.
- [ ] Add automation copy/paste and richer editing after curve support is stable.
- [ ] Clip crossfade handle workflow and realtime/offline equivalence tests.
- [ ] Track folders and reusable track/project templates.
- [ ] Expand scene/clip launcher toward a real grid workflow.
- [ ] DAWproject interchange with bounded parsing and roundtrip tests.
- [ ] Continue shortcut collision detection and workflow presets.

### MIDI
- [x] Preserve MIDI expression channels for MPE-capable paths.
- [ ] MIDI output implementation.
- [ ] MIDI clock output/synchronization implementation.
- [ ] Full MPE path and recorded expression automation.
- [ ] Add software loopback tests; leave final hardware acceptance external.

### Audio analysis/mastering
- [ ] Standards-correct integrated loudness (LUFS) analysis.
- [ ] Loudness range (LRA) analysis.
- [ ] Reconstructed true-peak measurement.
- [ ] Live spectrum and stereo-field scopes.
- [ ] Calibrated averaging and meter reset/hold behavior.
- [ ] Dedicated mastering workspace only after measurements are verified.

### Plugin host / engine
- [ ] Plugin multi-output routing.
- [ ] Independent multi-instrument instances.
- [ ] Lower-latency isolated plugin IPC/bridge improvements.
- [ ] Native VST3/AU editor-window bridging without sacrificing crash isolation.
- [ ] CLAP hosting support.
- [ ] Expand automated host stress tests around process death, preset/state churn and export failures.

### Release and commercial hardening
- [ ] Merge current release-status gate (#34).
- [ ] Keep `README.md`, issue #9 and current release status synchronized with merged behavior.
- [ ] Add stale-roadmap / stale-feature-state checks where feasible.
- [ ] Add purchase entitlement recovery design/code for lost confirmation links/accounts.
- [ ] Add older-major-version catalog support.
- [ ] Keep release promotion gated on one immutable source commit plus matching source/notices/checksums.

## External acceptance ChatGPT cannot honestly complete from this environment

These remain required but must be performed on real target systems/hardware:

- [ ] Real iPhone/iPad Safari import/playback/microphone acceptance.
- [ ] Physical audio-interface testing (Focusrite/RME/MOTU/etc.) and round-trip latency measurements.
- [ ] Fresh sustained Intel Mac realtime-performance qualification.
- [ ] Clean-install/update/uninstall acceptance on Windows, Linux, Intel Mac and Apple Silicon Mac.
- [ ] Broad commercial-plugin compatibility runs with actual installed plugins.
- [ ] Real MIDI-controller/synth hardware acceptance.
- [ ] Windows/macOS security-warning and organization-policy acceptance.

## Current priority order

1. Finish/merge the currently green-or-running reliability and mobile PRs.
2. Reliability / project safety.
3. Browser/mobile regressions and usability hardening.
4. Release-truth and CI gates.
5. Editing/workflow gaps.
6. MIDI software implementation.
7. Mastering/analysis correctness.
8. Plugin-host architecture upgrades.
9. External hardware/platform qualification.

## Continuation phrase

A future chat request such as **"continue Anharmonic bug bounty"** should be treated as instruction to resume this ledger, inspect current GitHub state, and continue the dependency graph / first safe unchecked software-only item without asking the user to restate the project history.
