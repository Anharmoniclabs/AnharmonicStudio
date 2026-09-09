# Anharmonic Studio: full production DAW roadmap

**Owner update, September 8 overnight:** the owner requested continual refinement,
one pass after another across the whole DAW. This supersedes the twice-daily
schedule proposed below. See [the continuous workflow](agent/CONTINUOUS_WORKFLOW.md)
and [controller operation](../automation/README.md) for the current implementation.
The milestone architecture below remains useful; the original pause/status notes
describe the planning audit, not the current live worker status.

Prepared September 8, 2026. This is a proposed development plan, grounded in a read-only review of the current source, existing evidence, and official technical documentation. No application features, running applications, or schedules were changed to prepare it. Base commit: `da6d3a00640f2723227f810222a7b164faa3a418`, plus the existing uncommitted September 8 work. No new build, test suite, listening session, or hardware test was run for this plan.

**Product goal.** Make Anharmonic a dependable studio where a producer can sample, design distinctive sounds, play external instruments, record a band or vocalist, arrange, mix, and deliver a finished song. Preserve the fast sample → chop → pads → pattern → song workflow, detailed illustrated Crates, and the new Song/Beats/Notes/Sampler/Instruments/Mix workspace with its selectable accent and full-height editor. Judge progress by finished musical tasks and recoverable sessions.

The planning default is balanced production on the existing Linux/CachyOS application. The owner's audio interface, MIDI controllers, and essential plugins are still unspecified. Those names should determine the first hardware and plugin compatibility fixtures. Feature gates below define completion; they are not promises of universal compatibility or fixed delivery dates.

**What is actually present.** Historical reports and the README lag some current source. Backend code, a connected feature, and a feature validated on real equipment must be tracked separately.

| Area | Current evidence | Next gap to close |
|---|---|---|
| Sampling and production | Four-bar chopping and placement, pad/sample notes, pattern and arrangement editing, groove, synth, effects, offline export; recent direct track capture | Finish full songs without shared-instrument and routing limitations |
| Sound palette | Recorded orchestral articulations and layered hybrids, prepared loading, sample controls and original patches | Independent instances, expression, authored loops/dynamics, modulation and stronger musical audition |
| Recording | One armed Song destination; mono recorder, input compensation, optional monitoring, failed-save retry in memory; existing take/comp/tuning tools | Durable capture recovery, simultaneous channels, take-lane integration and verified physical monitoring |
| Instruments and routing | 64 sample slots, one shared synth patch and eight mixer buses; arrangement rows are a separate concept | Persisted stable instrument/track/bus identities and independent signal paths |
| MIDI | Backend-neutral event helpers plus a separate ALSA native prototype | Connect hardware input and editable recording to the real application; complete timing, controllers, output and file support |
| Plugins | Filesystem LV2/CLAP/VST3 discovery and quarantine metadata; separate limited native LV2 effect prototype | Executable scan isolation, complete host lifecycle, VST3/CLAP adapters, state, UI, automation, routing, delay compensation and recovery |
| Engine | Main window still constructs the Python `Engine`; existing native DSP kernels accelerate parts of it | Integrate and prove a native render graph without losing current musical behavior |
| Sessions | Project/history mechanisms and packaging/relink/cache foundation modules | Connect collect/relink/recovery UI and bounded long-media streaming |
| Recurring development | Isolated snapshots, independent checks, backups, locking, and an installed hourly timer definition | Repair the paused integration workflow before recurring development resumes |

Source anchors: [main application](../mpclab/ui/main_window.py), [current engine](../mpclab/engine.py), [project model](../mpclab/model.py), [track capture](../mpclab/ui/track_recording.py), [recorder](../mpclab/vocal.py), [channel registry](../mpclab/channel_registry.py), [MIDI helpers](../mpclab/midi_io.py), [plugin discovery](../mpclab/plugin_registry.py), [native engine](../native/src/engine.cpp), and [workspace implementation](WORKSPACE_2026-09-08.md). The existing [workspace render](previews/workspace-2026-09-08/song-dark-1440.png) was inspected; it is an offscreen receipt, not a capture of the currently running app.

**Milestones, in dependency order.** Recording reliability begins immediately; deeper engine work should proceed through useful, reversible increments. A large architecture milestone spans several cycles.

