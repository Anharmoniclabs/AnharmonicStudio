# Refinement cycle — track recording monitor

Verified defect: Song-track input gain and monitor controls were tied to the global Vocal recording settings, while generic Song audio capture passed no monitoring callback. The UI could therefore indicate monitoring without creating a Song cue path.

Implemented on this refinement branch:

- persist per-row Song count-in, input gain, and dry-monitor state under validated `workflow.recording.track_inputs`;
- keep Vocal monitoring state isolated from Song-track monitoring;
- route enabled Song dry monitoring through the existing non-blocking engine cue path;
- disable the unsupported Song pitch-corrected cue control instead of presenting it as active;
- add focused regression coverage for row isolation, persistence, dry cue routing, Vocal/Song separation, and malformed monitor metadata.

Validation status: source diff inspected and focused validator checks passed in the refinement environment. Full repository CI and rendered GUI QA were not available in this run and are not claimed.
