# Release audit — 2026-09-10

Baseline: `0dd09d21077211513a56d0ee3368e847db8d172f` (merged web DAW).
Working source: `/home/al/Projects/mpc-lab`. The separate
`AnharmonicStudio` checkout is older (`01900b4`). Preserve local projects,
libraries, and the untracked `workflow-bindings.json`.

## Publication gate

Do not promote new Cloudflare downloads until the repaired commit passes source,
browser behavior, native packaging, and release acceptance checks. A build or
static HTML validation alone does not establish browser/desktop parity.

## Findings

| Area | Evidence at baseline | Required outcome |
| --- | --- | --- |
| Desktop CI | Run 34495499176: 4 failed, 1215 passed, 5 skipped | Repair failures, rerun full suite |
| Automation | First timer tick clears an already-started latch gesture | Preserve latch through release until Stop |
| Shared engine | Offline renderer eagerly imports live plugin host | Keep shared imports independent of runtime/device setup |
| Recording UI | Existing Recording menu wrapper becomes invalid during lookup | Retain menu ownership and attach commands once |
| Plugin UI test | Checks string membership in tuple of CommandSpec objects | Assert registered IDs and callable commands |
| Website gate | Pages succeeded while Source checks failed on same SHA | Require source and browser checks before deployment |
| Browser entry points | `app/index.html` and `app/studio.html` load distinct implementations | One canonical application |
| Browser audio | Export synthesizes fixed drum hits; live sample playback ignores trim/pitch/pan/gain | Shared render path and measured audio regressions |
| Browser transport | Fixed 16-step interval, no song/notes scheduling; mode only changes status | Schedule actual project patterns, notes, and arrangement |
| Browser controls | Placeholder pitch analysis, effects menus, sample library, chopping, and other controls | Implement and test functionality before claiming parity |
| Browser persistence | Zero values overwritten; current_pattern ignored; source metadata discarded from notes/clips | Validate input and preserve desktop project semantics |
| Native synth | Web Audio receives unsupported oscillator type `saw` | Map desktop oscillator names to browser types |
| Packaged self-check | Startup check constructs the base window without production feature attachments | Use the production installation path and assert registered commands |
| Delivery | Configured source SHA is `9adf246506f5648b46ff871783d4bb7fb9d9a3c8` | New immutable release, matching source/notices/checksums for all four platforms |
| Attribution | Three github-actions formatting commits and one Copilot merge commit | Preserve truthful attribution; review history changes separately |

## Commit provenance

The recent desktop fixes are already in main's history, including engine module
extraction, stable mixer identities, MIDI devices, recording/comping, plugin
chains, and automation modes. They must be built together from one tested SHA.

Bot-authored commits reachable from main:

- `c3fb398`: Format premium workflow sources [autoformat]
- `abfe870`: Format routing and PDC tranche [autoformat]
- `d146296`: Format loop and punch recording workflow [autoformat]
- `b7d620b`: Merge origin/main into feature/automation-modes

No current checked-in workflow creates commits. Removing historical contributors
is not the same as disabling automation; rewriting published ancestry changes
commit IDs and needs a reviewed migration. Do not silently force-push history or
change historical author identities without an explicit migration decision.

## Acceptance still required

Real browser interactions and audio assertions; desktop full/fallback suites;
cross-platform release regressions including the newly added features; Windows,
Linux, Intel Mac and Apple Silicon packaging; unsigned-installation guidance and
physical audio-interface acceptance for the intended release standard;
matching public source and notices; staged private-download hash verification.

Owner policy clarification: official packages will remain unsigned because of
signing costs. Windows signing and Apple notarization are not publication gates.
All four targets must clearly disclose this and include installation guidance;
software, physical-device, source and checksum checks remain separate requirements.

Browser-native plugin hosting and the desktop's full DSP are not implemented by
the existing JavaScript application. This audit does not certify enterprise
readiness or one-to-one parity.

## Repair validation

The repair branch fixes the four failing desktop checks and isolates the two new
UI test modules from physical audio devices. Full desktop result: **1219 passed,
5 skipped**, 520.79 seconds, peak test RSS 631.9 MiB. Separately, the new release
provenance regression passed, and the Python DSP fallback suite passed **67 tests**.
Lint and formatting passed. Native DSP compilation and the application's
offscreen self-check passed, including the real export subprocess, project
roundtrip, FFmpeg, isolated plugin processing, and UI lifetime.

Four browser project regression tests passed. An isolated Chromium session
visited every workspace and verified project rejection/preservation checks and
the synth's actual sawtooth/square oscillator types. This smoke check does not
validate the unimplemented audio and UI features listed above.

