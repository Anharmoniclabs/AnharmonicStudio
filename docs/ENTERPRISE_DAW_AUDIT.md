# Anharmonic Studio: enterprise Linux DAW readiness audit

Original audit: 2026-08-22

**Current update: 2026-09-05.** This historical inventory predates vocal
recording/comping, streaming export, the piano roll, and fader automation.
See [the current upgrade report](DAW_UPGRADE_2026-09.md) for implemented
capabilities, validation, and remaining release gates. Items below describe
the original audit unless explicitly updated.

Product: **Anharmonic Studio**

Publisher: **Anharmonic Labs**

Current package version: `0.1.0`

This document inventories the product that exists, records the weaknesses found
in a source and UI review, and defines the tools, functions, architecture, and
interface required to grow it into a dependable pattern-first Linux DAW. It is
a technical/product audit, not formal security certification, accessibility
conformance, or trademark clearance.

## Executive verdict

Anharmonic Studio is already a coherent sampler and beat-workstation prototype:
audio can be imported, analysed, chopped, played on 64 pads, sequenced, arranged,
mixed through built-in effects, and exported. The built-in synth, stem workflow,
and offline-first design give it a real product identity.

It is **not yet an enterprise DAW**. The largest gaps are architectural rather
than cosmetic:

1. The Python audio callback is not hard real-time safe.
2. export and media caching do not scale to long sessions;
3. document state, undo, recovery, and asset portability are incomplete;
4. recording, MIDI editing, automation, time-stretch, and plugin hosting are
   absent; and
5. accessibility, Linux desktop integration, release engineering, and support
   diagnostics are not yet productised.

The right positioning is **a Linux-native, pattern-first production studio**,
not an FL Studio clone. Preserve the fast pad → pattern → playlist workflow and
build professional recording, editing, routing, and reliability around it.

## Product identity decision

### New name

**Anharmonic Studio** replaces the previous visible product name. “Anharmonic”
is musically relevant, distinctive, and broad enough for a workstation that
combines sampling, synthesis, arranging, and mixing. “Studio” immediately says
what class of software it is. The publisher name is **Anharmonic Labs** and the
distribution slug is `anharmonic-studio`.

A preliminary exact-name web search found no obvious same-category conflict.
That is not legal clearance: before a public launch, search registered and
unregistered marks, company names, app stores, package repositories, domains,
and relevant international markets. “Forge” was deliberately rejected because
MAGIX actively uses SOUND FORGE for audio software. The USPTO also recommends a
broader clearance search than an exact federal-mark lookup.

References:

