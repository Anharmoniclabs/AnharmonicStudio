# DAW capability implementation ledger

The owner supplied `Anharmonic_Full_Deliverables.md` and
`Anharmonic_DAW_Comparison.xlsx` on 2026-09-10. Their original files remain in
Downloads and are not modified. [daw-capabilities.json](daw-capabilities.json)
imports all 216 comparison rows and all 194 work packages, including acceptance
criteria, historical source limitations, manufacturer cells as supplied, and
SHA-256 provenance. The two inputs agree on every work-package ID, name, priority,
status, limitation and completion criterion.

This is a product-development backlog, not a list of completed features.
Manufacturer cells and absence judgments come from the supplied static audit of
`e07bd34`; this implementation work does not independently certify those claims.
`NV` remains unverified, not absent. Counts are not completion percentages.

[daw-progress.json](daw-progress.json) tracks current work without rewriting the
historical comparison. Work packages without an explicit entry are not started.
An item cannot be marked implemented merely because a class or button exists;
record working production wiring, tests and the remaining acceptance limits.

See [USAGE.md](USAGE.md) for the new controls and their explicit limitations.
The [execution report](EXECUTION_REPORT.md) records implemented scope, checks and release holds.

## Explicit owner policy

The spreadsheet's signed-installer request (`19-01`) is superseded by the owner's
explicit instruction to distribute **unsigned** packages with installation advice.
The original requirement is retained for traceability, with its replacement in
the progress file. This does not waive functionality, platform/device, source,
checksum or private-download checks. No new feature authorizes early Cloudflare
promotion or deployment of an incomplete candidate.

## Execution order

1. Foundation: dynamic tracks, independent instruments, I/O/channel layouts,
   project rates, tempo/meter maps, and aligned multitrack recording.
2. Professional music workflows: markers/editing, MIDI interchange/expression,
   plugin routing/automation, measured metering, and stem/format export.
3. Expanded instruments, effects, media management, composition and live tools.
4. Specialist notation, video/post, immersive formats, external integrations and
   collaboration, with explicit licensing/device/service decisions where needed.

This first feature branch starts with dynamic tracks and independent marker and
analysis workflows while preserving the tested release-repair branch. It does not
include the separate `refactor/native-production` worktree or claim all 194 work
packages are finished. Hardware, licensed format/SDK access, paid service setup and
content licensing must never be fabricated or purchased without owner approval.

## Reproduce the import

The importer uses only the Python standard library. It reads bounded XLSX XML
without launching Excel, evaluating formulas/macros, or following external links:

```sh
python scripts/import_daw_backlog.py /path/Anharmonic_Full_Deliverables.md \
  /path/Anharmonic_DAW_Comparison.xlsx --output planning/daw-capabilities.json --check
```

Omit `--check` only to regenerate the catalog after an intentional input revision.
Progress is stored separately and is never reset by catalog regeneration.
