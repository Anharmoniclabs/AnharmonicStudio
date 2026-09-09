# Development memory

## Autotune pitch readability — September 9

- Owner screenshot exposed overlapping note labels: several octaves were
  compressed into roughly 140 vertical pixels. VocalPitchView now reserves a
  taller pitch canvas and at least 18 pixels per semitone. Auto-fit centers the
  main vocal when octave outliers cannot fit; a vertical scrollbar and pitch
  zoom/fit controls reach the rest without modifying detected pitch or tuning.
- Wheel over notes scrolls pitch; wheel over the waveform still zooms time.
  Existing time-selection and vocal-processing tests remain passing: 17 tests
  in `test_vocal_workspace.py` and `test_vocal_panel.py`, including wide-range
  regression coverage. Offscreen synthetic previews are in
  `docs/previews/autotune-readable/` (dark, light, narrow).
- At 960 pixels with both sidebars open, the existing workspace still needs
  horizontal scrolling; pitch lanes remain readable. The taller editor uses
  the existing vertical workspace scroll to reach lower controls. Live app,
  audio devices and user recordings were untouched.

## Owner-directed pads workspace — September 9

- Removed the Track/Pads switch: pads retain their sidebar and selection.
  Song track controls now live in a collapsible timeline strip with expandable
  input settings; capture/routing/recovery handlers are preserved.
- Added fixed top stereo RMS, held output peak, active recording input and DSP
  meters using existing telemetry. Bundled the owner's supplied Downloads A
  logo unchanged for the header and application identity.
- 70 initial and 85 subsequent related tests passed, with offscreen dark/light,
  normal/narrow renders. See `../PADS_WORKSPACE_2026-09-09.md` and its previews.
  The running DAW, devices and personal projects were not touched.
- Remaining: physical input/output validation, as before. These meters do not
  claim true-peak/LUFS measurement. Preserve the pads-only sidebar in later work.

## Continuous whole-DAW refinement setup — September 8 overnight

- Owner corrected the proposed twice-daily cadence: run passes continually, one
  after another across the complete studio's paths, functions and musician uses.
  DIRECTION/BACKLOG and `CONTINUOUS_WORKFLOW.md` now record that steering and the
  eight-area coverage ledger. The earlier roadmap cadence is superseded.
- Controller now supports `run --continuous`, retaining one lock across the loop,
  immediately starting a new pass after verified integration, persisting focus
  rotation and using a 60-second pause-aware backoff for held work. Manual pause
  prevents integration and further work without killing any process. Three
  consecutive failures still pause for inspection.
- Repaired the documented permission mismatch under service `UMask=0077`:
  existing source permissions survive editor replacements and application;
  new files retain verified candidate permissions/executable bits. Native C++
  source/configuration is now included in snapshots, generated builds excluded,
  and a separate CMake/device-free ABI gate supplements the existing DSP build.
- Controller verification: 35 disposable tests passed, Ruff lint/format and diff
  checks passed. Dedicated service/timer definitions passed systemd validation.
  The service is configured for continuous execution; the timer starts it at
  22:00 local time if not already active. Starting the loop is recorded separately.
- Previous failed run was reconciled against current source without replaying a
  stale patch. Existing completed content and subsequent user work are retained.
  See `evidence/2026-09-08-continuous-setup/previous-run-reconciliation.json`.
  The historical post-apply mode failures are addressed by this controller repair;
  do not spend the first new application pass repeating that old investigation.
- Made the native-loader unavailable test deterministic by removing candidate
  libraries in its fixture; it cannot accidentally construct a real device backend.
  Repaired four pre-existing Ruff formatting failures with before/after AST equality
  checks. Isolated software baseline evidence is being collected in the setup
  evidence directory; inspect its completed results before starting the worker.
- First application pass should address recording/recovery, including durable
  take storage and accurate dropout timing, unless current verified evidence
  establishes a more urgent session/audio defect. Hardware remains unvalidated.

## Reference workspace and direct track capture — 2026-09-08 interactive pass

- Fetched origin and verified the starting checkout matched `da6d3a0`, after
  today's UI restorations. Implemented the owner's image direction using the
  existing Qt editors: one navigation row, full-height central editor, optional
  side panels, no fixed bottom dock, dark reference surfaces and project-selected
  accents. COLOR stays at the top right, including narrow/focused windows.
- All navigation routes keep Studio active and its live editors in their
  persistent owners. Removed redundant mode/tool rows and moved master/take/
  automation utilities into Tools. The inspector follows explicit track or pad
  selection; this is required for numeric pad keyboard focus and undo/redo.
- Added direct audio and on-screen synth/sample-note capture into an armed Song
  row. Captures pin row identity and recording settings, preserve off-grid
  notes, create one completed-take undo step, compensate configured input delay,
  retain failed media saves for retry, and guard project replacement/tempo/seek.
  Per-row source/audio routing persist; arming remains transient. Uses the
  existing mono PCM24 recorder and note/audio playback/export paths.
- Focused evidence: 110 navigation/editing tests passed; 13 capture tests passed,
  including audible WAV output through the chosen mixer channel, save/reopen,
  undo/redo, failed save/retry, cancelled count-in, input failure and note timing.
  Python fallback capture/selected-sample checks: 28 passed. The broad run exposed
  six pad keyboard-focus regressions caused by the new inspector's initial page;
  selecting a pad now selects its inspector, and all six focused regressions
  passed without weakening their original assertions. Final broad results below.
