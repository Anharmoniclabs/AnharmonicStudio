# Improvement backlog

## Autotune display — September 9

Fixed crunched pitch lanes and overlapping note labels with a minimum semitone
height, centered fit, vertical pitch scrolling and pitch zoom/fit controls.
17 focused vocal tests passed; previews: `docs/previews/autotune-readable/`.
Keep pitch spacing readable in future density changes. Very narrow workspaces
with both sidebars open still require scrolling to reach all tuning controls.

## Owner-directed pads workspace — September 9

Completed pads-only sidebar, contextual Song recording/routing strip, fixed
top output/input/DSP meters and supplied owner logo. Evidence and limits:
[workspace note](../PADS_WORKSPACE_2026-09-09.md). Preserve pad visibility and
selection when selecting/arming tracks. Physical recording validation remains
pending; do not mistake sample-peak/RMS telemetry for true-peak or LUFS metering.

## Continuous whole-DAW refinement — September 8 overnight

The owner requests continual passes, one after another, covering every application
path/function/use over time. Rotate through [the coverage ledger](CONTINUOUS_WORKFLOW.md),
complete useful slices from [the full DAW roadmap](../PRODUCER_DAW_ROADMAP_2026-09-08.md),
and carry unfinished prerequisites forward. The older ranked sampling/preset table
below is context, not a reason to neglect recording, MIDI, plugins or other areas.

Initial implementation targets by rotation:

- Recovery/recording: durable take spools, retained failure data, dropout frame positions.
- Tracks/engine: independent instruments, persisted IDs, bounded native rendering;
  first separate native render construction from physical device initialization.
- MIDI: timestamped event ownership and a complete input → Notes → saved/exported
  performance slice; use simulated devices until physical validation is available.
- Plugins/routing: isolated discovery/validation, complete lifecycle/state and typed
  parameters; native VST3 remains a requested milestone, with current LV2 groundwork.
- Sound design: analog cutoff numeric control and correct grouped undo, then expressive
  orchestra dynamics/loops and modulation with actual rendered evidence.
- Arrangement/sampling: note chase, reversible in-beat sound audition, stretch preparation,
  precise fades and transitions; preserve existing off-grid and four-bar workflows.
- Mixing/delivery: parameter ownership/smoothing, coherent bus routing, aligned stems,
  export/error/cancel checks and persisted automation.
- UI/accessibility: keyboard paths through notes/regions/controls, semantic access,
  narrow/high-DPI layouts and exact values with undo/reset/cancel.

First resolve reproducible existing quality-gate failures if they prevent integrating
any useful work. Update this section and the ledger with the actual changed paths,
functions, uses, evidence and next gap. Do not report untested paths as covered.

## Owner-directed workspace — September 8

Implemented the single-row shell, full-height center, optional side panels,
project-selected accents, and direct audio/on-screen note recording into armed
Song tracks. See [workspace implementation](../WORKSPACE_2026-09-08.md).
Next recording acceptance: physical input/monitoring validation, durable failed
take recovery, then independent instrument instances and multichannel/MIDI
recording. Do not restore fixed-blue styling or repeated navigation rows.

Seeded 2026-09-05 from the current code/reports and an actual offscreen Studio screenshot. Revalidate each candidate before work; the application has changed substantially since older audits.

