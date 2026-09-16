# Dependency Rules

These rules govern the audit-first migration. A target directory is not a license
to move an implementation until its tests and import boundary are established.

## Direction

```text
UI -> application services/controllers -> domain and subsystem APIs
   -> engine/device/persistence implementations
```

- Domain, persistence, DSP, MIDI routing, and recording core must not import Qt UI.
- Engine code must not construct widgets or own editor selection/focus state.
- Device managers own enumeration, stable IDs, reconnect, availability, and errors;
  widgets display and request state.
- Project serialization must work without starting the application or opening a
  device.
- Browser code is a client of the documented project contract, not a second native
  engine and not an authority for desktop-only processing.
- Plugins are discovered separately from hosted processing; third-party code stays
  outside the realtime callback and project failure must remain recoverable.

## Audit constraints

- No move, rename, delete, or substantial production rewrite during the audit pass.
- Every refactor tranche has a compatibility import or facade until callers migrate.
- Every recovered behavior keeps its existing test or gains an equivalent regression.
- A suspected dead module is removable only after import, registration, dynamic-load,
  packaging, test, documentation, and Git-history searches.
- Hardware and platform acceptance remain explicitly pending when no evidence exists.

## Forbidden coupling to remove gradually

- Runtime monkey-patching of `Project.to_dict/from_dict` as persistence registration;
  `project_schema.py` is the sole extension registry.
- Generic recording branches spread through UI and engine modules.
- Duplicate audio-device enumeration in vocal, Song, preferences, and recording UI.
- Direct Prism gesture manipulation of arbitrary widgets.
- Editor widgets as authoritative project storage.