- Actual Qt receipts: `docs/previews/workspace-2026-09-08/`, generated by
  `scripts/render_reference_workspace.py` with temporary synthetic media,
  in-memory settings, offscreen Qt and disabled device startup. Inspected dark
  amber/purple, light teal, 960px Song, 1440px Focus, Sampler, Notes and Mix.
- Concurrent clipboard, selected-sample, sampler and DSP edits appeared during
  this pass and were preserved. Validation snapshots must be distinguished from
  later edits in the shared checkout. No commits, pushes, live app restart,
  hardware input access or changes to user media were performed.
- Limits/next acceptance: physical input and monitoring checks, durable failed
  take recovery, independent synth instances, multichannel/MIDI capture. The
  current synth patch and pad identities remain shared. Details and owner
  workflow: `docs/WORKSPACE_2026-09-08.md`.

## Recorded orchestra and distinctive hybrids — 2026-09-05 interactive pass

- Owner wants orchestral instruments and less generic sounds, using Omnisphere as a breadth/texture reference. Added actual recorded sources to native Instruments: **13 articulations and 10 original hybrid presets**, with 181 CC0 VSCO-2-CE recordings, five root pitches per articulation, retained selected velocity layers and alternate takes, about 62 MiB lossless FLAC. Official SFZ numeric pitch centers avoid inconsistent filename octave conventions. Pinned upstream revision, per-file hashes, original mappings and full CC0 dedication are bundled; installer is explicit, verified and never called from the runtime. See [sources, user workflow, palette and limitations](ORCHESTRAL_PALETTE_2026-09-05.md).
- Immutable prepared multisamples play with pitch interpolation, crossfaded sustain, recorded short-note dynamics and per-note round-robin takes. Hybrids layer contrasting sources with octave offsets, brightness, stereo width, amplitude motion and reverse. Shared callback/export voice rendering preserves timbre and timing. An initial existing factory-audition assertion exposed excessively quiet reversed tails; trimming only the reverse playback span to audible recorded content fixed it without weakening the test or changing original forward playback.
- Browser prioritizes new categories and searches descriptions. Background preparation keeps the previous instrument usable, discards stale selections/projects, and preserves music/history on failure. Sampled macro drags group into one undo; source and controls persist through save/reopen/history. Project loads preflight missing recordings before replacing current music. Preview waits for the selected sound and guards stale release timers. Unsupported analog oscillator displays are hidden for sampled sounds. Detailed Crates and four-bar sampling remain intact.
- Focused validation: **92 passed** across orchestra, factory workflow, sampler mapping and project corruption. Full quality suite: **700 passed, 3 hardware skipped** in 175.84 seconds; lock/native build/lint/format/compile gates passed. New regressions cover asset/root mapping, dynamics/takes, block-continuous forward/reverse loops, sustain/release, audible macros, callback file-read exclusion, FLOAT export parity, async races/failure, project-load preservation, grouped history and invalid fields. Logs: `docs/agent/evidence/2026-09-05-orchestra/`.
- Actual dark/light Qt renders at 1440×900 and 1000×900 inspected: recorded/hybrid controls fit, descriptions wrap, detailed Crates retained. Evidence renderer uses temporary settings/library, engine startup disabled and offscreen Qt. The audio reel compares the previous Solar Strings with twelve orchestral/hybrid examples; individual WAVs and cue times are included. Audio is measured/rendered, not claimed to have received a listening-panel assessment.
- Dedicated eight-voice sample benchmark: 48 kHz/512 frames, 750 callbacks per patch. Chamber Violins p99 **2.87 ms**, worst **2.95 ms**; Silk Pulse p99 **3.70 ms**, worst **3.93 ms**; zero measured deadline misses against 10.67 ms. These are short software measurements, not hardware latency certification.
- Limits and next work: compact source coverage, interpolated pitches and generated loops; no recorded legato transitions, continuous dynamic crossfade, tempo-synced movement, independent simultaneous instrument tracks or external VST hosting. Improve authored loops, phrase audition and expression before proliferating presets. No commercial Omnisphere content or parity claim. The active DAW/chat and user projects were preserved; changes appear at the next normal app launch.
- Final validation: full `scripts/quality.sh` exited 0, including software benchmark and soak gates. Mixed production workload p99 **3.95 ms**, worst **4.04 ms**, zero over-budget blocks; two-second soak **2445 iterations, zero late callbacks**. `MPC_NATIVE_DSP=0` synth/engine/workstation/orchestra regression: **64 passed** (`fallback.log`). The hourly controller was paused to avoid concurrent integration and **resumed after validation**, with status confirming `paused: false`. Its gain-control candidate `20260905T220055.512610Z` remains held with evidence preserved because the source evolved during this interactive pass. Do not blindly apply that stale candidate over current work.

## Detailed Crates and four-bar sampling — 2026-09-05 interactive pass

