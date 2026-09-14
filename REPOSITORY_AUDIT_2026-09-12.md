# Repository audit — 2026-09-12

## Scope and limits

This is an evidence-based review of the current checkout at commit `0dfa299` on
`feature/daw-capability-foundations`, including the uncommitted work present during the
audit. It covers desktop Python/PySide code, project persistence, realtime/offline audio,
browser-studio controls, the portable C++ core, the optional legacy LV2 prototype, tests,
CI, and existing planning/release documentation.

No finite static review can prove that every bug has been found. Hardware audio/MIDI,
installed third-party plugins, four-platform packages, and real browser behavior need
their own acceptance runs. Existing source changes were not modified by this audit.

## Executive verdict

The checkout is not release-ready. The main blocking issue is a partially introduced
project format 6: ordinary projects are still written as format 5, three tests fail, and
the browser rejects actual format-6 projects. Project loading also accepts invalid values
that can cause division by zero, invalid audio state, or misleading booleans later.

The UI is generally wired: a source scan found no obvious orphaned desktop push buttons,
and all 25 statically identified browser buttons with IDs are referenced by the browser
controller. The important control defects are behavioral rather than missing click
handlers: keyboard-triggered gate/loop pads are not released, transport presents Stop as
Pause, duplicated Help markup opens stale content, and browser navigation/menu state is
not exposed correctly to assistive technology.

The repository already has a much larger product-gap inventory. The 216-capability ledger
records 112 baseline-missing, 73 partial, 22 present, and 9 acceptance-pending items across
194 work packages (`planning/daw-capabilities.json`). Those are roadmap gaps, not all
newly discovered defects, and should not be mixed with the release blockers below.

## Validation results

| Check | Result | Evidence |
| --- | --- | --- |
| Full Python/Qt suite | **Fail**: 3 failed, 1,581 passed, 5 skipped; 611.78 s; 641.2 MiB peak RSS | All failures expect current project format 6 but receive 5 |
| Focused persistence/instrument suite | **Fail**: 2 failed, 61 passed | Same format-version regression |
| Ruff lint | Pass | `ruff check mpclab tests scripts` |
| Ruff formatting | **Fail**: 4 files | `mpclab/ui/piano_roll.py`, `mpclab/ui/synth.py`, `mpclab/ui/window_transport.py`, `tests/test_loop_punch_recording.py` |
| Dependency lock | Pass | `uv lock --check` |
| Website structural validator | **Fail** | Duplicate `help-dialog` ID |
| Portable C++ core | Pass | Clean temporary CMake build; `portable_core` 1/1 passed |
| Packaged-style offscreen self-check | Pass | Native DSP/output, export worker, FFmpeg, UI lifetime, existing production commands |
| JavaScript unit tests | Not run locally | Node is not installed in this environment; CI installs Node 24 |
| Chromium browser audit | Not run locally | Playwright is not installed in this environment |
| Hardware/plugin acceptance | Not run | Requires real devices and trusted installed plugins |

## Confirmed defects

### P1 — project format 6 is internally inconsistent and breaks interchange

- `mpclab/model.py:37` declares `PROJECT_FORMAT_VERSION = 6`, but `Project.to_dict()`
  writes 6 only when `project.instruments` is nonempty and otherwise hard-codes 5
  (`mpclab/model.py:657-665`).
- This directly fails `tests/test_project_persistence.py:12-23`,
  `tests/test_sample_instruments.py:80-92`, and `tests/test_workflow_state.py:54-60`.
- The browser still declares format 5 and rejects any version above 5
  (`website/app/project-model.js:4,158-168`). A native project that actually uses an
  independent instrument therefore cannot open in the browser studio.
- Persistence sidecars still document format 5 as the core contract
  (`mpclab/workflow_state.py:1-5`, `mpclab/pro_daw_state.py:1-4`).

Impact: schema meaning depends on whether a list happens to be empty; release tests fail;
desktop/browser compatibility is split; documentation and tests disagree with runtime.

Required fix: make one explicit decision. Either complete format 6 everywhere (migration,
desktop writer, browser reader/preservation/rejection rules, fixtures, docs, workers), or
keep the new fields as a rigorously validated format-5 extension and restore the version
constant/tests accordingly. Do not encode schema version from current content.

### P1 — desktop project loading accepts unsafe numeric, enum, and boolean state

`Project.from_dict()` verifies that a few top-level numbers are finite but does not enforce
their operational ranges (`mpclab/model.py:726-733`). Several nested dataclasses have no
validation at all: `Pad` validates only `root_note` and `mono` (`model.py:63-88`), `Clip`
has none (`model.py:446-460`), and `Track` validates only its ID (`model.py:433-443`).
Patterns convert `bars`, `div`, steps, and velocities without range checks
(`model.py:836-853`). Many booleans use Python truthiness rather than strict types.

