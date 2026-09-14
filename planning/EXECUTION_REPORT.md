# First capability implementation batch

Date: 2026-09-10. Branch: `feature/daw-capability-foundations`.
Repair baseline: `612d6c412738e3ff9d0da022b5801831802967b7`.

## Source audit and scope

Both supplied downloads were read and reconciled. All 194 work packages agree
between Markdown and Excel; all 216 comparison capabilities, acceptance criteria,
historical status and source provenance are in [daw-capabilities.json](daw-capabilities.json).
The original downloads were not modified or executed. The comparison is a static
audit of `e07bd34`, not independent verification of competing DAW claims.

This branch is separate from the existing release-repair branch/PR. It neither
merges the owner's separate native-refactor worktree nor rewrites Git history.

## Implemented code

- `01-01`: removes the fixed-eight-track limit throughout model, native callback,
  offline rendering, mixer/routing/pad/synth/vocal/automation/MIDI controls, plugin
  addresses and browser storage/playback. Adds stable, undoable track creation to
  128 channels. Separate instrument instances and physical qualification remain.
- `03-04`: persistent named/color markers, cues and regions, production commands,
  shortcuts, snap-aware editing, navigation, loop ranges, project recall and
  atomic JSON/CSV metadata exports.
- `12-03` / `12-09`: cancelable streaming audio-file analysis, finite-sample checks,
  peaks/RMS/DC, overload/full-scale statistics and stereo analysis, plus source-safe
  JSON/text reports. LUFS/LRA/true peak and live calibrated scopes remain unimplemented.
- Integration repairs: new-strip automation signal wiring, stale FX-rack references,
  dynamic routing selectors, and failure-atomic project path/history handling.
- Release integration: installed startup checks require the new controls and analyze
  their actual exported WAV. Four-platform candidate tests now include these features;
  Python-fallback CI also tests scalable tracks. Existing performance gates are unchanged.

See [USAGE.md](USAGE.md) for controls and limitations, and
[daw-progress.json](daw-progress.json) for per-requirement evidence and remaining work.

## Verification

All GUI checks use disposable offscreen Qt windows or an isolated headless browser;
the active chat is untouched. Synthetic audio is used; no physical input device is opened.

- Full source regression: **1,455 passed, 5 skipped**, 557.38 seconds, peak RSS
  589.1 MiB; `/tmp/anharmonic-capability-full-tests.xml` and
  `/tmp/anharmonic-capability-full-tests.json`. The later project-transaction tests
  and final rack binding cases were separately covered by the final integration run.
- Final integration/recovery/import/source checks: **78 passed**, report
  `/tmp/anharmonic-capability-final-integration.xml`.
- Python DSP fallback, including scalable tracks: **91 passed**, 24.17 seconds,
  peak RSS 212.5 MiB; `/tmp/anharmonic-capability-fallback.json`.
- Browser UI: **73 checks passed**, `/tmp/anharmonic-capability-web/report.json`.
- Actual browser audio: **30 checks passed**, `/tmp/anharmonic-capability-audio.json`.
- Browser model/interchange: both Node test files passed, including new 128-track,
  high-route, overflow, stable-ID and atomic Undo/Redo cases.
- Actual startup self-check passed with **zero audio devices opened**, actual
  subprocess export, analyzer report, project roundtrip and UI lifetime checks:
  `/tmp/anharmonic-capability-self-check.json`.
- Ruff lint/format, Python compilation, website references and reproducible source
  import `--check` passed.
- Local 48 kHz / 512-frame callback smoke: piano p99 **1.186 ms**, mixed p99
  **2.785 ms**, no over-budget blocks in either 187-block run;
  `/tmp/anharmonic-capability-callback.json`. This is short device-independent
  evidence, not hardware or Intel acceptance.

Temporary report paths are local evidence, not immutable cross-platform release
attestations. CI must repeat checks on the resulting commit before qualification.

## Not released

The complete 194-item backlog is **not finished**. The next P0 foundations include
independent multi-instrument instances, project sample-rate support, physical
input/output layouts, tempo/meter maps, and aligned multitrack recording.

At the repaired baseline, Windows, Linux and Apple Silicon completed candidate
validation; Intel Mac still failed its ten-minute 512-frame timing gate (wall p99
16.613 ms and thread CPU p99 16.144 ms against a 10.667 ms callback budget).
That result is not qualification of this newer branch. Four-platform binaries,
real installation/audio-device acceptance and source/checksum binding remain required.

Packages remain **unsigned**, with [installation advice](../packaging/INSTALLATION.txt).
No signing purchase, public installer upload or Cloudflare promotion occurred.
Live downloads must stay unchanged until the complete selected release passes its gates.