- Owner re-prioritized the detailed record-crate identity, ongoing responsiveness/precision improvements, and hip-hop phrase sampling informed by actual FL Studio trap/house producer transcripts. Restored the retained crate atlas to the default browser; CRATES toggles compact mode. Updated DIRECTION and BACKLOG so future cleanup preserves this identity. No user source work was stashed/reset/committed.
- Added a selected four-bar 4/4 phrase → 4/8/16 unused pads → new editable four-bar pattern → existing song placement action. A source-BPM control selects 16 beats from the exact chosen start; manual selection can establish the four-bar range directly. Shared frame boundaries prevent cumulative slice-rounding drift. Occupied pads and empty pads with existing pattern events are reserved; a full bank failure leaves music/history untouched. Song placement appends on the selected clip's lane, with one undo for the whole action and history after reopen.
- Pads persist `sync_beats`; callback and offline export repitch to that beat duration at project tempo, with normal pad pitch applied additionally. New TEMPO inspector supports free playback or a beat length, undo and stale-control retirement. Remapping clears sync. This is vinyl-style repitch, not pitch-preserving stretch. Existing free pads retain their original playback calculation.
- Auto-chop searches repeating four-bar phrases before shorter candidates and accepts two occurrences. The original one-bar detector test now explicitly requests one bar and keeps its assertions; a new test covers the four-bar default. Stereo averaging moved into the scan worker. Switching source while scanning cannot auto-map the other sample; replacing the project discards the result.
- [Research, exact sources, use cases and limitations](FOUR_BAR_SAMPLING_2026-09-05.md): accessible Navie D trap transcript; Yanick house transcript opening (full transcript unavailable); Niek's creator-written house companion; Image-Line's Slicex beat-count/downbeat selection documentation. These informed editable phrases and preserved musical intent, not automated imitation of a particular artist.
- Full `QT_QPA_PLATFORM=offscreen UV_OFFLINE=1 bash scripts/quality.sh` passed: **578 passed, 3 hardware skipped**, lock/native/lint/format/compile gates passed. Afterward, a final same-pad control-retirement guard and pre-paint atlas cache initialization received additional focused/fallback validation, recorded in `final-fallback.log`. Audio regression uses synthetic 220 Hz stereo at 48 kHz, 512-frame callbacks, 90/120/150 BPM, all 16 beats audible, precise repitch agreement and full four-bar FLOAT WAV length.
- Software mixed production benchmark: 48 kHz/512 frames, 64 blocks, 8-voice limit and inserts; p99 **3.86 ms**, worst **3.93 ms**, zero measured steady-state over-budget blocks. Two-second soak: **2880 iterations, zero late callbacks**. These are software checks, not physical latency certification.
- Inspected actual Qt sampling layouts in dark/light at 1440×900 and 1000×900, plus arranged-phrase view. Restored detailed crates and phrase actions fit; existing transport/trim toolbars scroll horizontally at narrow width. Evidence and reproducible isolated renderer: `docs/agent/evidence/2026-09-05-four-bar-sampling/`, `scripts/render_phrase_preview.py`. No desktop window or audio stream launched.
- Final validation: **82 passed** with `MPC_NATIVE_DSP=0`, covering DSP fallback, the complete new phrase workflow and Crates behavior, including same-pad control retirement. Final lint/format/compile gates also passed. `final-fallback.log` contains the result.
- The existing hourly timer was verified active. Its controller was paused only to avoid concurrent integration during this interactive edit and **resumed after final validation**. Source changes become visible on the next normal app launch; the active DAW/chat were preserved. Next: prepared pitch-preserving stretching, A/B flips and vocal-hook variants, downbeat/half-time correction, and measured long-song UI stalls.

## Setup — 2026-09-05

- Owner wants sustained refinement, creative control, completeness, distinctive UI, and Linux excellence.
- Source: `/home/al/Projects/mpc-lab`; visible name Anharmonic Studio; Python/Qt/NumPy/PortAudio with selected native C kernels.
- The checkout contains extensive uncommitted work, including new application files. The controller must snapshot the current files, including untracked source, rather than use HEAD as the application baseline. Never stash/reset/commit the owner's checkout.
- Existing functionality includes pad sampling/chopping, synth and arpeggiator, pattern notes/piano roll, arrangement, gain/pan automation, vocal takes/comping, recovery, native DSP kernels, independent streaming export, and Studio/Crates UI. Verify code before calling any of these missing.
- Inspected actual `docs/previews/studio-crates.png`: recognizable graphite/amber palette, Studio arrangement over channel rack, left browser, right pads/inspector. Several pad names wrap or truncate; many compact labels and horizontal parameter sliders deserve interaction review. This is a visual observation, not a completed usability study.
- The curated research library and acceptance rubric separate public source evidence from our design judgments. No commercial books were purchased or represented as fully read.
- First intended cycle: verify and improve a precise-control gap in the pad/sound inspector, or choose a more important reproducible defect if the current code already solves it.
- Setup baseline validation: `QT_QPA_PLATFORM=offscreen UV_OFFLINE=1 bash scripts/quality.sh` passed with **373 tests passed, 3 hardware tests skipped**. Build, lint, formatting, compilation and software DSP smoke gates passed. The mixed 48 kHz/512-frame smoke case had p99 2.97 ms and no deadline misses; the two-second callback soak had no late callbacks. These are short software checks, not hardware latency certification.
- The subsequent controller validation copied the then-current source and passed **377 DAW tests, 35 Python DSP fallback tests**, with 3 hardware tests skipped, plus all independent build/style/audio gates. Test counts changed as the source evolved during setup. Evidence: `~/.local/state/anharmonic-studio-agent/validation/quality.log`. The controller's separate temporary-repository suite passed 13 tests.

Append compact entries with: task, musician benefit, source reference, changed behavior, commands/artifacts and outcomes, limitations, and next action. The controller's run logs and patch files are the execution record; this file carries product context across cycles.

## Precise pad pan — 2026-09-05