| Stage | Work | Completion demonstration |
|---|---|---|
| 0 — Trust the session and the development loop | Reconcile failed integrations; fix controller mode handling; establish the exact source baseline; durable recording spool/journal and recovery; accurate capability list | A disposable restrictive-permission integration succeeds without changing unrelated files; a deliberately interrupted synthetic take is recovered with correct timing and its original media intact |
| 1 — Independent tracks and a shared audio model | Stable persisted IDs for tracks, instruments, buses and parameters; backward project migration; independent built-in synth/sampler instances; explicit audio/MIDI routing; native render ownership and live/offline parity in measured slices | Drums, bass, keys and strings retain different sounds after reorder, clone, undo, save/reopen and export; old projects keep their sound and routing |
| 2 — Dependable audio recording | Mono/stereo input selection, multiple armed tracks, disk streaming, synchronized input/output timing, explicit monitor/cue routes, pre-roll/count-in, punch and loop takes, comp lanes, fades and clip gain | Record a vocal and instrument simultaneously into distinct lanes, punch a phrase, comp three takes, reopen and export without altering dry originals; validate on a named physical interface |
| 3 — Complete everyday MIDI production | Device browser and reconnect, timestamped input/output, channel routing, note capture, sustain, bend, aftertouch, CC lanes and learn, MIDI-file import/export, note chase/panic, external clock/transport | Play and record a controller with pedal and bend; edit its performance; export/import the MIDI; reconnect the device; hear and render the same intended notes |
| 4 — Reliable plugin production | Complete the LV2 proof where useful; prioritize native Linux VST3 hosting for the owner's request; add CLAP through the same host abstraction; instruments/effects, plugin windows, presets/state, automation, sidechains, multiple outputs, delay/tail handling, freeze and crash recovery | A named synth plus effect chain saves and restores its exact state, automates correctly, renders with compensated latency and survives a plugin-worker failure without losing the session |
| 5 — Distinctive sound design and arrangement | Expression and articulation mapping, layered sampler, granular/wavetable/FM/resonator additions in useful slices, modulation/macros, resampling, pitch-preserving stretching, warp markers, vocal timing/pitch refinement, tempo/meter maps | Make three clearly different patches from one source, perform expressive changes, create a tempo-fitted sample flip, and retain editable originals through arrangement/export |
| 6 — Mixing and delivery | Expand beyond fixed buses; groups, sends/returns, sidechains, cue mixes and external inserts; full parameter automation; reference listening, accurate loudness/true-peak metering; stems, tail handling, sample-rate conversion, dither and export queue | Complete and reopen a substantial song; export aligned stems and a checked master; confirm routing, tails and loudness against reference signals |
| 7 — Professional release quality | Clean install/update, portable projects and missing-media repair, compatibility catalog, migrations, keyboard/accessibility support, high-DPI layouts, endurance and fault injection | A fresh Linux installation opens the portable reference session, finds supported devices/plugins, completes a recording and delivers the same intended mix |

Stage 0 precedes unattended integration. Stage 1 establishes interfaces required by multichannel recording, independent MIDI destinations, and production plugin hosting. Recording recovery, sound audition, accessibility and small producer improvements can advance while that architecture is built. Plugin scan metadata and compatibility research can also begin early. Do not make the separate native prototype the default engine before it reproduces current sample, synth, arrangement, effects and export behavior.