| Priority | Candidate | First bounded result / acceptance |
|---|---|---|
| 0 | Distinctive orchestral and hybrid instruments | Implemented 13 recorded articulations and 10 original layered patches with dynamics, short-note alternate takes, looping sustain, reverse, tone, width and motion controls. See [palette and evidence](ORCHESTRAL_PALETTE_2026-09-05.md). Next: audition realistic musical phrases, improve authored sustain loops and dynamic transitions, then a bounded expression/controller slice and prepared tempo-synchronized movement. Preserve recorded character, asynchronous loading and callback/export parity. Do not describe the native bank as external VST hosting or Omnisphere parity. |
| 0 | Four-bar sampling and producer workflows | Four-bar range → unused pads → editable pattern → existing song is implemented, with tempo-following repitch, grouped undo and restored detailed Crates. Next: pitch-preserving prepared stretching and alternate A/B or house vocal-hook patterns, guided by timestamped producer transcript evidence. See [workflow and limits](FOUR_BAR_SAMPLING_2026-09-05.md). Profile long-song UI work; retain the illustrated crates as the default. |
| 1 | Precise, consistent pad and sound controls | Pitch direct entry/fine keys/reset/Escape/grouped undo is complete in the September 5 release workflow pass. Pan now has direct percentage entry, 0.1% steps, center reset, Escape and grouped undo, verified through persistence and callback/export. Gain, attack, release and loop-crossfade sliders now capture the pre-edit sound and group each drag into one undo step, with keyboard edits and saved history verified. Gain now has direct dB entry, 0.1 dB numeric steps, unity reset, exact mute, Escape and persisted undo, while retaining its 0–4 linear slider. Numeric steps land on the -120 dB floor before mute. Attack, release and loop crossfade now have direct 0.1 ms entry, default reset, Escape, saved precision and persisted undo; repeated loop wraps now blend the correct slice head. Next: a bounded producer-guided A/B phrase variation or expression/controller slice, preserving the completed inspector controls. |
| 2 | Groove editing with intention | Improve one verified gap in velocity, note length, microtiming, or quantize workflow. Preserve deliberately off-grid material and give one clear reversible adjustment. |
| 3 | Sampler flow and articulation | Verify loop crossfade, trim feedback, choke/retrigger, snap bypass, and mapping continuity; fix the most reproducible audible/interaction defect. |
| 4 | Visual hierarchy and editing space | The September 5 pass flattened the theme, condensed category/navigation controls and replaced instrument cards with searchable rows. Next review mixer/advanced sound-design density and numeric-control consistency at narrow widths. Continue actual dark/light renders and keyboard checks. |
| 5 | Parameter smoothing and automation control | Identify a measurable discontinuity or a confusing manual/read transition; fix state and signal behavior together. Extend beyond gain/pan only as a complete supported parameter slice. |
| 6 | Transport and musical timeline | Live keyboard/arp and sequenced synth notes now have explicit ownership: same-pitch retriggers/key releases preserve the other source, and stop/seek release sequenced voices while live performance continues. Synthetic callback regressions verify unchanged backing sustain and arp audio. Next: note chasing and held-note behavior across song loops/arrangement boundaries before tempo/meter maps or stretching; retain the existing global polyphony cap. |
| 7 | Session portability and recovery | Inspect current save/history/media behavior. Complete one collect/relink/recovery task with synthetic assets and failure coverage. |
| 8 | Native render ownership | Profile the actual callback. Move one measured allocation/processing bottleneck behind a prepared native boundary with reference parity; do not declare the entire engine real-time safe. First-note NumPy random imports/file reads now happen during synth module initialization, with fresh-interpreter keyboard/pattern/arp regression coverage. See `2026-09-06-synth-startup` evidence; seeded audio is unchanged. Next: profile remaining per-note generator/state allocations and first-use sampled-voice preparation separately. Keep measuring cold callbacks as well as steady state; this removes one startup hazard and does not certify callback deadlines. |
| 9 | Expressive external instruments | Plan/implement a bounded MIDI input/state slice before sustain, CC, pitch bend, mapping, multiple instruments, and expression. Hardware work stays pending when devices are unavailable. |
| 10 | Routing, delivery, and Linux release | Complete a clean-install/launcher/package acceptance slice before calling the application release-ready; verify UI glyph/assets inclusion and missing-device behavior. Buses/sidechains, stems/dither/metering and isolated LV2/CLAP hosting remain separate architecture tasks. |

Completed in the [September 5 workflow pass](RELEASE_WORKFLOW_2026-09-05.md): non-overlapping pattern append and reveal, double-click pattern editing, Ctrl+F sample discovery, composed instrument search with keyboard preview, restrained dark/light control styling, and precise pad pitch. Do not repeat these as audit-only work; extend from the recorded evidence.

Move completed work into `MEMORY.md` with evidence and update the next acceptance target. Add discovered issues with a reproducible trigger. The list is a priority guide, not authorization to fabricate features or to repeat already-completed work.


## Sample/Notes workflow follow-up — 2026-09-07

The sample-workflow PR implements explicit sample destinations using the existing
64 pad slots, Notes channel selection, Beats/header/footer and tab drops, source
root/mono controls, recording/release ownership, swing-preserving step conversion,
format-3 persistence and export. See `../SAMPLE_WORKFLOW_2026-09.md` and its focused
test suite; do not rebuild these from the earlier audit. Next: glide and release
quality; confidence-scored root detection; independent synth instances/general
channel IDs; reversible in-beat sound audition; collect/relink and asynchronous
media preparation. No external plugins or independent synth instances are claimed.
