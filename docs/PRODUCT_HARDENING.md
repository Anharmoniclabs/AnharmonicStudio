# Product hardening

## Audio setup and recovery

The first visible launch opens **Audio → Audio setup + latency** until a setup
is accepted. The recommended starting profiles are:

- 512 frames while builds or heavy sessions are running;
- 256 frames for normal production;
- 128 frames only for experimental, light live tracking.

The optional latency test requires a physical cable from the selected output
to the selected input. Its round-trip result is saved in application settings
and in `Project.vocal_record.input_latency_ms`. Newly recorded takes use
that value when they are auto-placed; manually placed library takes do not.

An xrun or callback p99 at 85% of the block deadline adds a visible warning and
recommends the next buffer profile. The app never changes a live buffer without
the user choosing it.

Crash recovery is no longer silently applied. The chooser can restore the
autosave and its history, keep it for later, or start clean while archiving the
recovery. Undo and redo keep 40 atomic serialized project snapshots. Session
history lives beside the autosave; a named project gets a hidden history
sidecar and restores it when reopened.

## Vocal take management

The vocal workspace can rename, duplicate, and safely move unused takes to the
recoverable library trash. Tuned renders retain their dry parent ID, enabling
explicit dry/tuned A/B preview. Playlist placement is undoable and redoable.
Deletion is blocked while a take has Playlist placements or tuned children.

## Deliberately deferred boundaries

- A multi-take comp editor needs a first-class comp/region model and waveform
  UI; faking comping by destructively splicing library files was rejected.
- Library imports, take-file rename/duplicate, and recoverable-trash moves are
  durable asset operations, not project snapshots. Project references and
  generated-project replacement participate in undo/redo, but asset-level undo
  needs a separate library transaction journal.
- Streaming export, callback allocation/lock cleanup, monitor/mixer buffer
  ownership, and loop crossfades belong to the audio-engine hardening branch.
- Streamed vocal capture and processing cancellation belong to the vocal
  recorder/processor branch.
- Hour-long soak, USB hot-unplug, PipeWire hardware, corruption/fuzz, and full
  workflow GUI automation remain release-gate work because they require real
  hardware or long-running CI jobs rather than unit coverage.
