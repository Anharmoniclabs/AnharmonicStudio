# Anharmonic Studio Bug-Bounty Continuation Gate

This file is the durable handoff for the ongoing Anharmonic Studio hardening campaign.

## Resume instruction

If a future chat is asked to **continue the Anharmonic bug bounty**, first read this file, issue #9, open pull requests, and `RELEASE_AUDIT.md`. Continue from the first unchecked software-only tranche below. Do not claim physical-device, platform, commercial-plugin, or real-hardware acceptance without evidence from those environments.

## Operating rule

Work in small, reviewable tranches. Each fix should include reproducible regression coverage when practical. Do not combine unrelated high-risk engine, host, web, and release changes into one giant pull request. Prefer: reproduce -> test -> patch -> CI -> merge -> update this ledger.

## Completed / landed

- [x] Routing/bus/send graph and plugin delay compensation foundation.
- [x] Loop/punch recording and automatic take rotation.
- [x] Non-destructive take comping / swipe editing.
- [x] Serial insert plugin chains across track/bus/master paths.
- [x] Read/Write/Touch/Latch automation modes.
- [x] Standalone browser DAW foundation and project interchange hardening.
- [x] Browser/mobile Safari audio-import compatibility fix (#24, main `c486299`).
- [x] Premium roadmap issue #9 refreshed to remove stale already-completed items.

## In flight

- [ ] PR #25: deterministic mobile compatibility regression tests. Merge only after Source Checks and Portable C++ Engine workflows pass.
- [ ] PR #23: capability foundations. Review/integrate in qualified sub-tranches; do not treat its 216-capability ledger as release qualification by itself.

## Software-only tranches ChatGPT can continue implementing

### Reliability and project safety
- [ ] Expand malformed-project fuzzing on native and browser loaders.
- [ ] Add explicit save/load interruption and atomic-write regressions.
- [ ] Add crash/recovery simulations that can be performed without physical devices.
- [ ] Harden disk/permission/storage failure reporting where code paths are testable in CI.
- [ ] Expand plugin subprocess failure/recovery tests.
- [ ] Add longer CI soak tests with memory/thread/file-descriptor accounting where runner budgets permit.

### Browser/mobile
- [ ] Add stronger phone/tablet interaction acceptance checks: safe-area layout, touch targets, orientation changes, scroll trapping, and viewport keyboard pressure where automatable.
- [ ] Improve unsupported-native-processing disclosure so browser users cannot mistake preserved metadata for active processing.
- [ ] Continue browser/native project-field parity tests.
- [ ] Add browser marker/cue/region editing after native marker work is safely integrated.
- [ ] Continue large-session memory-budget and failure-path tests.

### DAW editing/workflow
- [ ] Richer automation curve shapes plus copy/paste tooling.
- [ ] Clip crossfade handle workflow and realtime/offline equivalence tests.
- [ ] Track folders and reusable track/project templates.
- [ ] Expand scene/clip launcher toward a real grid workflow.
- [ ] DAWproject interchange with bounded parsing and roundtrip tests.
- [ ] Improve shortcut collision detection and workflow presets.

### MIDI
- [ ] MIDI output implementation.
- [ ] MIDI clock output/synchronization implementation.
- [ ] MPE message path and recorded expression automation.
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
- [ ] Keep `README.md`, issue #9 and release status synchronized with merged behavior.
- [ ] Split current release status from historical audit material if the audit becomes ambiguous.
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

1. Reliability / project safety.
2. Browser/mobile regressions and usability hardening.
3. Release-truth and CI gates.
4. Editing/workflow gaps.
5. MIDI software implementation.
6. Mastering/analysis correctness.
7. Plugin-host architecture upgrades.
8. External hardware/platform qualification.

## Continuation phrase

A future chat request such as **"continue Anharmonic bug bounty"** should be treated as instruction to resume this ledger, inspect current GitHub state, and continue the first safe unchecked software-only item without asking the user to restate the project history.