The audit reproduced successful loads of all of these malformed documents:

- BPM `0` and `-120`
- master gain `-5`
- pad gain `NaN`, pan `9`, and mode `"corrupt"`
- pattern division `0` and bars `-2`
- track gain `NaN`
- clip length `-4` and kind `"x"`
- `format_version: 5.9`
- string `"false"` becoming true for `self_choke`, or remaining a truthy string for pad
  reverse and track mute

This is not merely cosmetic. BPM zero reaches `60.0 / bpm` in live and offline audio
(`mpclab/engine.py:798-805`, `mpclab/engine_offline.py:49-61`), and pattern division zero
reaches `1.0 / pat.div` (`mpclab/engine_scheduling.py:94`,
`mpclab/ui/playlist_rendering.py:241-247`). Nonfinite gains can contaminate DSP state.

Required fix: validate the complete project at one boundary with strict types, finite
numbers, enums, cross-field constraints, unique IDs, and operational ranges. Reuse the
same schema contract in browser and desktop tests. Add table-driven malformed-file tests
and fuzz/property tests.

### P1 — independent-instrument and MIDI-channel work is only partially reachable

- The model can add an instrument (`mpclab/model.py:560-567`), but the only call to
  `add_instrument` outside the model is in a test. Production UI can select instruments
  already present in a file (`mpclab/ui/piano_roll.py:433-483`) but provides no add,
  duplicate, rename, delete, or MIDI-channel controls.
- `Instrument.midi_channel` is persisted and validated (`mpclab/model.py:156-186`) but no
  production routing code reads it. `MidiRouter` discards channel information when it
  invokes note callbacks (`mpclab/midi_devices.py:329-355`), and `DevicesController`
  routes notes to the currently selected destination (`mpclab/ui/devices.py:55-80`).
- As a result, separate channel targeting is not implemented and live recorded channel
  provenance cannot reliably reach the new `Note.channel` field.
- Browser format support is absent, as described above.

Impact: projects can contain a feature users cannot fully manage, and the advertised MIDI
channel field has no routing effect. This is incomplete feature work, not release-ready
multi-instrument support.

Required fix: add lifecycle UI and commands, preserve channel in router callbacks, define
selected-vs-channel precedence, route channel-matched instruments, and test live play,
recording, loop/punch capture, save/reopen, realtime playback, offline render, undo, and
browser interchange/rejection.

### P1 — keyboard activation can leave browser gate/loop pads sounding

Pointer pad activation installs pointer-up/cancel release handlers
(`website/app/studio.js:405-413`). Global number-key activation similarly tracks the held
pad and releases it on key-up (`studio.js:990-1002`). Focused pad activation with Enter
only calls `triggerPad` and has no key-up release path (`studio.js:414`).

Gate and loop modes require release, while the audio engine gives an unspecified loop a
60-second duration (`website/app/audio-engine.js:390-395`). Keyboard users can therefore
start a gate/loop voice that continues until Stop, retrigger, or timeout.

Required fix: give focused-pad keyboard activation the same held/release lifecycle as
pointer and number-key activation, including blur, cancellation, repeat suppression, and
an automated browser assertion for one-shot, gate, and loop modes.

### P2 — duplicated Help dialog creates invalid DOM and opens stale copy

`website/app/studio.html:36` and `:37` contain two complete elements with
`id="help-dialog"`. The first omits the newer mixer-group limitation. Both Help and
Capabilities use `querySelector`, which returns the first copy (`website/app/studio.js:988`).

Impact: HTML ID uniqueness is broken, assistive-technology relationships are ambiguous,
and users see stale help. `scripts/check_website.py` fails on this defect.

Required fix: retain one canonical dialog and keep the structural validator as a release
gate.

### P2 — browser Play/Pause control actually performs Play/Stop

The play button is labelled "Play or pause" and changes to a pause glyph. When clicked
while playing, `togglePlayback()` calls `stopPlayback()`, which resets beat and step to
zero (`website/app/studio.js:160-181`). Resume is impossible.

Impact: the label/icon promise pause-resume semantics, but the control rewinds. Users can
lose their audition position.

Required fix: implement a true paused state and resume position, or label/render the
control as Stop and avoid duplicating the adjacent Stop button's semantics.

### P2 — project-name edits can escape the unsaved-change guard

The browser records the project name only on `change` (`website/app/studio.js:942`). A user
can type into the still-focused field and close/reload before blur; `state.dirty` may still
be false, so `beforeunload` will not warn (`studio.js:1003`). Export reads the live field,
which makes persistence behavior depend on the path used.

