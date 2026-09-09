# Studio workspace implementation

Based on the owner's September 8 reference image and instructions, on top of
`da6d3a0` (the restored interface). The remote was fetched and the local branch
matched `origin/main` before work began.

## Layout and appearance

- One navigation row: Song, Beats, Notes, Sampler, Instruments, Mix.
- The center is the full-height active editor. There is no fixed bottom editor
  or footer navigation. Browser and Inspector are independently hideable and
  resizable; Focus returns their previous visibility and widths on exit.
- The detailed illustrated crates remain. Vocals in the library are available
  samples; recording a performance does not require choosing a library vocal.
- Dark surfaces follow the supplied reference. COLOR remains pinned at the
  top right, including narrow and focused layouts. Every Studio selection uses
  the project's chosen accent and responds to light/dark switching.
- Song/beat generation tools are optional under Tools. Master output is part
  of Mix. Takes, comp and tuning are tools, rather than another primary mode.
- Keyboard commands and older internal navigation calls select the same
  persistent editors. Navigating does not reparent them or expose legacy tabs.

## Record directly into a track

1. Select a track header and open Inspector if it is hidden.
2. Choose Audio input for a microphone or external instrument, or Instrument /
   sample notes for on-screen playing and musical typing. Choose the audio
   mixer destination and input setup as needed.
3. Arm with the track's R button or Arm track in the inspector.
4. Press the top Record control. The chosen count-in runs before capture at
   the current playhead. Stop saves the take directly to the armed lane.

The destination is captured by track identity: selecting or reordering other
tracks does not redirect a take. Recording source and audio destination survive
save/reopen. Arming itself is temporary. Audio retains the existing PCM24
capture path and measured input-delay compensation; notes retain their played
timing and expand into a new pattern rather than wrapping into the old pattern.
The live audio region displays sampled input peaks, then the saved waveform.

A finished take has one undo step, including its library asset and placement.
Cancelled count-in and empty takes add no history. A failed media save retains
the captured data in memory and exposes Retry save. Project replacement, undo,
tempo changes and seek are guarded during a take. Track capture and the older
advanced take recorder cannot open inputs at the same time.

## Boundaries

- One armed recording destination at a time. The existing microphone recorder
  captures mono input; simultaneous multichannel/interface recording is not
  introduced here.
- Built-in synth notes still share one instrument patch. Sample notes use the
  existing stable pad slots. Independent synth instances and hardware MIDI
  recording require further engine integration.
- Failed saves remain in memory until retried; they are not crash recovery.
- Device-free tests verify capture integration and rendered output. Physical
  microphone/interface latency and monitoring have not been tested in this pass.
- The running desktop studio and chat were left alone. Source changes appear
  on the next normal studio launch.

## Review and evidence

Reviewed the pushed layout/restoration history and the current shell, theme,
navigation, arrangement, capture, note-entry, persistence, undo and focus paths.
Concurrent clipboard/selected-sample editing changes were preserved; the full
checks run against the combined checkout.

`scripts/render_reference_workspace.py` renders the actual Qt widgets with
temporary synthetic media, in-memory settings, audio startup disabled and the
offscreen platform. Receipts are in `docs/previews/workspace-2026-09-08/`:
amber and purple dark layouts, teal light layout, narrow Song, full Focus,
Sampler, Beats, Notes and Mix.

The focused recording suite is `tests/test_track_recording.py`; it covers
track identity across selection/reorder, cancellation, missing input, failed
save/retry, off-grid synth/sample notes, pattern growth, undo/redo, persistence,
audible channel routing on export, project-transition guards, stable editor
ownership and the pinned narrow-window color picker.

Validation results are recorded in `docs/agent/MEMORY.md` after completion.
