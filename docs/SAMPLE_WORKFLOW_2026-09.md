# Play a sample in Notes; drop a sound into Beats

Implemented for the sample-workflow pull request, 2026-09-07.

## Use it

Select a sound in the Browser and press **Play in Notes**. A free slot in the
current pad bank becomes a sample instrument, Notes selects it, and the keyboard
and note editor play that sound. A second invocation uses another free slot;
existing melodies keep their original destination. The source recording is not
modified. The Sample editor offers the same action for its exact selected range.

The Notes **Sound** selector chooses either the original synth or a specific
sample slot. Its heading includes the mixer destination. **Root MIDI** declares
the source's recorded pitch: 60 means C4, 45 means A2. C4 is an initial assumption,
not detected pitch. Existing pad pitch provides fine tuning in **Edit sound**.
Changing pitch by repitching also changes duration. **Mono** retriggers one note
at a time for that sample channel; it does not provide glide or recorded legato.
New sample instruments start in gate mode. The existing pad inspector chooses
one-shot, gate, loop, envelope, reverse, crossfade and routing.

**Add to Beats** adds a sound without overwriting an occupied or musically used
slot. Drag a Browser sound or selected sample range onto a Beats lane **header**
to replace its sound while keeping steps, notes, gain and mixer routing. Drag to
the explicit **Drop a sound here** footer to add a new slot. Dropping on steps is
not a hidden instruction to insert or overwrite a beat.

Hover over **Beats**, **Notes** or **Arrange** for 350 ms during an internal sample
drag to reveal its editor. Hovering alone changes no music. Dropping directly on
Beats adds a sound; on Notes it creates an instrument; on Arrange it keeps the
existing append-audio behavior. Both Studio and the full tab layout are supported.
An occupied bank reports that another bank or an explicit replacement is needed.
Malformed, cancelled and failed-decode drops do not create partial edits.

A drop onto the Notes sound selector replaces that selected sample instrument.
A drop onto the note grid creates another instrument. Right-click a Beats lane
for **Open in Notes** or **Convert steps to notes (keep swing)**. Conversion moves
the events, preserving their velocities and swung offsets; it does not play two
copies of each hit. It is explicitly undoable. Notes are not silently converted
back to the coarser step grid.

Browser activation/preview remains audition-only. The sample actions are also
available in its context menu. The existing illustrated Crates and native Qt
workspace are retained.

## Musical and project guarantees

Notes persist an optional pad-slot destination; a missing destination retains the
original synth route. The engine uses that stored slot, never the UI selection,
for Pattern playback, Song playback and offline export. Separate sample channels
can reuse the same recording with independent tuning and routing. A held key's
release/recording target is captured at key-down, so changing the selected sound
cannot redirect its note-off. Chords, mono retriggers and backing/live ownership
have regression tests.

New saves use **project format 3**. Formats 0, 1 and 2 remain readable; older
application versions deliberately reject new saves rather than discarding sample
note destinations. Save a copy when experimenting with an older build. Existing
JSON history, undo/redo, sample references and audio-source protection are reused.
Export reports missing sample-instrument slots rather than silently dropping them.

## Scope and limits

This is a working vertical slice of the larger DAW plan, not every item completed.
It reuses **64 stable pad slots** as sample-channel identities; pad and instrument
views of the same slot intentionally share settings. The original synth remains
one shared patch. Independent synth instances, unlimited/reorderable channel IDs,
plugins, MIDI devices, glide, automatic root detection, pitch-preserving stretch,
velocity zoning, disk streaming and collect/relink remain separate work.

Internal samples are decoded/prepared before assignment and outside the audio
callback, but that foreground operation can still stall on a large uncached file.
Operating-system files should be imported through the Browser before using the
new Beats/Notes targets. General asynchronous file-to-channel transactions are
not claimed here. This does not make the Python callback allocation-free or
certify a physical audio interface, latency, long-session stability or every
Linux distribution.

## Reproducible validation

`tests/test_sample_instruments.py` adds 49 checks covering schema/legacy loading,
independent sample/synth routing, actual rendered pitch, gate/one-shot/loop behavior,
recording ownership, save/reopen, undo/redo, channel-scoped editing, safe conversion,
hover/drop destinations, failed imports and visible bass octaves. Existing tests
and their assertions are retained.

Run the repository's locked CI environment and tests:

```sh
uv sync --locked --group dev
QT_QPA_PLATFORM=offscreen uv run --no-sync python scripts/build_native.py
QT_QPA_PLATFORM=offscreen uv run --no-sync pytest -q
MPC_NATIVE_DSP=0 QT_QPA_PLATFORM=offscreen uv run --no-sync pytest -q \
  tests/test_synth.py tests/test_engine_synth.py \
  tests/test_music_workstation.py tests/test_sample_instruments.py
uv run --no-sync python scripts/render_sample_workflow.py --output /tmp/sample-previews
```

The renderer uses temporary synthetic media/settings with audio-device startup
disabled. It captures actual Notes/Beats widgets at 1440 and 1000 pixels, dark and
light. It does not modify or display the user's running DAW.

The original PR quality jobs failed before tests could collect because Ubuntu
lacked `libEGL.so.1`. The workflow now installs Qt/audio runtime libraries and
checks offscreen imports explicitly. CI-fix-only run **34151082956** passed all
quality steps. Feature validation belongs to the checks on the feature commit;
that earlier green run must not be mistaken for testing these later changes.

## Best next additions, in order

1. **808 glide and explicit retrigger/legato controls.** Test overlapping notes,
   tempo changes and live/export equivalence before adding a glide knob.
2. **Root-note detection with confidence and manual correction.** Analyze prepared
   source audio; never label a noisy/percussive sample as confidently pitched.
3. **Independent synth instances and a general channel registry.** Migrate stable
   destinations; do not bind existing notes to selected sounds or list positions.
4. **Replace audition, favorites and recent sounds.** Compare snares in the playing
   beat, then Apply or Cancel with exact restoration and one undo transaction.
5. **Collect/relink plus async media preparation.** Make projects portable and keep
   browsing a long source from stalling the UI. MIDI capture can then target the
   same explicit sound destinations without creating another routing system.
