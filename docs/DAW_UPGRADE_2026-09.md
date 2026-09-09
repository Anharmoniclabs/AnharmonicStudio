# Anharmonic Studio / MPC Labs — September upgrade

Reviewed 2026-09-05. This is the current implementation report; the August
enterprise audit is retained as a historical architecture inventory.

## Research and product direction

The four reference products offer complementary workflows. The following
priorities are design judgments informed by their official documentation,
not claims of feature parity or copied implementations.

| Reference | Documented workflow | Application to this workstation |
|---|---|---|
| [GarageBand for Mac](https://www.apple.com/mac/garageband/) | Instrument presets, quick recording, note editing, mixing, and multiple takes | Keep a direct path from a musical idea to a recorded, editable pattern. Preserve the existing starter kits, synth presets, and vocal take workflow. |
| [FL Studio piano-roll manual](https://www.image-line.com/fl-studio-learning/fl-studio-online-manual/html/pianoroll_menu.htm) | Note selection, quantize, transpose, duplication, chord tools, and MIDI exchange | Implement an editable piano roll, precise note controls, chords, quantize, and history. External MIDI and score exchange remain future work. |
| [Ableton automation manual](https://www.ableton.com/en/manual/automation-and-editing-envelopes/) | Timeline envelopes and an explicit relationship between automation and manual controls | Add linear/step envelopes, lane bypass, mixer read indication, and consistent arrangement playback/export. |
| [Pro Tools](https://www.avid.com/pro-tools) | Recording, audio editing, clip gain, mixing, and production delivery | Preserve nondestructive clip editing and build isolated, cancellable rendering with atomic output publication. Advanced routing, plugin hosting, and hardware validation remain separate release requirements. |

## What is connected now

| Area | Before the interrupted task | Implemented result |
|---|---|---|
| Synth composition | Live synth and arpeggiator; printing audio to a pad was needed for sequencing | Pattern notes with pitch, start, gate duration, velocity; drag/draw/resize, multiple selection, transpose, copy/paste, quantize, chord tools, and fit-to-pattern |
| Performance capture | Pad recording and separate vocal capture | Onscreen and musical-typing synth notes record their actual held duration in Pattern mode |
| Playback | Patterns scheduled pad events | Notes schedule the synth at sample offsets, repeat through pattern clips, obey arrangement row/clip mute and solo, and use the synth's mixer route |
| Editing continuity | Existing project history and recovery | Notes survive save/load, undo/redo, duplicate/double, recovery, and pattern changes; note-only projects count as musical content |
| Arrangement overview | Pattern miniatures showed pad steps | Pattern clips also show their pitched-note contours |
| Automation | Static track/master faders | 17 supported targets: master level, 8 track gains, 8 track pans; linear or step interpolation, numeric and graphical editing, read/bypass, undo, persistence, playback, and export |
| Mixer feedback | Static fader positions | Automated faders show read values and an `A` level marker; automated gain/pan controls are disabled until the lane is bypassed, while saved manual values remain unchanged |
| Export | Streaming blocks written from the GUI using the live engine | Separate snapshot and renderer, background progress, cancellation, 16/24-bit PCM or 32-bit float WAV, adjustable effect tail, missing-media errors, and atomic final file publication |
| Workspace | Dense shared project/transport row, limited menus | Separate project and transport strips; File/Edit/View/Transport/Tools/Help menus; eight navigable workspaces; stronger surface/text separation; visible focus; scrollable editor toolbars |
| Distribution | No source license file | GPL-2.0-or-later source license, explicit third-party/media exclusions, and a free-source / paid-official-build distribution policy |

No user media was modified or reimported. New project saves use format 2 so
older builds reject them instead of silently discarding new musical data.
Format 0/1 projects remain readable. No new runtime dependency was needed.

## Visual review

These images were captured from the actual Qt widgets with an original
synthetic note arrangement. The offscreen review does not open a desktop
window or start an audio device.

- [Piano roll, dark](previews/piano-roll.png)
- [Piano roll, light](previews/piano-roll-light.png)
- [Automation](previews/automation.png)
- [Arrangement](previews/arrangement.png)
- [Mixer](previews/mixer.png)
- [Narrow workspace](previews/narrow.png)

The editor toolbars scroll at narrow widths so labels and controls remain
usable. Piano notes and automation points remain native editable objects;
these images are review artifacts, not UI backgrounds.

## Verification

Final local quality run: **289 passed, 3 hardware tests skipped**. Lockfile,
lint, formatting, and compilation passed. The pad-voice callback benchmark
reported p99 2.01 ms at 512 frames and 1.89 ms at 256 frames. The two-second
512-frame smoke soak recorded 3,400 iterations, no late callbacks, and no RSS
growth. These are short software measurements, not a long-session guarantee.

`bash scripts/quality.sh` checks the dependency lock, lint, formatting, Python
compilation, tests, callback benchmark, and short callback soak. New integration
tests cover validated musical data, legacy project loading, repeat and gate
boundaries, mute behavior, sample-level live/offline agreement at equal buffer
settings, automation bypass, held-note recording, undo/redo, pattern duplication,
real exported audio, WAV encoding, independent export snapshots, missing media,
and cancellation before and during a render.

The comparison test found and fixed a floating-point truncation that made some
live synth events arrive one sample earlier than their exported counterpart.
An additional insert-chain comparison verifies that a zero automated fader
does not erase compressor/filter history before the fade opens again.
Headless tests verify software behavior; the hardware tests are opt-in and were
not used to certify a connected interface or measured round-trip latency.

## Professional release gates still open

This remains a developing Linux DAW. The work above does not establish
enterprise readiness or parity with four mature commercial workstations.

| Requirement | Current boundary | Evidence needed before claiming completion |
|---|---|---|
| Audio scheduling | Python/NumPy callback, bounded polyphony, block-based transport and DSP; not an allocation-free native engine | Sustained dense-session tests, explicit sample-boundary scheduling across loops/seeks, native rendering architecture, underrun recovery, and measured interface latency |
| Full MIDI production | One shared built-in synth patch, editable pattern notes, typing/onscreen capture | ALSA MIDI inputs, device hotplug, sustain/CC/pitch bend, MIDI files, multiple instruments, expression, input timing compensation, and clock synchronization |
| Musical timeline | 4/4, fixed project tempo, 1–8-bar pattern controls | Tempo/time-signature maps, groove templates, independently adjustable long MIDI clips, note chasing when seeking into held notes, and tempo-aware time stretching |
| Automation depth | Arrangement gain/pan read and bypass; no fader recording | Touch/latch/write modes, plugin/synth parameters, controller mapping, smoothing and transitions; existing tone-kernel swaps must be made safe for sweeps |
| Recording and editing | Existing vocal capture, takes/comping, sample and clip editing | General multichannel track recording, punch/preroll, disk streaming, input routing/monitoring policies, editable fades and warp markers |
| Mixing and plugins | Eight fixed mixer buses with built-in inserts and shared sends | LV2/CLAP/VST3 hosting, isolated plugin scanning, state restoration, latency compensation, arbitrary bus routing, groups, sidechains, multichannel outputs |
| Delivery | Bounded-block stereo 48 kHz WAV render; integer quantization has no dither selection | Stem export with routing policy, other sample rates and formats, dither, loudness/true-peak measurement, normalization, metadata, and export queue |
| Large sessions | Source audio still decoded/cached as whole files | Bounded media caches, streaming playback, independent render processes, memory and performance soak with long projects |
| Portability and release | Local library IDs and linked sample-pack paths, GPL source license, paid-official-build policy, existing CI | Collect/relink project media, reproducible signed installers, dependency/model notice audit, SBOM, screen-reader and keyboard-only testing, recovery fault injection, distribution/hardware matrix, and support diagnostics |

No placeholder plugin controls, simulated MIDI connections, unsupported export
formats, or readiness badges were added. The next architectural work should
address scheduling and session state boundaries before expanding device/plugin
hosting and large multitrack recording.