- Reviewed previous held run `20260905T190300.009765Z` result and quality log: pitch passed; integration was held because source changed. Current baseline already contains that pitch implementation/tests, so this cycle extends pan instead of replaying the candidate.
- Reproduced the actual baseline inspector with synthetic audio: no numeric pan editor; dragging from center to approximately 0.2 then undo left pan at 0.2. The new control returns to center on undo. Reproduction and before/after renders: `docs/agent/evidence/2026-09-05-pad-pan/` (`render_fixture.py before|after`, run from workspace root).
- Pan now supports signed percentage entry (-100 left, 0 center, +100 right), 0.1% arrow steps, 10% slider page steps, context-menu/Alt+0 center reset, Enter/focus-out commitment and Escape cancellation. Shared pitch/pan interaction code captures one snapshot per drag and preserves text undo during entry, project undo after commitment, redo on no-op, and saved precision during rebuild. Retired controls cannot commit into a different pad/project.
- Research decision: [Qt keyboard tracking](https://doc.qt.io/qt-6/qabstractspinbox.html#keyboardTracking-prop) documents deferred value signals on Return/focus loss when tracking is disabled. Applied this existing pitch convention to pan so intermediate typed numbers do not change the project. Percentage scaling and reset are our interface choices; engine and serialized normalized pan remain unchanged.
- Focused tests: **38 passed** (pan and existing pitch). Covers limits, invalid input, cancellation, selection/empty state, shortcut ownership, drag grouping, no-op redo, save/reopen with history, and rendered sound. Synthetic 440 Hz dual-mono tone, 48 kHz, 512-frame callback blocks, 48 blocks; callback and FLOAT WAV export channel RMS directions match constant-power pan within 1e-6 at -100%, -37.3%, 0%, 12.7%, 100%. Python DSP fallback plus pan: **54 passed**.
- Inspected actual dark/light inspector renders at 320 and 268 px: entry, labels and sliders fit; graphite/amber styling retained. All rendering is offscreen with engine startup disabled and temporary media/settings. No hardware measurement or desktop launch.
- Next: precise pad gain with dB entry, unity reset, preservation of exact mute and the existing 0–4 linear range. Controller verification/integration of this pan candidate remains pending.
- Broad verification: `QT_QPA_PLATFORM=offscreen UV_OFFLINE=1 UV_CACHE_DIR=/tmp/anharmonic-pan-uv bash scripts/quality.sh` exited 0: lock/native build/lint/format/compile gates passed; **539 passed, 3 hardware skipped**. Software production mixed case (48 kHz/512, 64 blocks, 8-voice limit, inserts) p99 5.13 ms, worst 5.57 ms, zero measured steady-state deadline misses; two-second soak zero late callbacks. Cold piano preparation reached 18.54 ms; 256-frame callback benchmark reported no headroom. These short shared-host measurements do not certify startup deadlines, smaller blocks or physical latency. Full logs retained in the evidence directory.


## Undoable pad sound shaping — 2026-09-05

- Investigated held run `20260905T200155.518586Z`: read its result, quality log, patch, backup member list and manifests. Its quality checks passed. The controller's reported “Post-integration verification failed” is a content-and-mode manifest comparison after patch application. Every prior changed file's content matches this run's baseline; the three existing files (backlog, memory, padgrid) carry 0600 permissions versus candidate 0644. New prior files match both. Evidence: `docs/agent/evidence/2026-09-05-pad-slider-history/previous-integration.txt`. Preserved current workspace permissions; did not modify the controller or original checkout. This explains the hold without replaying pan or claiming integration success.
- Chosen prerequisite: dependable undo for gain, attack, release and loop-crossfade sliders. The real inspector mutated these parameters without a pre-edit snapshot. All 18 new regression cases failed on the baseline, including a gain reduction whose previous audible level could not be restored. `before.log` records the reproduction.
- The shared slider handler now snapshots before its first value change, groups a held drag into one history entry, and gives separate keyboard/step edits separate entries. Press/release without movement preserves redo and exact saved precision. Rebuild retires slider signals so an old control cannot modify a detached pad or add history after selection/undo/load. Existing ranges, display and DSP mappings are preserved.
- Research: [Qt QAbstractSlider](https://doc.qt.io/qt-6/qabstractslider.html#tracking-prop) documents value signals during tracked drags, and [sliderDown](https://doc.qt.io/qt-6/qabstractslider.html#sliderDown-prop) documents press/release signals. Our application captures before the first tracked change and ends grouping on release; waiting until release would lose the original sound.
- Focused regression: **18 passed** (`after.log`): all four parameters, selected-pad isolation, consecutive drags, keyboard steps, exact precision, no-movement redo, retired controls and save/reopen undo/redo. The initial combined run passed all 38 existing pan/pitch tests; its new audio test exposed the synthetic fixture's creation-cache float32 versus persisted PCM24 difference (maximum 6.34e-8). The fixture now reads persisted audio before comparisons, retaining exact array-equality assertions. No existing assertion or performance gate was weakened.
- Synthetic audio evidence: a two-second 440 Hz stereo fixture at 48 kHz; each comparison renders 24 device-free callback blocks of 512 frames. Gain 1 → 0.5 halves steady-state amplitude within 1e-7; undo and redo reproduce the corresponding full signals exactly. This is software rendering, not hardware latency evidence. No visible layout change; existing visual renders remain applicable.
- Next: precise gain entry with dB units, unity reset and exact mute. Controller verification/integration remains pending.

- Broad verification: `QT_QPA_PLATFORM=offscreen UV_OFFLINE=1 UV_CACHE_DIR="$PWD/.native/uv-cache" bash scripts/quality.sh` exited 0: lockfile/native build/lint/format/compile checks passed; **557 passed, 3 hardware skipped**. Python DSP fallback plus slider-history regression: **53 passed** (`fallback.log`). Software production mixed workload at 48 kHz/512 frames, 64 blocks, 8-voice limit and inserts: p99 **3.36 ms**, worst **3.38 ms**, zero steady-state deadline misses; cold piano worst **9.51 ms**. Two-second callback soak: zero late callbacks across 2924 iterations. Host: Linux x86_64 shared workspace. Evidence: `quality.log` in the task evidence directory. These short software measurements do not certify physical latency or sustained production deadlines.


## Precise pad gain, reconciled with the orchestra baseline — 2026-09-05

- Reviewed held candidate `20260905T220055.512610Z`: `result.json`, controller `quality.log` (602 passed, 3 hardware skipped, fallback/benchmarks/soak passed), and the source/test patch. It was held before integration while the interactive orchestra pass evolved the source; current memory explicitly warned against blindly applying it. Current `padgrid.py` still lacked numeric gain. Adapted only the reviewed gain implementation/tests and regenerated evidence; preserved newer orchestra/four-bar documentation and source, existing file permissions and the read-only shared environment. Controller verification/integration remains pending.
- Reproduced on this run's baseline: all **23** gain-entry regression cases fail because there is no numeric editor (`before.log`). The new control adds dB entry (two decimals, 0.1 dB arrow steps), Enter/focus-loss commitment, Escape cancellation, context-menu/Alt+0 unity reset, and exact mute. Slider positions retain the linear 0–4 range; displayed +12.04 dB maps to exactly 4, unity to 1 and mute to 0. Stored gain and callback/export representation remain linear.
- Rounded displays preserve exact saved values and no-op history. Slider drags group one undo snapshot, numeric edits have independent history, retired controls cannot affect replacement pads, and gain/mute history survives save/reopen. Positive legacy gains below the -120 dB numeric floor display `< -120.00 dB` and remain unchanged until adjusted; explicit reset provides exact unity even from a rounded near-unity display.
- Found an additional edge in the held implementation: entering -120.05 dB or stepping down from -119.95 crossed the advertised numeric floor and displayed a legacy-value prefix. Both new regressions failed (`floor-before.log`). Values between the floor and mute sentinel now land at -120 dB; the next downward step reaches exact mute, with one undo per step. Both regressions pass (`floor-after.log`).
- Research: [Qt keyboard tracking](https://doc.qt.io/qt-6/qabstractspinbox.html#keyboardTracking-prop) specifies deferred signals on Return/focus loss; [QDoubleSpinBox precision](https://doc.qt.io/qt-6/qdoublespinbox.html#decimals-prop) explains numeric rounding. Applied these to deferred entry and preserving the independent stored linear value. The floor, endpoint, reset and mute mappings are project design decisions.
- Evidence: `docs/agent/evidence/2026-09-05-pad-gain/`. Reused and reviewed `render_fixture.py before|after`: offscreen Qt, in-memory settings, temporary synthetic media, engine startup disabled; `before` reads this workspace's baseline HEAD inspector. Inspected actual dark/light 320/268 px inspector renders plus narrow mute/maximum/quiet-saved states. Controls and focus remain readable; no desktop window or hardware audio stream was launched.
- Focused tests: **79 passed** across gain/pan/pitch/grouped-slider history (`focused-after.log`), then **2 passed** for the additional floor fix (`floor-after.log`). Audio coverage uses persisted synthetic two-second 440 Hz stereo at 48 kHz, 48 device-free 512-frame callback blocks and one-bar FLOAT WAV exports. At -120, -6.25, 0, +12.04 dB and mute, asserts scaled finite signals, exact silent mute, and bit-identical audio restoration through undo/redo.
- Next: precise millisecond articulation controls, or a bounded producer-guided A/B phrase variation. Changes apply to subsequent pad hits; this does not introduce gain automation or physical latency certification.
- Broad verification: `QT_QPA_PLATFORM=offscreen UV_OFFLINE=1 UV_CACHE_DIR="$PWD/.native/uv-cache" PYTHONDONTWRITEBYTECODE=1 bash scripts/quality.sh` exited 0. Lock/native build/lint/format/compile gates passed; **725 passed, 3 hardware skipped** in 199.22 seconds (`quality.log`), including current orchestra and four-bar workflows and all 25 gain cases.
- Software performance on this Linux x86_64 host: 48 kHz/512 frames, 64 blocks, eight-voice mixed workload with inserts, p99 **4.45 ms**, worst **4.50 ms**, zero measured steady-state over-budget blocks against 10.67 ms. **Cold piano worst 14.26 ms exceeded that deadline**; the existing benchmark reports cold time separately and its steady-state gate passed. This UI change does not address cold synthesis preparation; do not infer all callbacks meet the deadline. Two-second soak: **2879 iterations, zero late callbacks**, no measured RSS growth. No physical latency or sustained production certification is claimed.
- Python DSP fallback: **89 passed** in 40.88 seconds with `QT_QPA_PLATFORM=offscreen MPC_NATIVE_DSP=0 PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q tests/test_synth.py tests/test_engine_synth.py tests/test_music_workstation.py tests/test_orchestra.py tests/test_pad_gain.py` (`fallback.log`). Final diff/whitespace check passed; changes are local and await independent controller verification/integration.


## Precise and dependable pad articulation — 2026-09-06

- Previous controller run `20260905T230055.566482Z` was applied, with its gain controls present in this baseline. Preserved that work and the newer orchestra/four-bar flows. Chosen task: direct millisecond attack/release/loop-crossfade entry, extending the established slider/history path. Baseline reproduction: all **31** initial interaction regressions failed because numeric editors were absent (`before.log`).
- Added 0.1 ms numeric steps, Enter/focus-loss commitment, Escape, context-menu/Alt+0 defaults (2 ms attack, 30 ms release, 5 ms crossfade), and zero crossfade through direct entry. Retains the prior ranges (attack 0.5–400 ms, release 5–1500 ms, crossfade 0–50 ms) and linear sliders. Values remain seconds in the model. Rebuild/no-op entry preserves exact saved precision; legacy values outside the range show a bound prefix and remain untouched until edited. Retired widgets cannot modify previous pads or project history. Removed the now-unused slider-only helper.
- Research decision: [Qt deferred keyboard tracking](https://doc.qt.io/qt-6/qabstractspinbox.html#keyboardTracking-prop) documents committing numeric entry on Return or focus loss; [Qt decimal precision](https://doc.qt.io/qt-6/qdoublespinbox.html#decimals-prop) documents rounding. Applied the existing inspector convention and kept an independent exact stored value. Millisecond steps, ranges and reset mappings are project choices based on current model/DSP behavior. Tooltips distinguish source-time crossfade, loop mode, short-slice capping and repitch effects.
- Audio validation uncovered a real prerequisite defect: crossfade used first-pass absolute travel to overwrite later cycles' head position. An 80 ms synthetic ramp with 5 ms crossfade jumped at the second and later wraps; the largest measured unit-gain jump was **0.187497** versus a 0.2 source span. All **18** independent scalar overlap-add reference cases failed on the baseline (`loop-before.log`). The small `PadVoice.render` fix keeps cycle-relative head positions after the first pass and adds the source slice offset consistently. Tests cover 0.75/1/2 playback rates, zero/nonzero slice starts, a crossfade larger than half the source and 64/257-frame blocks.
- The old full-loop PCM16 golden hash encoded those incorrect later wraps. The corrected hash was computed from the independent scalar reference before changing the assertion; the engine produced exactly that hash. Existing first-wrap continuity and dry-jump assertions are unchanged. `loop-after.log` preserves the intermediate run: **38 passed**, with only the obsolete golden failing. No tolerance or performance gate was loosened.
- Evidence: `docs/agent/evidence/2026-09-06-pad-articulation/`. Inspected actual offscreen dark/light 268/320 px inspector renders and narrow maximum-value focus. The renderer reuses the reviewed temporary synthetic-library/in-memory-settings fixture with engine startup disabled; no desktop window or device stream. `before` reads only this workspace's baseline HEAD inspector.
- Focused interaction plus existing gain/pan/pitch/slider-history coverage: **112 passed** (`focused-after.log`). Subsequent complete articulation/audio-reference/hardening coverage: **71 passed** (`final-focused.log`), followed by **9 passed** for expanded legacy precision cases (`legacy-after.log`). New sound checks use 48 kHz synthetic flat/ramp sources, 72 device-free 512-frame blocks and one-bar FLOAT WAV exports; callback/export agreement within 1e-7, analytical attack/release envelopes at limits/intermediate values, exact post-release silence, loop discontinuity reduction and bit-identical undo/redo restoration. Gate/loop note-off is measured across 150 callback blocks, including 1500 ms release.
- Limits: changes affect subsequent hits; this is not articulation automation. Loop crossfade still consumes overlapping head frames, is capped below half the selected source and changes audible duration under repitch. No hardware latency or long-session certification. Next: producer-guided A/B phrase variation or a bounded expression/controller slice; cold piano preparation remains separately backlogged. Local candidate awaits controller verification and integration.

- Broad verification: `QT_QPA_PLATFORM=offscreen UV_OFFLINE=1 UV_CACHE_DIR="$PWD/.native/uv-cache" PYTHONDONTWRITEBYTECODE=1 bash scripts/quality.sh` exited 0: lock/native build/lint/format/compile gates passed; **796 passed, 3 hardware skipped** in 221.02 seconds (`quality.log`). Software production mixed workload at 48 kHz/512 frames, 64 blocks, eight-voice limit and inserts: p99 **2.80 ms**, worst **2.82 ms**, zero steady-state deadline misses. **Cold piano preparation still exceeded the 10.67 ms deadline at 12.91 ms**, as separately reported by the existing benchmark; this change does not solve that previously backlogged issue. Two-second soak: **3406 iterations, zero late callbacks**, no measured RSS growth.
- Dedicated changed-path benchmark (`bench_loop_fixture.py`, `loop-benchmark.log`): Linux 7.2.0-1-cachyos x86_64, eight simultaneous synthetic 80 ms sample loops at a nonzero source offset, 12.7 ms crossfade, -3/0/+3 semitones, 48 kHz/512 frames. After 64 warmup callbacks, **2000 callbacks** (21.33 seconds of rendered audio) measured p99 **1.22 ms**, worst **1.37 ms**, **zero deadline misses**; warmup worst **1.60 ms**. Direct software timing, without devices or real-time scheduling; not a physical latency or sustained-production certification.

- Python DSP fallback: **141 passed** in 57.64 seconds with `QT_QPA_PLATFORM=offscreen MPC_NATIVE_DSP=0 PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q tests/test_synth.py tests/test_engine_synth.py tests/test_music_workstation.py tests/test_orchestra.py tests/test_pad_articulation.py tests/test_pad_loop_reference.py tests/test_audio_hardening.py` (`fallback.log`). Final whitespace/diff check passed. Only workspace source/tests, evidence, BACKLOG and MEMORY changed; existing `.venv` link left untouched. Controller verification/integration remains pending.


## Dependable first synth notes — 2026-09-06

- Reviewed held run `20260906T000055.541772Z`: saved result, controller quality log, patch, manifests and backup member list. All saved verification commands passed (796 tests plus fallback/benchmarks/soak). Every changed file's contents match this workspace baseline; `mpclab/engine.py` and `tests/test_audio_hardening.py` have 0600 modes versus the candidate's 0644. This is consistent with the prior content-and-mode integration failure. Preserved current permissions and existing articulation work; did not replay the patch or modify the controller/original checkout. Exact comparison: `docs/agent/evidence/2026-09-06-synth-startup/previous-integration.txt`.
- Chosen prerequisite: remove first-note module loading from the callback. Actual profiling traced the cold piano cost to `SynthVoice.__post_init__` resolving lazy `np.random`: Python imports, extension loading and file reads occurred during the first chord. All three fresh-interpreter regressions failed on the baseline for keyboard, pattern and arp entry (`before.log`, 29 import/open audit events per case). The initial instrumented profile preceded native compilation and used the Python fallback (`profile-before.log`, 49.28 ms); it is diagnostic evidence, not a native timing comparison.
- Changed the synth module to import `default_rng` eagerly before callback execution. Each voice retains the exact previous seed, independent generator, noise sequence, envelopes, timing and rendering paths. This moves one-time loading to ordinary module initialization without warming a voice, consuming noise samples or changing musical state. No UI or persistence format changes.
- Research: [JACK process callback requirements](https://jackaudio.org/api/group__ClientCallbacks.html) state that processing must avoid potentially blocking operations, including I/O. Applied this principle to the observed Python import/file-read boundary; the application still uses PortAudio and this is not a claim of complete real-time safety. Relevant library entry: RESEARCH.md → JACK client callbacks.
- Focused verification: **38 passed** (`focused-after.log`), including the new fresh-process audit tests and existing synth/engine/workstation tests for note releases, voice limits, persistence, callback/offline agreement and export. New tests render synthetic noise-bearing eight-note chords at 48 kHz/512 frames for 96 callbacks per keyboard/pattern/arp case, assert no callback import/open events, finite audible output, exact silent tails after note-off/gate release, and no sounddevice import.
- Reproducible measurements: `cold_probe.py --profile` launches five fresh interpreters, each rendering an original eight-note pattern with noise for 256 callbacks (2.731 seconds of audio), then separately profiles one first callback. All callbacks, including the initial eight, count toward p99/worst/deadline misses. Native baseline (`cold-before.log`) first blocks were 4.90–5.70 ms; all five had zero misses against 10.67 ms. Thus the previous 12–18 ms native overruns did not recur in this short baseline sample, but the callback I/O defect reproduced deterministically. The first after probe (`cold-after.log`) overlapped focused tests, so it is retained as loaded-host evidence rather than used for a clean speed comparison.
- Limits: per-note generators/state arrays and other Python callback allocations remain. Import cost is paid at module initialization; it is not eliminated from total launch time. No hardware, physical latency, cold filesystem cache, sustained production or universal startup-deadline certification. Existing Qt appearance remains unchanged. Next: profile remaining voice preparation if it is the next measured bottleneck, or return to producer-guided A/B phrase editing. Candidate remains local pending independent controller verification/integration.

- Broad verification: `QT_QPA_PLATFORM=offscreen UV_OFFLINE=1 UV_CACHE_DIR="$PWD/.native/uv-cache" PYTHONDONTWRITEBYTECODE=1 bash scripts/quality.sh` exited 0: lock/native build/lint/format/compile passed; **799 passed, 3 hardware skipped** in 593.77 seconds (`quality.log`). Python reference checks (`MPC_NATIVE_DSP=0`, synth-startup/synth/engine-synth/music-workstation) **38 passed** in 29.48 seconds (`fallback.log`).
- Final uncontended-by-agent-tests probe (`cold-final.log`): five fresh processes on Linux 7.2.0-1-cachyos x86_64, native DSP, 48 kHz/512 frames, eight-note original pattern with noise, 256 callbacks each. First callbacks **1.655–1.758 ms**, per-run all-block p99 **1.110–1.252 ms**, worst across all runs **1.758 ms**, **zero deadline misses** across 1280 callbacks against 10.67 ms. Baseline first callbacks were **4.904–5.698 ms** with the same fixture. All ten before/final complete stereo renders have identical SHA256 `a02cb45b513d4b079cbdc5482ff1254c8cbff8bad6297dfe38a890c381284e5a`. Final after profile confirms the import stack is absent. These fresh-process software runs share the host and filesystem cache; no physical latency claim.
- Broad production smoke: piano cold **1.73 ms**, mixed cold **2.57 ms**; mixed steady p99 **2.89 ms**, worst **3.05 ms**, zero measured deadline misses. Two-second callback soak: **3366 iterations, zero late callbacks**, no measured RSS growth. Final diff/whitespace checks passed. Changed only synth import/use, new regression/evidence files, BACKLOG and MEMORY; preserved baseline file modes and the read-only shared `.venv` link. Independent controller verification/integration remains pending.


## Play synth along with patterns without cutting their notes — 2026-09-07

- Investigated held run `20260906T010139.323257Z`: read result, full controller quality log, saved patch, backup member list and candidate/baseline manifests. Its 799-test suite, fallback and software audio checks passed. Every changed file's content is present in this baseline; `mpclab/synth.py` is 0600 versus the candidate's 0644. Read-only controller inspection confirms a content-and-mode comparison after patch application, consistent with the hold. Exact evidence: `docs/agent/evidence/2026-09-07-synth-ownership/previous-integration.txt`. Did not replay the patch, change controller code or edit the original checkout; preserved current file modes.
- Reproduced a musician-facing ownership defect: live note-off and same-pitch retrigger affected sequenced synth notes, while transport stop/seek treated live arp gates as sequence ownership. The initial 20 synthetic regressions yielded **9 failed, 11 passed** (`before.log`). This can truncate a backing chord while playing along or interrupt an arpeggio during transport navigation.
- Added explicit runtime `SynthVoice.live_trigger` ownership, propagated by live/pattern/export construction. Same-pitch retrigger and live key release now affect their own source. Stop/rewind/seek release sequenced notes and preserve live keyboard/arp voices. Global/synth panic still releases both; natural gates and within-source retrigger remain effective. No model/persistence/UI format change. The existing sequenced-stop unit fixture now specifies sequence ownership explicitly; its original release assertion is unchanged.
- Decision grounded in the existing pad ownership contract (`Engine._pad_trigger_cuts` and `tests/test_pad_choke.py`) and actual synth call paths. No external research was needed to decide this local source-ownership correction; no new UI behavior or DSP algorithm is attributed to an external source.
- Focused verification: **61 passed** in 8.46 seconds (`final-focused.log`), including 26 new ownership cases plus synth/workstation/transport/cold-import coverage. Synthetic oscillator fixtures use 48 kHz/512-frame blocks, low gain and no devices. After a same-pitch live note ends, backing sustain is bit-identical to the untouched control render; its own gate still ends in exact silence. Keyboard/arp audio across stop, rewind and seek is bit-identical to a control in both pattern and song mode. Tests also cover reverse ownership (sequence onset over a held live note), within-source retrigger, panic and sequenced transport release. Existing callback/offline/export agreement remains covered. `focused-after.log` retains the intermediate failure of the old gate-only fixture.
- Limits: voices still share one patch, round-robin counters, routing and the existing global polyphony/oldest-voice limit. This change isolates release/retrigger ownership below that limit; it does not reserve polyphony, add separate instruments, chase notes after seek, or change loop boundaries. Existing Qt appearance and project/history format are unchanged. Next: bounded note-chasing/loop-boundary work or producer-guided phrase variation. Local candidate awaits independent controller verification/integration.

- Broad verification: `QT_QPA_PLATFORM=offscreen UV_OFFLINE=1 UV_CACHE_DIR="$PWD/.native/uv-cache" PYTHONDONTWRITEBYTECODE=1 bash scripts/quality.sh` exited 0. Lock/native build/lint/format/compile passed; **825 passed, 3 hardware skipped** in 226.93 seconds (`quality.log`). Python reference DSP checks with `MPC_NATIVE_DSP=0` over synth, engine-synth, workstation, ownership and cold-start tests: **64 passed** in 9.19 seconds (`fallback.log`).
- Software production smoke at 48 kHz/512 frames, 64 blocks, eight-voice limit: mixed workload with inserts p99 **3.49 ms**, worst **3.54 ms**, cold worst **2.92 ms**, zero measured steady-state misses against 10.67 ms. Two-second callback soak: **3630 iterations, zero late callbacks**, no measured RSS growth.
- Dedicated changed-path probe: `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python docs/agent/evidence/2026-09-07-synth-ownership/benchmark.py` (`benchmark.log`). Linux 7.2.0-1-cachyos x86_64, native DSP, 48 kHz/512 frames, four live notes over four same-pitch pattern notes, no inserts; after 64 initial callbacks, **2000 callbacks / 21.33 seconds of synthetic audio**, p99 **1.09 ms**, worst **1.54 ms**, **zero deadline misses**; initial 64 worst **1.41 ms**, finite audible output. These are direct software timings on a shared host, not physical latency or sustained-production certification. No concurrent agent tests during this dedicated probe.
- Final diff/whitespace and existing-file permission checks passed. BACKLOG and MEMORY updated; shared environment link and owner work preserved. Candidate remains local for independent controller verification/integration.


## Samples into Notes and Beats — 2026-09-07 interactive GitHub pass

- PR #2 initially failed importing Qt because `libEGL.so.1` was absent. Added
  Ubuntu Qt/audio dependencies and import preflight without weakening tests;
  repair-only run 34151082956 passed all gates. Later feature commits need their
  own checks, not the helper snapshot workflow's green badge.
- Added stored Note.pad targets, Pad.root_note/mono, format 3 with legacy loading,
  reused sample voice/render paths, explicit Browser/Sample destinations, safe
  Beats/Notes drops, scoped note editing, held-note ownership and step conversion.
- Added 49 synthetic/offscreen regressions; focused sample suite and 65 existing
  drag/key/music tests passed locally. The container uses Python 3.13/older Qt,
  not the locked Python 3.12 CI environment; rely on published CI for the latter.
- Actual preview renderer: `scripts/render_sample_workflow.py`. Notes/Beats at
  1440x900 and Notes at 1000x900, dark/light, inspected without audio startup.
  Corrected sample-channel focus so a bass melody opens in its actual octave.
- Remaining limitations and next tasks are recorded in `../SAMPLE_WORKFLOW_2026-09.md`.
  Existing pad/sample identities are shared, synth remains global, and source
  preparation is off-callback but not yet a general asynchronous import service.
