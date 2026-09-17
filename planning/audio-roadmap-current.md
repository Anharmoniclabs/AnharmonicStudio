# Anharmonic Studio — Current Audio Architecture Repass

**Audit base:** `fd51faef702e3ba6d411c5f3412d11ddab5c4288`  
**Inputs:** Pirkle DSP roadmap, Pirkle synthesizer roadmap, Smith physical-audio roadmap, current repository implementation and CI evidence.

This file is a **current engineering status**, not the older historical baseline in
`planning/daw-capabilities.json`. The older file remains useful as provenance, but several of its
limitations have already been improved on `main`.

## What is already stronger than the old baseline

- Mixer/project architecture now supports up to **128 tracks**; the legacy eight-track count is a
  default layout, not the runtime maximum.
- Multiple built-in instrument instances exist with stable instrument IDs, independent patches,
  MIDI channels, recording destinations and project persistence.
- Sample voices have explicit live/sequenced ownership, placement IDs, choke domains, gate/loop
  behavior and cross-pattern isolation.
- Synth and external voices carry sequence ownership so stop/seek can release scheduled voices
  without killing unrelated live performance.
- MIDI routing already implements sustain, sostenuto, soft pedal, pitch bend, pressure and CC
  expression behavior.
- The built-in synth already uses PolyBLEP correction for discontinuous oscillator shapes.
- Graph-aware plug-in delay compensation, native/reference audio regressions, bounded offline
  rendering, browser regression checks and cross-platform native CI already exist.
- Current CI baseline is green across source checks, Python shards, callback benchmarks, Linux
  packaging, website deployment, Linux/Windows native and both Apple Silicon/Intel macOS targets.

## Foundation gaps confirmed by the repass

| Area | Status before this pass | Action |
|---|---|---|
| Parameter smoothing | Missing as a shared primitive | **LAND NOW** — reusable linear and one-pole smoothers |
| General modulation matrix | Missing | **LAND NOW** — typed parameter specs and deterministic routes |
| Reusable envelopes/detectors | Synth-local/fragmented | **LAND NOW** — DAHDSR generator + peak/RMS follower |
| Shared delay-line reference | Duplicated/private implementations | **LAND NOW** — integer/fractional line with bounded feedback |
| DSP test signals | Ad hoc arrays in tests | **LAND NOW** — impulse/DC/step/sine/Nyquist/seeded-noise helpers |
| Audio event trace | Missing | Next P0 diagnostics ticket |
| Per-track/plugin CPU profiler | Partial global timing only | Next P1 diagnostics ticket |
| Plug-in sidechain audio buses | Missing | P1 graph/routing ticket |
| External plug-in per instrument | Partial; previous attempt reverted | P0/P1 redesign with PDC and lifecycle tests |
| Project sample-rate workflow | Runtime configurable, project UX centered on 48 kHz | P1 project/device contract |
| Hardware I/O patchbay | Partial device selection, stereo graph | P1/P2 routing expansion |
| Multichannel track graph | Missing | P2 after stereo graph contracts are sealed |
| Managed disk read-ahead | Partial mmap/bounded cache | P1 large-session reliability |
| Parallel graph scheduler | Missing | P2 after graph ownership/profiling |
| 64-bit summing mode | Missing | P2 quality option with explicit conversion boundaries |
| Shared processor interface | Fragmented | P1 first-party DSP lifecycle contract |
| Multisample key/velocity regions | Missing as full instrument system | P2 synth architecture |
| Vector synth core | Missing | P2 Prism architecture |
| FM/PM operator graph | Missing | P3 synth architecture |
| Physical waveguides/modal bank/FDN | Missing | P2/P3 physical-model architecture |

## Highest-leverage build order from here

### P0 — keep playback trustworthy

1. Deterministic bounded audio-event trace for voice create/release/steal and transport changes.
2. Rendered-loop regression that checks **audio onsets**, not only scheduled event lists.
3. External hosted-instrument ownership redesign: one instance per stable instrument without
   repeating the previously reverted latency/lifecycle problems.
4. Make retrigger behavior explicit for pads (`restart`, `layer`, `ignore`, `crossfade`) while
   preserving current one-shot/gate/loop semantics.

### P1 — unify control and DSP architecture

1. Wire the new smoother into gain/pan/filter/delay/drive transitions where abrupt updates remain.
2. Migrate synth LFO/envelope/velocity/pressure/Prism control toward the shared modulation matrix.
3. Introduce a first-party processor contract:
   `prepare → reset → process → latency_samples → tail_samples → save/restore`.
4. Extract common filter and delay primitives from monolithic effect code without changing sound.
5. Add per-track, per-plugin and disk-pressure diagnostics to the existing callback timing view.
6. Add true plug-in sidechain audio routing with PDC-aware graph edges.

### P2 — production instrument/effect expansion

1. Multisample key zones, velocity layers and deterministic round robin.
2. Vector mixer and gesture paths as a first-class Prism mode.
3. Fractional-delay chorus/flanger/vibrato built from the shared delay primitive.
4. Crossovers and multiband dynamics after shared detectors/gain computers are stable.
5. Modal resonator instrument/effect as the first distinctive physical-model feature.
6. FDN reverb after reusable delay/filter primitives are native-accelerated and measured.

### P3 — distinctive Anharmonic systems

1. Plucked-string/waveguide instrument.
2. Bow/reed nonlinear exciters with bounded solvers.
3. FM/PM operator graph.
4. Plate/membrane numerical models where CPU budgets justify them.
5. Physical Prism: gesture controls excitation position, force, damping and material parameters.

## Engineering gates for every future audio change

A feature is not accepted just because it makes sound.

- Ruff/compile gates pass.
- Focused regression exists.
- Six Python shards pass.
- Callback fallback/benchmark gates pass.
- Native/reference regressions pass where relevant.
- Offline/live behavior is compared when the feature exists in both paths.
- Stop/seek/panic ownership is defined.
- Sample-rate and block-size behavior is tested.
- Parameter extremes cannot generate NaN/Inf or unbounded feedback.
- State serializes without breaking old projects.
- UI does not become the owner of DSP state.

## Immediate status after this pass

The new foundation modules are intentionally **not wired into existing hot paths in the same
commit**. They land with isolated tests first. Once CI proves them on every supported path, the next
commit can migrate existing filter/gain/delay/modulation code incrementally and compare output before
and after. This keeps the green baseline meaningful while still moving the architecture forward.