Required fix: transact or independently mark dirty on `input`, then normalize/commit on
blur or Enter. Add a browser test that edits the name without blurring and attempts to
replace/unload the session.

### P2 — browser navigation and popup menus are incomplete for keyboard/AT users

- Workspace buttons visually behave as tabs but lack tab roles, `aria-selected`, and
  tab/panel relationships (`website/app/studio.html:29`).
- Browser/pad/focus toggles update CSS classes but not `aria-expanded` or `aria-pressed`
  (`website/app/studio.js:903-917`).
- Popup menus declare `menu`/`menuitem`, but opening does not move focus, arrow-key/Home/
  End navigation is absent, and the anchor's expanded state is not exposed
  (`studio.js:70-90`).

Impact: controls work with a pointer but their current state and navigation model are not
reliably operable or announced through keyboard and assistive technology.

Required fix: implement the ARIA Authoring Practices behavior for tabs, disclosure
buttons, and menus; include keyboard-only Playwright tests and an automated accessibility
scan.

### P2 — portable browser import/export has avoidable peak-memory amplification

Import permits a 360 MiB file, reads it all as text, parses a second object graph, decodes
base64 to a string, copies it into a typed array and Blob, then decodes PCM
(`website/app/studio.js:253-279`). Export similarly materializes every base64 string plus
the final JSON document (`studio.js:228-236`). The decoded-audio budget is useful but is
enforced after these transient allocations.

Impact: a nominally allowed bundle can consume several times its file size and freeze or
terminate a tab, especially on mobile.

Required fix: reduce the admitted bundle size or move to a streamed/archive container;
enforce aggregate encoded and decoded budgets before copies; release intermediate data
incrementally; add peak-memory acceptance on the supported browsers.

### P2 — newly added MIDI-file and loudness/normalization code is not in production wiring

The current working tree contains new `midi_smf.py`, `midi_file_state.py`,
`ui/midi_files.py`, `loudness.py`, and `audio_normalization.py`. However:

- `attach_midi_files()` exists (`mpclab/ui/midi_files.py:199`) but is never called.
- `install_midi_file_state()` exists (`mpclab/midi_file_state.py:343`) but is absent from
  `install_application_runtime()`.
- `measure_loudness()` and `normalize_audio_file()` have no application command or UI.
- The self-check production command list contains neither MIDI-file nor loudness/export
  commands.
- User documentation and Audio Analysis UI still explicitly say LUFS/LRA/true peak are not
  measured (`planning/USAGE.md:48-56`, `mpclab/ui/audio_analysis.py:93,202`).
- Loudness has focused tests, but MIDI SMF/state/UI and normalization have no dedicated
  test module in the checkout.

Impact: code existence can be mistaken for shipped capability, while users cannot reach
it and persistence/export workers do not install its state wrapper.

Required fix: keep it explicitly experimental until the full production path, commands,
workers, docs, cancellation/error UX, and regression tests are complete.

### P3 — Standard MIDI parser accepts malformed fixed-size meta events

Tempo and time-signature meta events are rejected only when shorter than 3 and 4 bytes
respectively (`mpclab/midi_smf.py:131-134`). SMF defines exact lengths, so oversized
payloads are accepted and trailing bytes ignored.

Required fix: require `size == 3` for tempo and `size == 4` for time signature; add
short/long malformed fixtures and ensure import leaves the current session untouched.

### P3 — optional legacy LV2 prototype has native safety defects

This target is explicitly opt-in and not used by production (`native/CMakeLists.txt:36-39`),
so these findings do not apply to the passing portable core. If retained:

- C ABI mutators dereference null handles inside `guard`; a null dereference is not a C++
  exception and can terminate the caller (`native/src/c_api.cpp:54-59,72-74,84-100,
  103-120`). Query functions already handle null, so the contract is inconsistent.
- `set_plugin_control` writes a shared `float` while the audio callback can run the same
  plugin, without a lock, atomic handoff, or stopped-audio precondition
  (`native/src/engine.cpp:130-133,135-166,276,403-405`). That is a C++ data race.
- `Lv2Plugin` acquires raw Lilv resources during a constructor that can throw before its
  destructor runs (`engine.cpp:63-119`). `Engine` similarly initializes PortAudio before
  `open_midi()` can throw (`engine.cpp:179-187`). `anh_engine_create` allocates a raw
  `Handle` before constructing `Engine` (`native/src/c_api.cpp:34-46`). These exception
  paths leak resources.

