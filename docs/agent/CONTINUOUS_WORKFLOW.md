# Continuous whole-DAW refinement

Owner direction, September 8 overnight: work continually, one pass after another,
refining the complete studio across its paths, functions and uses. The earlier
twice-daily proposal is superseded. The controller runs one isolated worker at a
time; a successful verified integration is followed immediately by the next pass.
The nightly timer starts the loop at 22:00 local time if it is not already running.
Once started it continues while the computer is available until paused or held by
its failure/prerequisite/artifact policy. It does not stop automatically at dawn.

This is ongoing engineering, not a claim that every function can be fully inspected
or improved in one night. Build progressively verified coverage, while delivering
audible or usable changes each pass. The full [DAW roadmap](../PRODUCER_DAW_ROADMAP_2026-09-08.md)
defines the larger architecture and compatibility milestones.

## Each pass

1. Read current DIRECTION, BACKLOG, MEMORY, this ledger and the controller's previous
   outcome. Inspect current code and retained failure evidence. Preserve unfinished
   local changes and the recent studio workspace.
2. Use the controller-assigned focus. Session-loss/audio regressions and a previous
   failed integration gate override rotation. If the area needs unavailable hardware
   or dependencies, implement a useful software prerequisite or a different unblocked
   path and record the reason. Do not repeat an audit without producing new evidence.
3. Choose one real musician task and trace the affected entry point, UI handler,
   model/state change, engine/DSP path, history, persistence and export. Inspect
   happy path plus boundary/error/cancel/missing-resource behavior.
4. Reproduce with original synthetic fixtures; implement a complete bounded slice;
   test behavior and actual audio output when relevant. For visuals inspect actual
   offscreen Qt widgets. Do not invent hardware results or subjective listening.
5. Run focused checks, then let the controller run its independent full gates.
   Never weaken checks, loosen timing thresholds or hide failures to claim completion.
6. Update BACKLOG, MEMORY and this ledger with precise paths/functions/use covered,
   test or render receipt, remaining gaps and next action. Return the structured
   result. The controller records integration separately from the worker's claim.
7. The next pass starts immediately after success. Held passes retain work and use
   a short failure backoff; three consecutive failures pause the loop for inspection.

## Rotating coverage ledger

Initial entries are source-review findings. They are not new runtime validation.
Replace/add rows as evidence accumulates; retain references to completed work.

| Focus | Paths/functions/use to trace | Initial next acceptance | Evidence/status |
|---|---|---|---|
| Recovery and recording | `vocal.py` capture callback/writer/finalize; `ui/track_recording.py`; record → stop/failure → recovery → placement | Retain durable capture audio on failure; preserve dropout positions; recover without duplicate takes | Pending; current take/finalize limitations documented in roadmap |
| Tracks and engine | `model.py`, `channel_registry.py`, `engine.py`, `native/src/engine.cpp`; independent sound → route → history/reopen → render | Stable instrument IDs; independent patches; device-free native render lifecycle and parity | Pending; separate native backend is not current app engine |
| MIDI | `midi_io.py`, native event queue, Notes and capture; input → timed note/controller → edit → file/playback | Deterministic timestamps/overflow/note-off ownership; sustain/bend; reconnect and saved recording | Pending; hardware stage must be named separately |
| Plugins and routing | `plugin_registry.py`, native LV2 lifecycle/control code, mixer; scan → insert → state/UI → automate → render/recover | Complete isolated validation and state lifecycle; VST3 adapter after host contract | Pending; discovery/prototype do not establish full hosting |
| Sound design | `ui/synth.py::_set_patch`, arp/routing handlers, `synth.py`, `orchestra.py`; gesture → sound → undo/reopen/export | Precise analog cutoff with grouped undo/reset/Escape; expressive orchestral changes | Pending; analog edit snapshot gap identified by source review |
| Arrangement and sampling | Playlist, Notes, SampleWorkflow, waveform, pad voices; import/chop → perform → vary → arrange | Note chase/loop edges, reversible audition, editable tempo fit/fades | Existing workflow tests; next missing cases require receipts |
| Mixing and delivery | `fx.py`, mixer/automation UI, `export.py`, worker; route → process → automate → save → mix/stems | Smooth parameter changes, explicit automation state, cancellation and alignment | Existing WAV path; buses/stems/plugin PDC need separate slices |
| UI and accessibility | Studio/nav/inspector, piano/playlist painted regions, controls; keyboard → select/edit → undo → finish | Complete keyboard control, semantic regions/notes, readable narrow/high-DPI states | Existing saved Qt renders; assistive-technology checks pending |

For each new receipt record:

```text
Pass / focus:
Musician use and before → after:
Paths and functions actually traced/changed:
Entry/model/engine/history/save/export links checked:
Boundary/error/cancel cases:
Tests, original audio render and/or actual widget image:
Software measurements (host/rate/buffer/workload/duration):
Hardware/listening status:
Remaining uncertainty and next bounded action:
```

After a complete rotation, include an end-to-end reference production in the next
suitable pass: original drum/sample phrase → bass/keys → recorded synthetic take →
arrangement → mix → save/reopen → export. Add newly introduced paths to this ledger.
Use the full tests for regression coverage, but do not equate line coverage or a
passing short benchmark with complete musical or hardware correctness.

## Operation and boundaries

The controller has one loop-wide lock, isolated source copies including uncommitted
work and native source, independent verification, mode-preserving integration,
patch/backup retention and conflict detection. Source is applied only after all
gates pass and the original still matches the snapshot. Existing user Git staging,
projects, library, exports and running app are preserved. Changes appear on the
next normal application launch; the worker never restarts it.

Use `anharmonic-agent status` to see the current pass and focus. Use
`anharmonic-agent pause` to prevent integration and further passes without killing
the running worker. To resume, clear pause and start the dedicated service as
described in `automation/README.md`; verify the exact service before any control
action. Never stop/restart the active chat or a shared Codex server.

The computer must stay awake and the Codex account/network/dependencies must remain
available. The current model/authentication settings are inherited. Artifact limits
and repeated failures may pause work; retain diagnostics rather than repeatedly
overwriting or replaying failed candidates. Hardware, packages and protected
controller/release changes stay separate from unattended application development.