Release workflow changes require source/browser checks before Pages and native
candidates, include recent feature tests on every native platform, and allow a
new candidate version. Packaging rejects modified or untracked source inputs
and verifies the commit again before promoting its local output directory.
No Cloudflare catalog, bucket, live website, or historical Git author was changed.

The first local Linux candidate at commit `aa67f18` passed packaging, relocation,
temporary installation, and installed export. It is a development-machine build
(glibc 2.44), not the portable Ubuntu release. Its check exposed a coverage gap:
the packaged self-check did not attach the production feature controllers. The
normal launcher and self-check now share `application_features.py`; the expanded
self-check verifies plugin-chain, recording/comping, and automation commands.
The expanded self-check and 39 related regressions passed. A new build must use
this expanded check before it can replace that first candidate.

## Browser architecture decision

Both routes require rebuilding the browser UI to match the supplied desktop
screenshot and a control-by-control acceptance matrix. The choice affects the
audio implementation and the user's installation requirements:

- **Desktop-connected browser:** use the installed DAW as the audio/project
  authority through an authenticated local bridge. Local libraries, native
  plugins and current DSP stay available. Requires installing and running the
  companion DAW; cannot work as a standalone public static website. The bridge
  must bind locally, require explicit pairing, validate origin and commands,
  and preserve the desktop's project/thread ownership.
- **Standalone browser:** implement browser-supported DSP, scheduling, media
  storage and project exchange, with equivalent native code ported to WebAssembly
  where feasible. Requires substantial porting and audio equivalence tests;
  native VST3/LV2 binaries cannot simply be loaded by the current JavaScript app.
  Cloudflare's static hosting/download worker does not provide the desktop
  audio engine or local sample library.

Work is proceeding on the standalone browser route, as described to the owner.
Native-only processors must be disclosed and must not silently disappear from
exports. This is not a claim of bit-identical browser/native DSP. Cloudflare
promotion remains on hold pending final acceptance.

## Continued release preparation

- Browser project normalization now validates nested numeric/index fields and
  stable identities, preserves unknown desktop metadata, and bounds document
  complexity/history memory. Ten model regressions pass; all 83 saved desktop
  project JSON files present during this check loaded read-only. User songs were
  not edited.
- Catalog preparation now validates all four platforms before emitting output,
  rejects mixed source/version/checksums, binds reports to checked artifacts,
  and separates candidate preparation from production acceptance. The explicit
  production policy is **unsigned**, with checksum-covered installation guidance
  and owner-reviewed software, device, and source evidence.
- Run `34504318439`, built from the pre-history-rewrite commit `04f4b2f`, passed
  all preflight checks and Windows, Linux and Apple Silicon native candidates.
  Intel Mac failed the ten-minute stress gate: p99 callback wall time 17.04 ms
  against a 10.67 ms deadline (thread CPU p99 14.14 ms). This is a blocking
  performance result, not a reason to weaken the gate.
- Another session rewrote local Git history during this work. The baseline main
  commit changed from `f38f83b` to `e07bd34`, with the identical tree
  `bfb8745949cab1683013910c40ad156140a04156`. Main now has owner attribution.
  This audit did not perform that rewrite. Old run SHAs remain historical
  evidence; new candidates must identify their actual new committed source.
- A separate, actively changing native rewrite remains outside this release
  integration tree. Do not absorb its unfinished work or claim its platform
  validation applies to the repaired engine on main.

The continued desktop validation passed **1270 tests, 5 skipped** in 525.52
seconds at 633.2 MiB peak RSS. Python DSP fallback passed **67 tests**, and the
production startup/export self-check again passed with zero audio devices opened.
New focused installation, catalog, profiler and project-interchange safeguards
were tested separately after the full suite collected its tests.

The shared browser audio graph passed **24 actual Chromium audio regressions**,
including sample PCM trim/reverse/pitch/pan/gain, routing/mute/solo, native swing
timing, gate/choke/loop crossfades, tempo changes, effect export, and missing-media
and unsupported-processing rejection. These do not certify bit-identical native
DSP or physical-device performance. **60 headless UI checks passed with zero
browser exceptions**, covering file import, editing,
portable export/reopen in a fresh browser context, generated microphone fixtures,
and audible song export, plus recording recovery and mobile controls. Two native
and two JavaScript interchange tests verify the same shared fixture; a generated
browser export also preserved 1,400 shared scalar fields through the native
project loader. Browser-only media storage and master effects are not native
project processing and remain explicitly documented.
