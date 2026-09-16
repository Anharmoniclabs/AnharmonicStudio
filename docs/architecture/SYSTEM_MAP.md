# Anharmonic Studio System Map

Status: audit snapshot for `main` at `8ff5651d90bc737edc4bbbbf28f0ef7deffd11f1`.

This is an ownership map, not a move list. During this pass production files are
not moved, renamed, deleted, or substantially rewritten. The target destinations
are migration boundaries to be introduced behind compatibility imports only after
the owning subsystem has tests green before and after the change.

## Product boundaries

| Owner | Current production surface | Owns | Target destination | State |
| --- | --- | --- | --- | --- |
| Application shell | `mpclab/__main__.py`, `application_features.py`, `production_runtime.py`, `runtime_paths.py`, `ui/main_window.py`, `ui/window_layout.py`, `ui/*_layout.py` | startup, application services, workspace composition, runtime paths | `mpclab/app/`, `mpclab/ui/shell/` | active |
| Domain and project model | `model.py`, `music.py`, `instrument_state.py`, `channel_registry.py`, `realtime_contract.py`, `project_migrations.py` | project objects, notes, instruments, tracks, validation and migrations | `mpclab/domain/` | active, overloaded |
| Persistence and recovery | `model.py`, `project_schema.py`, `project_io.py`, `project_package.py`, `project_media.py`, `project_migrations.py`, `session_history.py`, legacy state validators | JSON schema, migrations, atomic writes, sidecars, autosave and recovery | `mpclab/persistence/` | active authoritative boundary; compatibility installers retained |
| Audio engine and DSP | `engine.py`, `engine_scheduling.py`, `engine_mixing.py`, `engine_offline.py`, `audio_kernel.py`, `audio_analysis.py`, `audio_normalization.py`, `audio_storage.py`, `fx.py`, `loudness.py`, `native_*.py`, `native/` | transport, voices, render graph, native kernels, analysis and export | `mpclab/audio/` | active |
| Audio devices | `audio_setup.py`, `linux_audio.py`, `native_input.py`, `native_output.py`, `device_profiles.py`, `ui/audio_setup.py` | enumeration, stream lifecycle, channels, monitoring and recovery | `mpclab/audio/devices/` | active, duplicated inventory |
| Recording | `vocal.py`, `recording_timing.py`, `recording_workflows.py`, `ui/track_recording.py`, `ui/vocal_recording.py`, native input queue | microphone spool, vocal processing, Song note/pad capture, loop/punch/takes | `mpclab/recording/` | active, generic coordinator boundary |
| MIDI | `midi_io.py`, `midi_devices.py`, `midi_output.py`, `midi_performance.py`, `midi_playback.py`, `midi_smf.py`, `midi_file_state.py`, `ui/midi_files.py`, `ui/devices.py` | event normalization, routing, capture, output, files and expression | `mpclab/midi/` | active |
| Instruments | `synth.py`, `orchestra.py`, `sample_voice.py`, `sample_performance.py`, `prism*.py`, `plugins/prism/` | sampler, synth, Prism and performance state | `mpclab/instruments/` | active, Prism split pending |
| Sequencing and editing | `workflow.py`, `automation.py`, `workflow_commands.py`, `workflow_organization.py`, `take_comping.py`, `ui/sequencer.py`, `ui/piano_roll.py`, `ui/playlist*.py`, `ui/waveform.py` | patterns, notes, clips, arrangement, automation and editor state | `mpclab/sequencing/`, `mpclab/editing/` | active |
| Mixer and routing | `workflow_routing.py`, `workflow_mixing.py`, `plugin_latency.py`, `plugin_chain_runtime.py`, `ui/mixer.py`, `ui/fxrack.py`, `routing_ui.py` | buses, sends, PDC, inserts, meters and mixer controls | `mpclab/mixer/` | active, sidecar routing |
| Plugins | `plugin_registry.py`, `plugin_host.py`, `plugin_chain_host.py`, `plugin_chain_runtime.py`, `plugin_chain_ui.py` | discovery, isolated hosting, state, chains and crash handling | `mpclab/plugins/` | active |
| Scoring and transcription | `transcription.py`, `transcription_project.py`, `scoring.py`, `ui/transcription.py`, `ui/scoring.py` | analysis results, notation and MIDI/score insertion | `mpclab/scoring/` | active |
| Browser studio | `website/app/*.js`, `website/app/*.html`, `tests/web/`, `scripts/audit_web_*.py` | browser client of the project contract and Web Audio | `website/app/` client boundary | active, separate implementation |
| Delivery and packaging | `scripts/build_*.py`, `packaging/`, `delivery/`, `.github/workflows/` | native builds, frozen bundles, release evidence and installer delivery | `packaging/`, `delivery/` | active |

## Current production inventory

The complete current-source inventory is the union of these owned surfaces. Each
path is production, test, or delivery code unless marked experimental below.

### Python modules under `mpclab`

