# Anharmonic Studio — DAW establishment pass

Date: 2026-09-08

This pass turns the September DAW inventory into working migration infrastructure
without breaking project-format-3 files or pretending unfinished host/device
systems are production ready.

## Implemented backend systems

- Conservative offline sample root-pitch estimation with confidence scoring.
- Stable channel identities layered over the current 64 sample slots and shared synth.
- Deterministic mono-sample glide, legato and retrigger policy helpers shared by
  future live/offline rendering integration.
- Backend-neutral MIDI events, pitch-bend decoding and timestamp-to-frame conversion.
- Filesystem-only LV2/CLAP/VST3 discovery plus persistent quarantine state; discovery
  deliberately does not execute third-party code.
- Deterministic project media inventory, missing-media detection, chunked hashing and
  conservative relink ranking.
- Portable project packaging that copies only referenced media and writes a hashed
  media manifest without modifying source assets.
- Bounded byte-budget LRU cache infrastructure for decoded/derived media.
- Immutable engine snapshots, bounded command batches and an optional native-engine
  capability/loader contract with safe Python fallback.
- Focused regression tests for all of these foundations.

The quality workflow is the merge gate. Until the current head reaches pytest and
all callback/DSP smoke checks successfully, these changes stay on the PR branch.

## Compatibility policy

1. Project format 3 remains the persisted compatibility baseline in this pass.
2. Existing pad indices are never silently reinterpreted as new channel IDs.
3. Pitch detection is advisory: confidence and manual correction remain required.
4. Disk I/O, hashing, decoding and plugin discovery stay outside the audio callback.
5. Missing native/plugin/MIDI backends are reported as unavailable rather than
   represented by placeholder controls.
6. Headless CI is not a claim of physical-interface latency or long-session stability.

## Integration work still required

The modules above are real backend implementations, but several systems still need
wiring into the existing application before they are user-facing production features.

### Sample instruments

- Persist glide/legato/retrigger settings in a backward-compatible project-format bump.
- Add Auto Root + confidence to Notes/Sample UI with manual override.
- Feed the same glide trajectory through live `PadVoice` playback and offline export.
- Regression-test overlapping mono notes, tempo changes, seek/loop boundaries and
  live/export equivalence.

### Channels and instruments

- Persist stable channel IDs in the next project format while retaining formats 0–3.
- Add independent synth instances instead of one shared project patch.
- Detach Notes destinations from physical pad slots without breaking pad projects.
- Add reorder/clone/group operations against stable identities rather than list order.

### Media and projects

- Connect Package Project to the Library path resolver and File menu.
- Add missing-media/relink UI and store trusted media hashes in the project manifest.
- Replace current unbounded decoded/reverse caches with `MediaLRU` budgets.
- Add chunked/memory-mapped streaming for long recordings and songs.

### Realtime engine

- Implement the actual Rust/C/C++ render graph behind the native-engine protocol.
- Build immutable snapshots outside the callback and swap them only at block boundaries.
- Use bounded SPSC queues and preallocated voice/bus/DSP/event storage.
- Remove Python allocation/object traversal from the hard realtime callback.
- Validate dense sessions, discontinuities, underrun recovery and physical-interface latency.

### MIDI

- Add an ALSA/JACK device backend with hotplug and timestamped event capture.
- Wire sustain, CC, pitch bend, input compensation and clock into stable channels.
- Add MIDI file import/export and mapping/preferences UI.

### Plugins and mixer

- Add an isolated scanner process that validates discovered plugins before use.
- Implement an LV2/CLAP host, then VST3, with state restoration and crash containment.
- Add plugin delay compensation, dynamic mixer buses, groups, sidechains and routing.
- Expand automation to synth/plugin parameters with smoothing and touch/latch/write.

### Recording, editing, delivery and release

- General multichannel recording, punch/pre-roll, monitoring and calibrated placement.
- Editable fades/crossfades, warp markers, spectral view and pitch/time tools.
- Stem export, more formats/sample rates, dither, loudness/true-peak and export queue.
- Reproducible Linux packages, desktop/AppStream/MIME integration, SBOM/signing.
- Screen-reader semantics, keyboard-only/high-DPI testing and long-session fault injection.

## Current boundary

This branch materially establishes the architecture and backend services needed for
the next stage, but it does not claim that a native hard-realtime renderer, physical
MIDI backend, or executable plugin host has been completed. Those systems require
native/platform integrations and hardware/plugin validation, not placeholder Python UI.

## Public-beta gate

A strong public beta should not be declared until the realtime ownership boundary,
project collect/relink UI, distributable package, hardware smoke matrix, recovery
fault injection and long-session tests are complete. Plugin hosting is optional for
the first beta only if it is not advertised; every advertised feature must be real,
recoverable and covered by tests.