Required fix: either remove the retired prototype or harden it with null checks, RAII
owners/scope guards, a lock-free control-message handoff (or stopped-only contract), and
ASan/UBSan/API misuse tests for the optional target.

## Button and interaction audit

### Desktop

- 149 `QPushButton` constructions were found under `mpclab/ui`; the broader signal scan
  found 318 button/signal connection references.
- No obvious actionable push button lacked a connection after accounting for buttons whose
  connection is made in another module and menu buttons that intentionally open a menu.
- Native Qt controls provide a better accessibility baseline than the custom browser UI,
  but real keyboard/focus/screen-reader testing is still absent from release evidence.
- Missing controls are feature-level: independent-instrument lifecycle/channel controls,
  MIDI file commands, and loudness-normalized export are not attached.

### Browser

- All 25 static button IDs are referenced in `website/app/studio.js`; dynamic and
  data-attribute controls are handled through direct or delegated listeners.
- Confirmed control bugs are the pad Enter release, false Pause semantics, stale Help
  dialog, incomplete dirty tracking, and missing ARIA state/menu keyboard behavior above.
- Add a control matrix test covering click, Enter, Space, key-up/cancel, disabled/busy
  state, error reporting, undo/redo, and state restoration for every visible control.

## Cross-cutting improvement areas

### 1. Replace persistence monkey-patching with an explicit schema

`workflow_state`, `pro_daw_state`, `automation_mode_state`, `timeline_markers`, plugin
state, recording state, and the new MIDI state wrap `Project.to_dict/from_dict` at runtime.
Comments already acknowledge installation-order constraints
(`mpclab/timeline_markers.py:267-300`). Import order can silently determine which fields
survive in the app, tests, and export worker.

Move extensions into declared project fields or one ordered extension registry with a
single serialize/validate/migrate pipeline. Test the same pipeline in normal startup,
self-check, background export, direct library use, and packaged builds.

### 2. Establish one desktop/browser interchange contract

Keep a canonical schema/version document and shared fixtures for every version. Require
both runtimes to either implement a field or reject an active unsupported feature before
play/export. Generate documentation/fixtures from the contract where practical.

### 3. Add aggregate resource limits at trust boundaries

Desktop `Project.load()` reads an unlimited file into memory (`mpclab/model.py:943-945`).
Per-list limits can multiply to enormous totals (for example 1,024 patterns each allowing
100,000 notes, plus 4,096 rows each allowing 100,000 clips). Add file-size, total-node,
total-note, total-clip, string-length, and nesting budgets before constructing the model.

### 4. Make the web source reviewable and add web linting

The main browser controller is 1,023 lines and frequently compresses multiple state
transitions onto one line; HTML and CSS are also densely packed. Current CI runs a custom
validator and tests but no ESLint, type checker, or formatter. Split transport, storage,
media, recording, views, and accessibility into modules; add a web formatter/linter and
make failures block deployment.

### 5. Strengthen quality gates beyond test count

The suite is broad, but there is no coverage threshold, static type check, schema fuzzing,
or accessibility gate. The offscreen self-check reported `project_roundtrip: true` while
three version-contract tests failed, so it should also assert the active format version
and every production feature command expected for the build.

### 6. Keep product claims synchronized with reachability and acceptance

Update `planning/daw-progress.json`, `planning/USAGE.md`, the self-check command list, and
release evidence only when a feature is wired, tested, and accepted. Existing
`RELEASE_AUDIT.md` correctly holds publication for four-platform, physical-device, source,
and browser checks; the present failures add another reason not to promote this checkout.

## Recommended repair order

1. Resolve the format-5/format-6 decision and restore desktop + browser interchange tests.
2. Add strict, aggregate project validation; cover every value that reaches timing, array
   sizing, routing, DSP, file access, or UI indexes.
3. Complete or feature-gate independent instruments and preserve MIDI channel routing.
4. Remove the duplicate dialog and fix browser keyboard release/transport semantics.
5. Wire or quarantine MIDI-file and loudness/normalization work; update docs only after
   production integration.
6. Fix the four formatting failures and rerun all source/browser/native gates.
7. Harden/remove the optional legacy LV2 prototype.
8. Run Chromium interaction/audio/accessibility tests, four-platform CI/package checks,
   real audio/MIDI interface acceptance, and trusted plugin tests before release.

## Release gate for the next candidate

A candidate should not be promoted until the working tree is intentionally committed and
clean, all Python and browser tests pass, formatting and HTML validation pass, current
portable native tests pass under sanitizers, desktop/browser project fixtures agree on the
active schema, unsupported features fail explicitly, and the existing platform/device/
package acceptance requirements in `RELEASE_AUDIT.md` are satisfied.