`__init__.py`, `__main__.py`, `agent_harness.py`, `application_features.py`,
`audio_analysis.py`, `audio_kernel.py`, `audio_normalization.py`, `audio_setup.py`,
`audio_storage.py`, `automation_clipboard.py`, `automation_mode_state.py`,
`automation_modes.py`, `channel_registry.py`, `command_catalog.py`, `crates.py`,
`dawproject.py`, `dawproject_io.py`, `dawproject_ui.py`, `detect.py`,
`device_profiles.py`, `dsp.py`, `engine.py`, `engine_constants.py`,
`engine_mixing.py`, `engine_offline.py`, `engine_scheduling.py`, `event_source.py`,
`export.py`, `export_worker.py`, `external_dsp.py`, `factory.py`, `fx.py`,
`install_bundle.py`, `instrument_state.py`, `library.py`, `library_comp.py`,
`library_journal.py`, `library_types.py`, `linux_audio.py`, `loudness.py`,
`mastering_analysis.py`, `mastering_runtime.py`, `media_cache.py`, `midi_devices.py`,
`midi_file_state.py`, `midi_io.py`, `midi_output.py`, `midi_performance.py`,
`midi_playback.py`, `midi_smf.py`, `model.py`, `music.py`, `native_core.py`,
`native_dsp.py`, `native_engine.py`, `native_input.py`, `native_output.py`,
`note_generator.py`, `orchestra.py`, `pitch_detection.py`, `plugin_chain_host.py`,
`plugin_chain_runtime.py`, `plugin_chain_ui.py`, `plugin_host.py`, `plugin_latency.py`,
`plugin_registry.py`, `premium_workflows.py`, `prism.py`, `prism_camera.py`,
`prism_motion.py`, `pro_daw_state.py`, `production_runtime.py`, `project_io.py`,
`project_media.py`, `project_migrations.py`, `project_package.py`,
`recording_timing.py`, `recording_workflows.py`, `realtime_contract.py`,
`runtime_paths.py`, `sample_performance.py`, `sample_voice.py`, `scoring.py`,
`synth.py`, `take_comping.py`, `time_stretch.py`, `transcription.py`,
`transcription_project.py`, `vocal.py`, `workflow.py`, `workflow_commands.py`,
`workflow_mixing.py`, `workflow_organization.py`, `workflow_plugin_pdc.py`,
`workflow_routing.py`, and `workflow_state.py`.

`mpclab/ui/` is the desktop editor surface. It contains shell/workspace layout
builders, controls, browser/library views, sampler and waveform editors, beats,
piano roll, Playlist, mixer, automation, devices/plugins, vocal/tuning/scoring,
Prism camera controls, dialogs, and session-history helpers. It is active UI code;
it is not an authoritative storage layer.

### Other production surfaces

- `mpclab/native/`: active portable C++ engine and the separate legacy/native API
  surfaces; ownership is audio engine, not UI.
- `plugins/prism/`: first-party Prism plugin source, tests, generated bank inputs,
  and product documentation; target `instruments/prism/` conceptually.
- `website/app/`: browser project model, audio engine, transport, media storage,
  workspaces, compatibility disclosure, mobile support, and client UI.
- `delivery/`: private installer service, Cloudflare worker, templates, and tests.
- `scripts/`: build, release, audit, browser, benchmark, preview, and test runners.
- `tests/`: regression, unit, integration, packaging, native, Qt, and browser tests.

## Dependency observations

1. `engine_scheduling.py`, `engine_mixing.py`, `engine_offline.py`, and
   `sample_voice.py` are the closest existing engine boundary and already accept
   an explicit coordinator.
2. `ui/track_recording.py` currently owns a `VocalRecorder` even for Song audio,
   while `ui/window_transport.py` dispatches note/pad capture separately. This is
   the controlling coupling for the recording migration.
3. `workflow_state.py`, `pro_daw_state.py`, `automation_mode_state.py`,
   `timeline_markers.py`, and `midi_file_state.py` wrap `Project.to_dict` or
   `Project.from_dict` at runtime. Import order can therefore control persistence.
4. `vocal.py` owns input enumeration and `ui/devices.py` owns MIDI enumeration;
   audio-device enumeration is also reached from UI setup and recording paths.
5. Prism camera code emits/consumes gesture data through `prism_motion.py`, but
   the parameter/runtime boundary is not yet a standalone instrument service.

## Status classification

- **Active:** all surfaces listed above that are imported by application startup,
  export, browser tests, packaging, or production workflows.
- **Compatibility:** `mpclab` flat-module imports, legacy project migrations,
  `event_source.ScheduledNote` compatibility, and the legacy API wrappers retained
  by `engine.py`.
- **Experimental or opt-in:** optional separation, camera/MediaPipe, trusted
  third-party plugin checks, and `native/src/` legacy LV2/ALSA prototype.
- **Dead:** none removed or declared dead by this audit. Filename absence is not
  sufficient evidence for deletion.

## Migration map

| Tranche | First owner | Compatibility boundary | Required proof |
| --- | --- | --- | --- |
| CI repair | source checks | none | Ruff, compile, focused tests |
| Recording | `recording_workflows.py`, `vocal.py`, `ui/track_recording.py` | `RecordingService` facade | audio/MIDI/sampler isolation, recovery transitions |
| Persistence | `model.py` and sidecar installers | `ProjectSchema` facade | roundtrip, malformed input, migration, browser fixture |
| Devices/MIDI | `vocal.py`, `midi_devices.py`, `ui/devices.py` | device manager APIs | reconnect, stable IDs, channel routing |
| Routing/engine | workflow routing and engine modules | compiled plans | offline/realtime equivalence, no UI imports |
| UI/workspaces | `mpclab/ui/` | controllers/services | command and undo coverage |
| Cleanup | root/docs/legacy modules | documented replacements | import, packaging, history and test audit |