- [USPTO: comprehensive trademark searching](https://www.uspto.gov/trademarks/search/federal-trademark-searching)
- [MAGIX SOUND FORGE Audio Studio](https://www.magix.com/us/support/technical-support/voluntary-product-accessibility-templates/sound-forge-audio-studio/)

### Compatibility boundaries

The display name, publisher, CLI description, launcher text, documentation, and
Python project metadata now use the new identity. These internal identifiers are
intentionally unchanged until a migration plan exists:

- Python imports remain under `mpclab` so scripts and environments keep working.
- Existing `application/x-mpclab-*` drag MIME types remain compatibility IDs.
- The repository directory remains `Projects/mpc-lab`; moving it would invalidate
  absolute paths in the existing virtual environment.
- The legacy sample-pack folder remains readable for installed starter content.

Do not invent a reverse-DNS application ID until Anharmonic Labs controls the
corresponding domain or namespace. Once chosen, keep that ID stable even if the
display name changes again.

## Current capability baseline

| Area | Implemented now | Important boundary |
|---|---|---|
| Import | SoXR-first FFmpeg import into a local 48 kHz, 24-bit WAV library, with a tuned native fallback | no managed external-file mode or batch asset policy |
| Analysis | BPM, onset, hit-family, loop, and drop detection | heuristic results need correction tools and analysis caching |
| Separation | optional cancellable local Demucs jobs | model lifecycle, disk estimates, and failure recovery need product UI |
| Sampler | 64 pads, trim/tighten, peak normalize, gain, pan, pitch, envelope, loop/gate, reverse, choke, routing | no multisampling, round robin, zones, modulation matrix, loop crossfade, or disk streaming |
| Sequencing | pattern steps, velocity, divisions, swing, fills, humanise, live pad capture | no piano roll, per-note expression, groove templates, or event list |
| Synthesis | eight-voice built-in subtractive synth and arpeggiator | no patch library browser, modulation automation, or MIDI mapping |
| Arrangement | pattern/audio clips, loop range, basic tools, mute/solo, track reorder | no tempo map, markers, folders, crossfades, comping, automation lanes, or elastic audio |
| Mixer | eight fixed tracks, fixed inserts, two sends, cue bus, dBFS RMS/peak meters, master processing | no dynamic graph, buses, sidechains, separate cue output, reorderable plugins, or delay compensation |
| Export | single 24-bit WAV mixdown | synchronous, memory-heavy, no stems/queue/formats/loudness options |
| Projects | JSON state, version marker, atomic project replacement, dirty autosave | no Save As/history/bundle/relink/migration service or full schema validation |
| UI | native Qt, dark/light/accent themes, keyboard workflow, custom visual editors | limited accessibility, scaling, persistence, menus, and view virtualization |
| Linux delivery | source launch through `uv` | no installable package, desktop entry, AppStream data, MIME registration, or stable app ID |

## Hardening completed during this audit

- Centralised the visible product name, slug, and publisher.
- Added project-format versioning, rejection of unsupported future formats,
  atomic project writes, and path-safe project/export filenames.
- Kept editing and offline rendering available when the audio device cannot
  open, with an explicit offline status.
- Expanded dirty tracking and undo snapshots across many sampler, sequencer,
  mixer, synth, marker, and project edits; cleared undo history on project load.
- Made library removal recoverable by moving unreferenced clips to a local
  trash folder and blocking removal while project references remain.
- Bounded live pad polyphony at 64 voices.
- Added four persistent 48 kHz buffer profiles, rolling callback-tail/xrun
  diagnostics, cache-only live sample lookup, and a repeatable DSP benchmark.
- Added SoXR-first 24-bit import with a native fallback, non-destructive pad
  normalize/tighten actions, an independent preview/metronome cue bus, and
  dBFS-scaled RMS/sample-peak meters.
- Moved tone impulse-response/filter-plan preparation out of the callback and
  bounded offline pad interpolation scratch to 16,384 frames (about 1.1 MiB).
- Added a preallocated latest-block vocal monitor ring, reusable callback mixer
  scratch, saved loop crossfades, rate-aware sinc interpolation for export, and
  fixed-block WAV writing that removes song-length mix/track-bus allocations.
- Kept stateful track effects consuming silence so delayed internal state does
  not freeze and reappear on the next sound.
- Corrected keypad release after a bank change, master-fader synchronisation,
  preset re-selection, unsnapped ruler seeking, stale detected-slice labels,
  filtered-lane resizing, pad-flash repaint expiry, track-colour menu swatches,
  and misleading help text.
- Added regression coverage for persistence, safe filenames, audio-device
  failure, cross-bank pad release, voice limits, and recoverable library trash.

These are valuable guards, but they do not replace the deeper systems below.

## P0 risks: resolve before a public production beta

### 1. Real-time engine boundary

Tone impulse responses and their fixed-block filter spectra are now prepared
outside the PortAudio callback. The callback still performs Python object/list
work, NumPy allocation, FFT convolution, voice construction, event collection,
and a per-sample synth filter loop. Live pad lookup is cache-only—a miss is
silenced and counted rather than decoded—but the GUI and callback still read
and mutate the same `Project` object without a real ownership boundary. The
existing command queue covers transport and note events, but not every state
change, and it is unbounded.

Required design:

- Move the hard real-time render graph to a native Rust/C/C++ library or another
  demonstrably allocation-free engine boundary.
- Build immutable engine snapshots away from the callback and swap them only at
  block boundaries.
- Use bounded single-producer/single-consumer rings for commands, events,
  parameter ramps, meters, and transport state.
- Preallocate voices, buses, DSP scratch buffers, and event storage. Define and
  expose deterministic voice-stealing rules.
- Resolve samples to validated, preloaded/streamable handles before scheduling;
  never decode, allocate, log, lock, or call Qt from the callback.
- Split blocks at loop, seek, tempo, time-signature, punch, and automation
  discontinuities so boundaries are sample accurate.
- Make xruns, late blocks, graph rebuild time, and plugin overruns observable in
  a local diagnostics panel and soak tests.

### 2. Scalable offline rendering

The WAV writer now consumes fixed-size render blocks, so its mix and eight track
buses are bounded by callback size rather than arrangement duration. Asset and
event lists still scale with the project, choke preparation is quadratic in
event count, and the export dialog remains synchronous, calls `processEvents()`,
cannot cancel, and temporarily mutates live engine mode.

Replace it with a render service that:

- captures an immutable project/asset/plugin snapshot;
- renders fixed-size blocks directly to a temporary output file;
- supports cancellation, tail policy, progress, and atomic finalisation;
- renders stems serially or with a bounded memory budget;
- resolves choke decisions in one chronological pass;
- never pumps nested GUI events or touches the live engine; and
- can resume/report a failed render without corrupting its destination.

### 3. One document command model

The main window is a large controller that constructs UI, mutates the model,
starts jobs, saves files, and exports. Undo remains a bounded list of complete
JSON snapshots with no redo; continuous controls can generate poor transaction
boundaries and some mutations can still bypass it.

Introduce a document controller and command registry:

- every edit is a typed command with `redo`, `undo`, merge/transaction rules,
  display text, and optional engine delta;
- use `QUndoStack` clean indices as the source of truth for modified state;
- begin continuous edits on press and commit one command on release;
- derive menus, shortcuts, tooltips, help, command search, and enablement from a
  single `QAction`/command catalogue; and
- keep view state separate from saved musical state unless explicitly intended.

### 4. Project and asset integrity

Project JSON references IDs in a shared source-tree library. Whole decoded and
reversed clips are cached indefinitely, metadata writes are not atomic, and
library IDs read from disk require strict validation before path construction.
There is no missing-media resolver or portable project.

Required project service:

- a fully validated, migrated schema with type/range/count/invariant limits;
- project UUID, created/modified timestamps, app/build version, sample rate,
  plugin requirements, and a media manifest with hashes;
- **Package Project** / consolidate, copy-versus-reference policy, unused-media
  cleanup, relink search, replace, and broken-media reporting;
- atomic metadata and autosave writes plus multiple rotating recoveries;
- bounded decoded/reverse caches with LRU eviction and memory/disk budgets;
- persistent multiresolution waveform/analysis caches; and
- chunked or memory-mapped playback for long recordings and songs.

### 5. Common background-job service

Selection decode/overview construction, parts of analysis/import, synth print,
and export can block or re-enter the GUI. Consolidate all non-trivial work under
one service with immutable inputs, cancellation tokens, bounded concurrency,
structured progress/errors, temporary outputs, and queued Qt completion signals.
Closing a document must either cancel or safely detach its jobs.

### 6. Project lifecycle and failure recovery

Add New, Save, Save As, Save Copy, recent projects, templates, overwrite checks,
dirty-document prompts on New/Open/close, an autosave recovery browser, and a
visible failed-save state. A normal close must never silently swallow the only
recovery-write failure. Record the active save path separately from the project
display name.

### 7. Release and supply-chain foundation

The Python project is currently non-packaged and assumes a writable source
checkout, `uv`, and local runtime folders. Before external distribution, add a
license, reproducible build, locked dependency review, model/license inventory,
SBOM, signed artifacts, vulnerability response policy, local crash logs, and a
supported-version matrix. Treat FFmpeg inputs, project files, downloaded models,
and future plugins as untrusted content with quotas and validation.

## Required production functions and interface

| Workspace/tool | Functions the product needs | Priority |
|---|---|---|
| Project centre | New/Open/Save/Save As, recent/templates, versions, recovery, package/consolidate, relink, notes, dependencies | P0 |
| Preferences | PipeWire/JACK/ALSA device, sample rate, custom buffer/quantum, input/output ports, latency calibration, MIDI devices, plugin paths, UI scale, files, autosave | P0 |
| Transport | pattern/song, seek, loop, tempo/time signature, tap, metronome, count-in, pre-roll, punch, follow, sync, calibrated round-trip latency | P1 |
| Browser | filesystem and indexed database, folders/tags/favourites, BPM/key/type/duration filters, synced preview, batch import, rename, trash, locate/relink, duplicates, plugin/preset tabs | P1 |
| Sampler editor | transient/grid slicing, fades/loop crossfades, reverse, loop points, zones/layers, round robin, root key, envelopes, modulation, tempo sync, destructive action history | P1 |
| Audio editor | sample-accurate trim/slip, fades/crossfades, gain, silence, reverse, resample, stretch/warp, pitch, spectrogram, regions, send to sampler | P1 |
| Channel rack | instruments/audio generators, step controls, per-channel routing, grouping, cloning, swing offsets, graph/editor access | P1 |
| Piano roll | duration/pitch/velocity/pan/release, CC and MPE, quantize, humanise, grooves, scale highlighting, chords, strum, legato, event list | P1 |
| Recording | input arm/monitor, pre-roll/count-in, punch, loop takes, take lanes, comping, latency compensation, file allocation and dropout indication | P1 |
| Playlist | markers, tempo/time-signature and automation tracks, folders/groups, resizing, clip lock/mute, slip/stretch, fades/crossfades, take lanes, follow playhead | P1 |
| Automation | lanes and clips, stepped/linear/curve points, recording modes, touch/latch/write/read, parameter search, smoothing and safe deletion | P1 |
| Mixer/routing | dynamic tracks, buses/groups, routing matrix, sidechains, pre/post sends, reorderable insert slots, wet/dry/bypass, separate cue outputs, gain-staging tools | P1 |
| Plugin manager | LV2 first, then CLAP/VST3; scan/cache/rescan, formats/paths, validation, quarantine, bridge process, UI embedding, presets, MIDI learn, PDC | P1/P2 |
| Render/export | destination/range, WAV/FLAC/OGG, rate/bit depth, dither, mono/stereo, normalization/true peak/loudness, tails, mix/stems, metadata, queue | P1 |
| History/actions | undo/redo, cut/copy/paste, duplicate, consolidate, freeze/bounce, editable shortcuts, command palette, macro-safe action IDs | P1 |
| Diagnostics | audio graph and ports, plugin scan report, long-term xrun/timing history, cache/disk usage, missing assets, logs, support bundle, safe mode | P0/P2 |

## UI and accessibility weaknesses

### State and navigation

- The app needs conventional File/Edit/View/Transport/Tools/Help menus and a
  persistent status for active project path, dirty state, audio device, and
  recording risk.
- Dockable/resizable panels, named workspaces, persistent splitter geometry,
  zoom, visible tabs, and shortcuts are needed. Do not force maximized/normal
  window states on Wayland or tiling compositors.
- Selecting a track, channel, clip, pad, automation target, or plugin should
  update one predictable inspector instead of rebuilding broad widget trees.
- Destructive actions need clear dependency-aware confirmation and undo; routine
  edits should remain immediate and non-modal.

### Accessibility and scaling

The custom-painted pads, step cells, playlist clips, waveform markers, meters,
EQ curve, and piano keys expose little or no semantic child structure to a
screen reader. Focus styling is weak, some small secondary text misses 4.5:1
contrast, several labels use 6.5–8 pt fonts, and fixed 18–30 px controls will not
survive large text or 300% scaling.

Required work:

- expose named roles, values, states, actions, and virtual children through Qt
  accessibility APIs or semantic item-view models;
- provide complete keyboard navigation, visible focus, logical tab order,
  buddies, announcements, and non-pointer alternatives;
- add application scale, high-contrast and colour-vision-safe palettes, reduced
  animation, and a large-target mode;
- replace font-dependent emoji controls with theme-aware SVG icons; and
- test keyboard-only and screen-reader workflows at 100%, 150%, 200%, and 300%.

Target WCAG 2.2 AA for applicable desktop interactions and document the tested
assistive technologies rather than claiming compliance from colour checks alone.

### UI performance

- Playlist painting traverses the full arrangement at playback refresh rates.
  Cull to the damaged viewport, index clips by time, and cache tiled waveforms.
- The step grid repaints every visible cell when only the playhead changes. Cache
  the static grid and invalidate the old/new playhead strips.
- The waveform performs multiple per-pixel passes during playback. Persist peak
  pyramids and cache paths at each zoom level.
- Browser search rebuilds all rows and icons per key. Use a model plus filter
  proxy, cached icons, paging, and asynchronous metadata.
- Mixer changes currently cause broad inspector refresh work. Emit targeted
  property notifications.
- Consolidate many independent 30–60 Hz timers into a visibility-aware UI clock
  and stop animation for hidden panels.

## Target architecture

The core rule is ownership: the document thread owns editable state, the audio
thread owns render state, and workers own long jobs. They exchange bounded,
versioned messages rather than sharing mutable models.

```text
Qt presentation + accessibility + QAction registry
                    |
          Document command dispatcher
          /          |             \
 undo/redo      project/media      immutable engine snapshots
                    |                         |
          cancellable job service       native RT audio graph
     decode/analyse/separate/render      voices/DSP/plugins/transport
                    |                         |
            atomic asset store       bounded commands + meter events
```

Suggested modules:

- `document`: validated model, commands, selections, undo, migrations, dirty state
- `media`: import, hashes, stream handles, peaks, analysis, relink, cache budgets
- `jobs`: cancellation, progress, scheduling, temporary outputs, error objects
- `engine`: native graph, transport, event scheduler, parameter ramps, meters
- `plugins`: scanners, database, helper processes, UI bridge, PDC
- `persistence`: project bundle, autosave generations, settings, recent files
- `presentation`: small Qt controllers/views driven by document notifications
- `diagnostics`: logs, timing, xruns, plugin failures, environment/support report

## Linux platform and integration choices

| Concern | Recommended direction |
|---|---|
| Audio | support PipeWire as the default desktop path, JACK for studio graphs, and ALSA where appropriate; expose devices/ports rather than hardcode one stream |
| MIDI | ALSA sequencer or a proven cross-platform MIDI layer, with hotplug, timestamping, MIDI clock/MTC, mappings, and controller feedback |
| Plugins | LV2 via mature discovery/UI libraries first; add CLAP and VST3 behind one internal ABI and isolate third-party DSP in helper processes |
| Stretch/resample | evaluate a maintained, quality-tiered library and its licence; perform preview and render modes outside the hard RT callback |
| Files | use `QStandardPaths`, XDG data/config/cache/state locations, and `QSettings`; keep projects user-selected and portable |
| Desktop | stable reverse-DNS ID, `.desktop`, scalable icon, AppStream metadata, project MIME XML, file associations, and an About/build view |
| Packaging | produce reproducible signed native packages and one portable format; test Flatpak/plugin-path constraints before making it the studio build |
| Display | test Qt on Wayland and X11, fractional scaling, tiling compositors, touch, multi-monitor, and software rendering |

## Verification programme

Keep the existing fast unit suite, then add:

- golden DSP vectors and tolerance-qualified offline-render comparisons;
- deterministic project round trips and migrations for every released schema;
- fuzz/property tests for project JSON, metadata, MIDI, and media boundaries;
- null-device engine tests at several sample rates and block sizes;
- callback allocation/lock assertions, xrun stress, dense-event overload, and
  multi-hour playback/record/render soak tests;
- sample-accurate loop, seek, punch, tempo-change, PDC, and automation tests;
- forced disk-full, permission, missing-media, device-loss, and plugin-crash tests;
- plugin scan/restore corpus tests in isolated helpers;
- keyboard/accessibility and 100–300% scale UI tests;
- performance budgets for project open, waveform creation, timeline scrolling,
  memory/cache use, render throughput, and shutdown recovery; and
- CI on supported Ubuntu/Debian and Fedora-family releases, plus at least one
  rolling distribution, under Wayland and X11 where practical.

## Delivery gates

### Gate 0 — trustworthy document

- command-based undo/redo and clean state
- Save As, dirty prompts, recovery browser, schema validation/migrations
- portable assets/relink and cache budgets
- unified cancellable jobs and streamed export
- reproducible tests for failure paths

### Gate 1 — trustworthy engine

- native/preallocated render graph and bounded messages
- sample streaming and sample-accurate transport boundaries
- audio/MIDI preferences, recording, latency compensation, diagnostics
- piano roll, elastic audio, and automation with parameter smoothing

### Gate 2 — production ecosystem

- dynamic routing, buses, sidechains, PDC, and scalable mixer
- isolated LV2 hosting, then CLAP/VST3 according to validated demand
- plugin/preset browser, MIDI learn, freeze/bounce, stem render queue
- long-session performance and crash-containment gates

### Gate 3 — enterprise release

- accessible scalable UI and persistent workspaces/keymaps
- signed cross-distribution packages, desktop/AppStream/MIME integration
- licence/SBOM/security/update/support policies and local diagnostics bundle
- documented compatibility, migration, backup, and deprecation guarantees

## Release definition of done

Do not market a build as an enterprise DAW until all of these are true:

- no allocation, disk I/O, unbounded work, GUI call, or mutable-document access
  occurs in the hard audio callback;
- a one-hour dense project plays, records, saves, reopens, and renders within
  published CPU/memory/xrun budgets;
- device loss, disk full, corrupt projects, missing media, and plugin crashes do
  not lose the last recoverable user state;
- undo/redo covers every document edit with deterministic results;
- projects can be packaged and reopened on another supported machine;
- latency/PDC and exported audio pass sample-accuracy and golden-render tests;
- all primary workflows are keyboard operable and tested with supported screen
  readers and scaling levels; and
- install, upgrade, rollback, diagnostics, and removal are documented and tested
  on every supported Linux distribution.
