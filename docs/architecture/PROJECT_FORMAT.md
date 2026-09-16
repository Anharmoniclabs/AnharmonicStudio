# Project Format Contract

## Current contract

The desktop model declares `PROJECT_FORMAT_VERSION = 6`; the browser project model
also declares format 6. This is the current contract for new work. Historical
format 0 through 5 documents migrate in `project_migrations.py`. The contract must
not derive its version from whether optional lists happen to be empty.

The native and browser clients share project semantics for names, tempo, swing,
pads, tracks, patterns, notes, current pattern identity, vocal settings, automation,
and preserved extension metadata. Audio bytes and browser media storage remain
outside native JSON and are referenced by stable IDs.

## Authoritative implementation

The desktop pipeline is now:

```text
Project -> serialize -> validate -> atomic write
JSON document -> validate -> migrate -> deserialize -> Project
```

`mpclab/project_schema.py` owns the optional extension registry and calls the
existing bounded validators for workflow, plugin chains, automation modes, timeline
markers, imported MIDI sources, and track folders. These fields are explicit
`Project` attributes with stable defaults. Track folders retain their historical
wire location under `workflow.track_folders`; all other extensions retain their
existing top-level names. Empty timeline/MIDI state is omitted from the wire format.

The former `install_*_state()` functions remain compatibility hooks but no longer
replace `Project.to_dict/from_dict`; startup order cannot change field preservation.
`Project.save()` serializes through this boundary, validates before writing, flushes
and fsyncs a temporary file, then atomically replaces the destination.

## Field ownership

The canonical desktop top-level fields are: `name`, `bpm`, `swing`, `master`,
`self_choke`, `pads`, `tracks`, `patterns`, `current_pattern`, `rows`, `slices`,
`synth`, `instruments`, `selected_instrument`, `arp`, `song_length_beats`,
`loop_start`, `loop_end`, `loop_enabled`, `accent_color`, `delay_fx`, `reverb_fx`,
`master_fx`, `vocal`, `vocal_record`, `vocal_comps`, `current_vocal_comp`,
`automation`, `plugins`, and the registered extension fields below. Their concrete
types/defaults are the `Project` and nested dataclass annotations in `model.py`;
`Project.from_dict()` applies operational validation before returning an object.

| Canonical field | Owner | Default | Desktop/browser behavior |
| --- | --- | --- | --- |
| core model fields | `Project` domain | dataclass defaults | processed by native; shared semantic fields normalized by browser |
| `workflow` | sequencing/recording/routing workflows | `{}` | preserved; active unsupported browser processing is rejected |
| `workflow.track_folders` | organization workflow | `[]` | desktop-only UI organization, preserved by browser |
| `pro_daw` | plugin chains | `{}` | desktop-only processing, preserved and rejected when active in browser |
| `automation_control` | automation | `{}` | desktop-only write modes, preserved |
| `timeline_markers` | arrangement editing | empty version 1 document | desktop-supported; browser preserves unknown extension data |
| `midi_files` | MIDI interchange | empty version 1 document | desktop import/export metadata, preserved where browser supports unknown fields |
| `plugins` | plugin registry/host | `{}` | desktop-only processing, preserved and disclosed/rejected in browser |

Unknown top-level fields remain ignored by the desktop model for historical
compatibility; new owned fields must be registered in `project_schema.py` rather
than added through import side effects.

## Persistence call map

```text
MainWindow/project actions
	-> Project.save(path)
		 -> Project.to_dict()
				-> core validation + project_schema.serialize_extensions()
		 -> JSON encode -> temp file -> flush/fsync -> os.replace

Project.load(path) / project_io.load_project_file(path)
	-> bounded UTF-8 JSON reader
	-> Project.from_dict(document)
		 -> project_migrations.migrate_project_document()
		 -> core model construction/validation
		 -> project_schema.deserialize_extensions()

SessionHistoryMixin._autosave_session
	-> Project.save(.session-autosave.json)
	-> atomic undo history sidecar (.session-autosave.json.history)
	-> recovery-versions backup before replacement

project_package.package_project / dawproject_io
	-> Project.save() or explicit interchange adapter
	-> media manifest/archive publication

Browser ProjectStore
	-> website/app/project-model.js normalize/validate
	-> browser JSON/portable media bundle
	-> shared semantic fixtures in tests/web/fixtures/
```

The former runtime hook locations remain field validators and compatibility
install functions only: `workflow_state.py`, `pro_daw_state.py`,
`automation_mode_state.py`, `timeline_markers.py`, `midi_file_state.py`, and
`workflow_organization.py`. They no longer assign `Project.to_dict` or
`Project.from_dict`. `application_features.install_application_runtime()` may
still call them for older integrations, but call order has no persistence effect.

Autosave, explicit user saves, recovery snapshots, and temporary recording WAVs
remain separate artifacts. Autosave recovery is archived or intentionally restored;
it never overwrites the last explicit project save.

## Browser behavior

Unsupported active native processing must be rejected before browser playback/export
with the saved settings preserved. Browser-only behavior must not be serialized as
if native processing occurred. Shared fixtures under `tests/web/fixtures/` and
`tests/test_web_project_interchange.py` are the compatibility proof surface.

Malformed documents are rejected at the model/schema boundary with strict types,
finite values, enum/range checks, unique IDs, resource budgets, and migration tests
before engine construction. Version migrations remain explicit in
`project_migrations.py`; v4 adds stable mixer IDs and v5/v6 preserve the current
instrument-era contract without deriving version from content.
