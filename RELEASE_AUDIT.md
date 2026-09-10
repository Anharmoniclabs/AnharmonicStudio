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
falsify authorship.

## Acceptance still required

Real browser interactions and audio assertions; desktop full/fallback suites;
cross-platform release regressions including the newly added features; Windows,
Linux, Intel Mac and Apple Silicon packaging; signing/notarization and physical
audio-interface acceptance where required for the intended release standard;
matching public source and notices; staged private-download hash verification.

Browser-native plugin hosting and the desktop's full DSP are not implemented by
the existing JavaScript application. This audit does not certify enterprise
readiness or one-to-one parity.
