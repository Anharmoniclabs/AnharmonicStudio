# Anharmonic Studio — Current release status

This file is the short, current-state release gate. `RELEASE_AUDIT.md` preserves historical checkpoints and evidence; it should not be read as a list of only-current defects.

Updated: 2026-09-11

## Current product state

- Native desktop source supports Linux, Windows, Intel Mac and Apple Silicon Mac packaging paths.
- The standalone browser studio is functional but is not a bit-identical port of native DSP and does not host native desktop plugins.
- Official distribution policy is unsigned packages with explicit installation guidance and checksums.
- The public source remains GPL-3.0-or-later.
- Current development contains fixes newer than the existing `0.1.0-rc.1` installer set. A new immutable candidate set must therefore be built before release qualification.

## Software gates

The following must be green on the exact source commit used for a release candidate:

- [ ] Source Checks workflow: browser regressions, Chromium workflow/audio checks, native/full Python tests, fallback DSP, callback benchmarks and Linux bundle.
- [ ] Portable C++ Engine workflow.
- [ ] Four native candidate builds from one source commit.
- [ ] Matching source archive, third-party notices and checksums.
- [ ] Browser unsupported-processing checks remain explicit; unsupported active native processing must never silently disappear from playback/export.
- [ ] Candidate catalog contains one matching version/source identity across every platform.

Passing a build job alone is not production acceptance.

## External acceptance still required

These checks require real target machines, hardware, installed third-party software, or user-facing delivery infrastructure and cannot be replaced by unit tests:

- [ ] Windows x86_64 clean install, update and uninstall acceptance.
- [ ] Linux x86_64 clean install, update and uninstall acceptance.
- [ ] Intel Mac clean install plus sustained realtime-performance qualification.
- [ ] Apple Silicon Mac clean install, update and uninstall acceptance.
- [ ] Real audio-interface input/output, switching, recording and round-trip behavior.
- [ ] Real iPhone/iPad Safari import, playback and microphone acceptance for the web studio.
- [ ] Real MIDI controller/external-synth acceptance for supported MIDI workflows.
- [ ] Broad trusted third-party plugin compatibility sampling on supported hosts/platforms.
- [ ] Staged Stripe/Cloudflare paid-access, expired/unpaid denial, download hashes and source-link verification.

The detailed evidence format is in `packaging/ACCEPTANCE.md`.

## Known capability boundaries

These are not necessarily bugs; they are current product limits that must stay honestly documented until implemented and qualified:

- Native plugin editor windows are not complete.
- MIDI output/clock and full recorded MPE/expression workflow are not release-qualified.
- CLAP, VST2 and LV2 are not native load formats in the current host.
- Browser/native full DSP parity is not claimed.
- Browser-native plugin hosting is not implemented.
- Mastering analysis is not yet a complete calibrated LUFS/LRA/true-peak delivery suite.
- Physical-device acceptance remains a separate gate from CI.

## Bug-bounty hardening campaign

The durable implementation queue and continuation instructions live in `BUG_BOUNTY_CONTINUATION.md`.

When that campaign changes a release-critical behavior, update this file only with claims supported by merged code plus required evidence. Do not pre-mark external acceptance as passed.

## Promotion rule

Promote a release only when:

1. one immutable source commit has passed all required software gates;
2. all four candidate installers were built from that commit;
3. matching public source/notices/checksums exist;
4. required real-platform/device acceptance reports are nonempty and passing;
5. staged delivery/access checks pass; and
6. the owner intentionally promotes that reviewed candidate set.

Until all six are true, describe the build as development or release-candidate software, not production-qualified.