**Engine decisions that make expansion possible.** Keep Qt/Python for editing, project logic and preparation; extend the existing C++ work for time-critical rendering after benchmarking its boundaries. Use one render graph and parameter/event model for playback, recording alignment, plugins and offline export. Prepare media, graph changes, plugin state and DSP kernels outside the callback; pass bounded commands and reclaim retired state outside rendering. Define event-overflow and worker-timeout behavior explicitly. PortAudio identifies allocation, I/O and mutex operations as callback hazards; the design should demonstrate bounded work rather than infer safety from the implementation language. [PortAudio callback guidance](https://portaudio.com/docs/v19-doxydocs/writing_a_callback.html).

The current native prototype needs its own gate before integration: input is copied directly to output, pitch-bend handling is empty, its MIDI event type lacks timestamps, and LV2 control values are written directly while rendering reads them. Introduce explicit monitoring policy, timed event dispatch, and owned parameter queues. Its LV2 implementation currently expects audio inputs and outputs and processes at most two of each, so it does not establish general instrument hosting. These are source findings requiring implementation tests, not observed failures in the running studio.

The normal local quality script builds the existing native DSP helper; it does not build/test the new C++ engine. The GitHub quality workflow already builds that engine and checks its ABI loads. Align local/controller gates with that build and add deterministic native render fixtures, lifecycle/fault tests and relevant sanitizer runs. Retain the Python/reference paths for comparison during migration. Long recordings need disk-backed streaming and bounded caches; a recording writer must retain recoverable partial files if disk space, a device, or the app fails. The current recorder reads the whole take into memory at finalization and removes its spool; queue overflow drops blocks without preserving their timeline position. Prioritize durable spools, frame timestamps, explicit gap markers and recovery before expanding capture duration or channel count.

**Make MIDI and plugins accessible in practice.** One searchable browser should show instruments/effects, format, installed version, availability and scan status. A musician should be able to insert a sound, route a controller, learn a knob and restore its preset without working with backend identifiers. Use labeled generic parameter controls when a plugin has no usable custom editor. Support keyboard navigation, readable values, focus ownership and screen-reader semantics alongside mouse operation.

| Compatibility tier | Planned support and evidence |
|---|---|
| Everyday MIDI first | USB/DIN interfaces exposed through supported Linux backends, multiple named ports/channels, note/velocity/release, sustain, pitch bend, channel/poly pressure, CC, program/bank changes, RPN/NRPN and bounded SysEx transport as appropriate; MIDI learn and `.mid` format 0/1 round trips |
| External hardware production | MIDI output, clock/start/stop/continue and song position where applicable, selectable sync source, measured clock jitter, device reconnect, input/output offset calibration and external audio return; controller profiles after generic mapping works |
| Expressive MIDI next | MPE zones, independent note expression and articulation maps; add UMP/MIDI 2.0 after backend and device evidence. MIDI 2.0 builds on MIDI 1.0 and requires deliberate implementation of additional capabilities. [MIDI Association](https://midi.org/midi-2-0) |
| Native Linux plugins | LV2 validation builds on existing code; VST3 is the first major requested compatibility milestone; CLAP follows or shares independent adapter work. Validate state, events, GUI, automation, buses, offline rendering and lifecycle per format. [VST3 SDK portal](https://steinbergmedia.github.io/vst3_dev_portal/), [LV2](https://lv2plug.in/), [CLAP](https://cleveraudio.org/) |
| Windows plugins on Linux | Optional tested yabridge/Wine route after native hosting is dependable. Record exact plugin, bridge, Wine and OS versions; test authorization UI, window focus, state and export. This is compatibility work per plugin. [yabridge documentation](https://github.com/robbert-vdh/yabridge) |
| Legacy and other platform formats | Assess demand and redistribution constraints separately. Do not imply every VST binary or every platform-only plugin can run in a Linux host. Preserve missing-plugin state and allow relinking/replacement without discarding a session |

The plugin host needs separate scanner and runtime failure handling: an isolated scanner alone cannot contain a crash during playback. Prototype process-isolated plugin workers with bounded communication, timeouts, defined silence/bypass policy and measured additional latency. Test worker restart and state restoration using disposable fixtures. Implement plugin delay compensation across all relevant paths, including sends, sidechains, freeze and live monitoring. Pin SDK/dependency versions and record their distribution terms before packaging.

**Sound design should improve audible character.** Start with the existing orchestral/hybrid palette: authored sustain loops, release behavior, velocity transitions, round robins, mod-wheel dynamics and articulation switching. Build eight clearly named macros that map to real parameters, tempo-synchronized LFOs/envelopes/step modulation, and reproducible random variation. Then add a granular sampler, wavetable/FM voice or resonator only when a musical example demonstrates the gap it fills. Provide layered patches, key/velocity splits, user preset tagging, A/B comparison and one-action resampling with source provenance. Avoid multiplying nearly identical presets.

A small useful parallel slice is analog filter cutoff with exact Hz entry, fine adjustment, reset, Escape, one-drag undo and saved precision. The existing synth setter snapshots sampled-instrument changes but not analog edits; arp and synth-route changes also mutate directly. Extend the established pad-control interaction to Instruments and Mix, validating audible behavior and undo rather than adding decorative controls. Extend automation with stable parameter IDs, smoothing, explicit read/touch/latch/write modes and predictable return to manual control. For custom-painted notes and regions, provide accessible semantic children or an equivalent table editor and test individual-item navigation with assistive technology.

For stretching, compare a prepared pitch-preserving implementation against the existing repitch workflow on drums, voice, sustained chords and full mixes. Rubber Band is a candidate because it offers independent tempo and pitch manipulation; evaluate sound, transient placement, resource cost and distribution requirements before selecting it. First deliver cancellable offline preparation with a reusable cache, then consider live warping. [Rubber Band Library](https://breakfastquay.com/rubberband/).

Make arrangement tools serve finishing: ripple/duplicate sections, markers, linked versus independent pattern copies, reversible groove and quantize strength, note length/velocity/expression lanes, fades/crossfades, alternate takes, tempo/meter changes and audio-to-MIDI only where confidence is exposed and edits remain available. Integrate the existing take/comp/tuning tools before designing replacements. Develop a reusable short hip-hop piece, a house arrangement, and an expressive orchestral/electronic cue to test different production demands.

**Repair the recurring workflow first.** The installed timer file is hourly. The persisted status inspected for this plan is `paused`, with three consecutive failures and a September 7 00:31 UTC update. Its reason is post-integration verification failure. Timer enablement and live service state were not queried. Read [automation documentation](../automation/README.md) and [integration evidence](agent/evidence/2026-09-07-synth-ownership/previous-integration.txt).

The evidence shows at least one candidate's contents already matched while the actual file mode was `0600` and the candidate expected `0644`. The installed service uses `UMask=0077`, and the controller compares content plus exact modes. Treat mode handling as a supported root-cause hypothesis to reproduce in disposable tests. Preserve prior permissions, account for executable bits/new-file policy, and reconcile already-applied content before retrying. Do not replay stale patches over today's changes. Controller, schedule, skill, release workflow and direction edits are protected from the existing unattended worker, so this repair belongs in a dedicated interactive development pass.

The controller's source-directory allowlist also excludes the separate `native/` tree. Before assigning C++ engine/plugin-host work to recurring cycles, extend snapshot, inventory and patch coverage to its source and build configuration while excluding generated build products. Verify inclusion, integration, conflict handling and exclusions in disposable controller tests. Adding a CMake command alone would leave the required source unavailable to the worker.

**Proposed cadence after repair.** Use the existing controller as the single scheduler/integration owner. Start with two bounded implementation opportunities per weekday, proposed 09:00 and 21:00 America/New_York, then adjust from actual completion rates, computer availability and account usage. A practical initial limit is 45 minutes for worker implementation with a separate measured allowance for independent gates; split any task that exceeds that budget. These are proposed limits, not current settings. The machine must be awake and dependencies/network access available. Never start a second writer to compensate for a slow run.

| Cadence | Concrete work | Receipt |
|---|---|---|
| Every implementation cycle | Select one unblocked milestone slice; reproduce; implement the full affected path; run focused verification and one independent candidate gate; integrate only against an unchanged source snapshot | What a musician can now do, patch, test/log paths, relevant before/after audio or widget image, next limitation |
| Nightly, when code changed | Exercise affected recording/recovery, MIDI order, routing, save/reopen and rendering paths in a longer synthetic session; hold future integrations on regressions | Workload, duration, callback p99/worst/misses, memory/disk behavior and recovery result |
| Weekly | Complete a reference-song workflow, assess audible changes, review incompatibilities and held work, choose the next milestone slice; inspect retained artifact size | A playable/exported example, measurable workflow friction, next week's ordered tasks |
| At each milestone/release candidate | Real interface/controller/plugin matrix; clean install; project migration/portability; two-hour endurance and interruption/recovery tests | Named configurations and explicit pass/fail/untested results, release candidate and rollback information |

The current worker already provides isolated source snapshots including uncommitted work, a lock, independent checks, retained evidence/backups, conflict detection, protected paths, and a 2 GiB artifact ceiling. Preserve these. Add resource budgets and coordination with interactive work before increasing frequency. Revalidate a selected task against current source at the start of every run. After several successful controlled integrations, reassess whether hourly scheduling would produce useful progress within the available usage budget.

Desktop scheduled tasks are an optional replacement for the local timer, not an additional simultaneous writer. Official documentation supports local-project or worktree execution and says the computer and app must remain available for local work; task prompts should be tried manually first. No scheduling tool or timer was used to activate this plan. [Official OpenAI scheduled-task documentation](https://learn.chatgpt.com/docs/automations?surface=app).

**What Codex can do repeatedly.** Implement scoped code changes, generate original synthetic music fixtures, test DSP and migrations, render actual offscreen widgets, investigate failures, evaluate relevant official APIs, maintain compatibility evidence, update backlog/memory, and prepare reviewable release candidates. Use an engine/DSP reviewer, a product/UX reviewer and a test/recovery reviewer in interactive sessions where independent work saves time; the existing scheduled worker remains single-agent under its current policy. Architecture decisions, controller repairs, new dependencies and system integration need dedicated interactive work. Human listening and physical interface/controller/plugin validation are separate evidence; rendered test signals alone do not establish musical quality or hardware reliability.

All unattended work must preserve the running studio, active chat, current audio graph and personal recordings/projects. Use temporary media, isolated settings and offscreen Qt. Application-control tests must use dedicated test processes and verify the target before any action. Never launch windows over, move, minimize, switch away from, restart or close the active chat. The user's [workspace safety rules](/home/al/AGENTS.md) apply throughout.

**First implementation queue.** Order the queue by risk and dependencies rather than by preset count. A row may require several bounded runs; a cycle must leave evidence and a precise next step when incomplete.

| Order | Deliverable | Owner/mode | Acceptance |
|---|---|---|---|
| 1 | Reproduce/reconcile controller integration and mode mismatch | Interactive controller repair | Disposable restrictive-umask regression, preserved file modes/content and user edits, one successful controlled integration |
| 2 | Establish the baseline, include native source in controller snapshots, and add the separate C++ build/test gate | Interactive setup, then worker maintenance | Native sources/configuration copy and integrate correctly while build products stay excluded; current working paths characterized and native prototype covered by deterministic renders |
| 3 | Durable failed-take storage, dropout timing and recovery | Worker-sized recording slices | Simulated write failure/interrupted test process leaves a recoverable take; dropped blocks retain timeline gaps; placement, undo and dry source survive |
| 4 | Persist stable instrument/channel IDs with legacy migration | Coordinated architecture slice | Reorder/clone/save/reopen does not change notes, sound destination or media identity |
| 5 | Two independent built-in instrument tracks | Worker slices after model decision | Different patches sound simultaneously and remain distinct in playback, history and export |
| 6 | Native ownership/parity slice for those paths | Engine work with review | Prepared commands, bounded events and reference comparison for first note, sustained note, loop, seek and tail |
| 7 | One external MIDI input to a chosen instrument and recorded Notes clip | Backend/UI work; supervised device acceptance | Software timestamps plus real controller take, no stuck notes after stop/disconnect |
| 8 | Sustain, bend and CC recording/editing; MIDI-file round trip | Worker slices | Expressive phrase preserves events and timing after edit/save/import/export |
| 9 | Stereo/dual-mono input selection and disk-backed simultaneous capture | Engine/recording work; supervised interface acceptance | Two sources land on distinct lanes with verified placement and explicit monitoring |
| 10 | Isolated executable plugin scan and durable registry | Host setup plus worker slices | Bad/hanging test plugin cannot prevent scans of other candidates; diagnostic and rescan are usable |
| 11 | First complete plugin track | Host architecture work | Complete LV2 reference proof as useful, then one native VST3 instrument and effect with UI/state/live/offline validation |
| 12 | One expressive sound-design feature and a finished reference production | Worker implementation plus listening review | Audibly useful control, editable result, saved state, measured cost and a short completed song |

Release the first useful recording/MIDI/instrument milestone as a coherent candidate before broad plugin-format expansion. Keep pitch-preserving sample preparation as a parallel producer slice when its dependency setup is ready. Reorder specific hardware and plugin tasks around the owner's named equipment.

**Definition of done and progress measurement.** Each feature must work through the relevant model → editor → performance → history → save/reopen → export path, including error/cancel/missing-device behavior. Keep existing assertions and explain baseline failures. Run focused checks while editing and broad gates once the candidate is ready. Current short callback probes and the two-second soak are smoke tests; they do not certify production readiness.

| Evidence | Proposed measurable gate |
|---|---|
| Session integrity | Legacy fixtures reopen with preserved semantics; interrupted saves never replace the last good project with a partial file; completed or journaled take data can be recovered within a documented checkpoint interval |
| Timing and audio | Deterministic event placement within one frame of the intended software timeline; finite output and specified tail behavior; native/reference and live/offline tolerances chosen for each algorithm |
| Callback reliability | Declare host, rate, buffer and workload; begin with a 16-track 30-minute session at 48 kHz/512 frames, then a defined 64-track two-hour workload as capacity grows; target zero deadline misses/xruns on the qualification machine, report p99 and worst, and investigate failures |
| Tracking latency | Measure cable-loopback round-trip latency and recording placement on named hardware; a proposed desirable target is under 10 ms round trip on a qualified tracking setup, conditional on device/DSP settings; never report buffer duration as physical latency |
| Plugin compatibility | Per-version scan, instantiate, UI, automation, save/reopen, bypass, sidechain/multiple outputs where supported, offline render, missing-plugin and crash recovery results |
| MIDI compatibility | Note/controller ordering, overflow behavior, disconnect recovery, software timing and independently measured hardware jitter; retain intentionally off-grid playing |
| Sound quality | Original phrase comparisons at matched loudness, artifact/tuning/transient/tail checks and documented listening feedback; no quality claim based on preset quantity |
| Usability and accessibility | Finish core tasks by keyboard; visible focus and non-color states; normal/narrow, light/dark and high-DPI layouts; semantic names/roles for painted controls; measured task time/errors against the preceding build |
| Delivery | Portable reference session opens in a fresh environment; exported stems align and reproduce the intended mix subject to documented nonlinear bus processing; cancellation leaves no falsely completed output |

The first month should be evaluated by whether the controller is dependable, recordings are recoverable, independent instruments work, and a real MIDI performance can be recorded. These are an initial focus, not a guaranteed one-month delivery commitment. Re-estimate after the controller/native-build baseline and each architecture spike. Full multichannel recording, broad plugin compatibility and mature sound design are a sustained product program.

**Durable recurring task prompt, to adopt after controller repair and priority reconciliation:**

```text
Improve Anharmonic Studio in the supplied isolated workspace. Read AGENTS.md,
the anharmonic-studio project skill, current DIRECTION, BACKLOG, MEMORY and
PRODUCER_DAW_ROADMAP_2026-09-08.md. Reconcile documentation with current source.

Select the highest-priority unblocked acceptance task. Session loss and audible
regressions take priority. Prefer independent instruments, reliable recording,
MIDI and production plugin integration over additional preset or cosmetic work.
Preserve detailed Crates and the current full-height studio workspace.

Deliver one bounded, useful slice. Reproduce it with temporary original media;
implement all affected model/editor/engine/history/persistence/export paths;
run focused verification and prepare for the controller's independent gates.
Never weaken checks to obtain a pass. Distinguish source code, tested software,
and physically validated behavior. Record accurate evidence and the next gap.

Use offscreen UI and synthetic fixtures. Keep the active chat, running studio,
system audio graph and personal projects/media untouched. Do not self-modify
the controller, schedule, skill or direction; install dependencies; spawn agents;
publish; or replay stale patches. Follow the existing controller's integration
and failure policy. If blocked, record the prerequisite and select useful
independent work; do not spend repeated cycles rewriting the same audit.

Update BACKLOG and MEMORY with the actual result. Return: musician benefit,
changed files, verification/evidence, remaining uncertainty, and next task.
```

This roadmap is ready for review and later adoption into the existing direction/backlog. Creating it does not repair or resume the paused worker. The first concrete development action is the controller integration repair, followed by durable recording recovery and independent instrument tracks.
