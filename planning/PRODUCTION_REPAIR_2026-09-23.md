# Production repair — 2026-09-23

User report: separate tracks unexpectedly share instruments/VST patches; controls
lag or fail, recording and MPC sampling are unreliable, and repository/release
state is confusing. This is an active repair plan, not a release attestation.

## Visible supervision

Launch `.venv/bin/python scripts/swarm_room.py` from this checkout. The original
block-character control room reads local role status files under
`automation/production_swarm/` every 1.5 seconds. Select a station to inspect its
assignment, constraints, agent identifier, last report and evidence. Motion only
indicates reported activity. Closing the viewer does not stop agents. It contains
no task executor or network listener; dispatch remains in the supervising chat.

Ten roles: ATLAS research, FOREMAN integration, PATCH instruments/VST, PRISM UI,
LUMEN UX direction, BUS mixer/mastering, TICK sequence/beats/notes, SPLICE
sampling/recording, ANCHOR stability/repository, SENTINEL independent audit.
Three specialist slots run at once; other roles remain visibly queued.

## Confirmed starting findings

- Native instrument IDs exist, but normal sound creation/selection does not
  expose them adequately. A new mixer channel is only an output route.
- External instrument hosting, parameter controls and export use one shared
  instrument slot. Isolation must cover state, events, plugin lifecycle, live
  rendering, save/load and export together.
- Printing the selected synth to a pad reads the primary synth patch instead.
- Mixer value changes rebuild the entire pad inspector.
- A retained render error prevents the main UI timer from servicing recording.
- Historical capability reports are dated snapshots; some missing capabilities
  described there now exist. New evidence must be tied to the current source.

## File ownership and order

PATCH owns model/instrument state, external DSP, engine live/offline/mixing,
MIDI performance, instrument and device panels, and plugin latency integration.
SPLICE owns window sampling and track/sample recording workflows. PRISM owns
the main-window render-error and mixer-change paths and targeted inspector UI.
FOREMAN integrates mixer selection and shared-file connections. Later mixer,
sequence and stability changes must claim files before editing. SENTINEL reviews
the final combined changes independently.

Preserve songs, libraries, recordings, exports, credentials and existing work.
No system audio changes, microphone activation, broad process termination,
automatic GitHub push/merge, release or deployment. Tests use synthetic fixtures
and the repository's memory-bounded headless runner. Heavy suites run one at a
time. Reports distinguish reproductions from suspected causes.

## Required acceptance

1. Two native/VST instances can keep distinct sounds; edit B leaves A unchanged.
2. Track selection targets the corresponding existing sound. Instance creation is
   explicit; output routing alone does not claim to create a sound.
3. Save/load, undo, note recording and offline export retain instrument identity.
4. Plugin failure, stale async completion and project replacement remain isolated.
5. Sampling prints the selected sound or reports unsupported input before mutation.
6. Recording completion/recovery remains serviced after audio errors; UI controls
   do not recreate unrelated widgets during simple adjustments.
7. Focused regressions, independent review, lint and integration tests pass.
8. Actual plugin/device acceptance, long performance soaks, cross-platform builds
   and packaged installation checks remain separate production-release gates.

## Using independent sounds in this development build

1. Select the desired mixer output and open Instruments. An empty channel shows
   **Empty track · create a sound** and disables editing of the previous sound.
2. Press **+ SOUND**, then choose a factory sound or use **VST…** to load a plugin
   into that instance. Existing recorded notes retain their original sound IDs.
3. Use the instrument selector or a mixer channel with an existing instrument to
   switch which sound you play and edit. The plugin parameter panel names its
   target instance. Its controls are the host's parameter editor; native vendor
   graphical plugin windows remain unsupported.
4. Record/edit notes and save the project. Projects containing independent VST
   states use format 7 so older readers reject them instead of dropping sounds.
   Projects without those states retain the existing format-6 interchange.
5. Native sound printing uses the selected patch. VST-to-pad printing remains
   unsupported here and explains the export-to-WAV alternative before editing.

## Repository reconciliation gate

This working copy started at `ed885c4` on `feature/daw-capability-foundations`.
The inspected remote main is `8b51e51`. The non-shallow local object graph has
**no merge base** between them; direct tree comparison spans 470 files. This is
not a routine fast-forward. Remote main contains further recording isolation,
runtime, and mixer smoothing changes absent here. Preserve both sources and
reconcile them deliberately before making an installer or publishing a release.
The `wrongrepo` remote and old branches were identified but not deleted.

Local detailed research and role evidence are intentionally outside public source
under `automation/production_swarm/reports/`. No production claim follows merely
from running this plan or launching its dashboard.
