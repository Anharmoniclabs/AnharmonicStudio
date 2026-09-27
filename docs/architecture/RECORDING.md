# Recording Boundary

## Audit state

PR #3, “Separate vocal, audio-input and MIDI capture paths,” was compared with
current `main` at `8ff5651d90bc737edc4bbbbf28f0ef7deffd11f1`. Its recording intent
is already present on `main`: explicit vocal input channels, dry generic Song
audio, and event-only MIDI/instrument/sample-pad paths. The PR head also contains
large unrelated Prism, transcription, native, and dependency changes, so it was
not merged or cherry-picked. Its remaining useful work is represented here as a
small compatibility boundary instead.

## Required session types

```text
AudioTrack   -> AudioCaptureSession
VocalTrack   -> VocalCaptureSession -> AudioCaptureSession
MidiTrack    -> MidiCaptureSession
SamplerTrack -> SamplerCaptureSession
```

Audio capture owns physical input, selected channels, stream lifecycle, WAV spool,
monitoring, retry, destination, and temporary-file retention. Vocal capture adds
pitch/key/take metadata without changing generic audio-track behavior. MIDI capture
owns events only. Sampler capture owns pad identity and gate/release performance
events; neither event pipeline opens the microphone recorder.

## Current controlling path and call graph

The production participants are:

- Vocal: `ui/vocal_layout.py` and `ui/vocal_recording.py` choose the device,
	channels, monitoring, and count-in; `vocal.py:VocalRecorder` owns the physical
	input stream, temporary WAV, decode, and recovery path; `ui/vocal_takes.py`
	imports the decoded take into the library; `ui/vocals.py` places it on the
	project and `model.py` persists the resulting clip/take metadata.
- Generic audio: `ui/arrangement_layout.py`, `ui/track_recording.py`, and
	`ui/window_transport.py` arm a Song row; `TrackCapture` selects the audio
	session, `VocalRecorder` captures a dry WAV, and `TrackCapture.save_take()`
	imports it through `library.py`, creates an audio `Clip`, and places it on the
	armed row. No vocal correction callback is installed for this path.
- MIDI/instrument: `ui/window_transport.py`, `ui/typing_keyboard.py`, and
	`ui/devices.py` submit events through `midi_devices.py`/`midi_performance.py`.
	`MidiPerformance` records `MidiTake` notes and controls, while
	`TrackCapture.save_take()` creates a pattern clip for Song recording.
- Sampler: `ui/sample_workflow.py`, `ui/window_sampling.py`, `ui/devices.py`,
	and `midi_performance.py` turn pad performance into event ownership; the
	pattern/clip save path is shared with MIDI but never opens the microphone.
- Loop/punch/take comping: `recording_workflows.py` decorates the compatibility
	coordinator and `take_comping.py` edits normal rows/clips after capture. These
	are preserved and remain outside the first boundary adapter.

`ui/track_recording.py:TrackCapture` was the shared coordinator and still owns
compatibility flags (`active`, `pending`, `unsaved`), but it now selects an
explicit session in `mpclab/recording.py`. `VocalPanel` now uses the
`VocalCaptureSession` adapter while retaining its existing controls and meter
properties. The remaining direct recorder references are diagnostics and storage
configuration, not start/stop lifecycle ownership.

## State machine contract

`mpclab/recording.py` now exposes `CaptureState`, `CaptureSession`,
`AudioCaptureSession`, `VocalCaptureSession`, `MidiCaptureSession`, and
`SamplerCaptureSession`. The shared lifecycle exposes `IDLE`, `ARMED`, `STARTING`,
`RECORDING`, `STOPPING`, `PROCESSING`, `COMPLETED`, `FAILED`, and `RECOVERABLE`.
`TrackCapture.state` observes the selected session while its old flags remain as a
compatibility surface. Audio decode failure enters `RECOVERABLE` and retains the
temporary WAV; save/import failure also re-enters recovery so retry does not lose
the destination or captured material.

## Tests and remaining adapter work

- `tests/test_recording_contracts.py` proves event sessions have no microphone
	recorder, audio channel selection reaches the recorder, and recovery/cancel
	transitions are valid.
- `tests/test_track_recording.py`, `tests/test_instrument_recording_route.py`,
	`tests/test_vocal.py`, and `tests/test_vocal_workspace.py` preserve existing
	audio, vocal, MIDI, sampler, loop/punch, take, and comping behavior.
- Remaining work is to expose one device manager to both audio paths and replace
	the remaining compatibility lifecycle flags after the full recording suite runs
	in Python 3.12.

## September 27 performance and transport contract

`ui/performance_capture.py` coordinates GUI pad and typing-keyboard capture with
`MidiPerformance` hardware capture. The audio clock and a monotonic count-in
deadline define the first recordable event. Count-in events are excluded; a key
or pad arriving after the deadline is captured even before the UI timer updates.
Input ownership prevents a single pad strike becoming both a step and a pitched
note. Key release returns to the instrument that received the corresponding press.

Workspace navigation changes editors only. Song playback survives switching to
Notes, Beats, Instruments or Sampler. Space/Stop retain transport ownership.
Song Record uses an armed destination; with none armed it creates a notes row.
Joining an already playing song does not seek. Ending Record commits the take
and leaves playback running. Recording may extend the arrangement's current end;
an explicitly enabled loop still wraps.

Instruments are inserted into a pattern as distinct Native or Prism instances.
`Pattern.instrument_ids` and `selected_instrument` persist membership and the live
selection. Layer current notes copies notes into the new owner; live MIDI follows
only the selection. Pattern duplication remaps notes to cloned instances.

Undo/redo restores changed project state incrementally, keeps unchanged audio
caches and plugin hosts, and updates parameters in place when supported. A failed
restore does not consume its history entry. Recording guards prevent history
restoration from replacing a take in progress.

For a controller with shared key/pad channels, configure pads to channel 10 and
keys to another channel before selecting Keys + drum channel. MIDI Learn maps
actual pad notes to Studio slots. Device hardware edits may be temporary until
saved on the controller; do not assume Studio can distinguish identical channel
and note messages from keys and pads.

Regression coverage includes `test_keyboard_performance.py`,
`test_recording_input_ownership.py`, `test_track_recording.py`,
`test_pattern_instrument_rack.py`, `test_prism_instances_ui.py`, and
`test_undo_runtime.py`. Physical latency remains a separate measurement.
