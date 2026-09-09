# Acceptance rubric

Use the criteria relevant to the selected task. These are development targets, not current certifications.

## Musician workflow

Use short, original synthetic fixtures: a drum loop with ghost hits, an off-grid groove, a held chord crossing a loop, and a vocal-like mono signal with silence. Keep originals and variations separate. Never use or modify the user's songs or sample packs for unattended tests.

| Task | What a completed improvement must preserve |
|---|---|
| Find → audition → chop → pad | Stable sample identity, clear preview/selection, exact range, snap bypass, keyboard operation, original media |
| Perform → edit → vary a groove | Velocity, gate, microtiming, explicit quantize amount if introduced, undoable variation, repeatable playback |
| Pattern → arrangement → transition | Coherent selection and zoom, clip boundaries, note releases/chasing, loop/seek behavior, audible transitions |
| Record → comp → mix | Dry source/takes, timing, explicit monitoring and routing, reversible edits, no accidental project replacement |
| Shape sound → automate → export | Useful units and ranges, smooth changes, visible automation/manual state, tails and latency, saved state, live/offline agreement |
| Save → reopen → recover → deliver | Format compatibility, asset identity, missing-media behavior, cancellation, atomic completed files |

Creative tools should expose what they change and let musicians keep, revise, or undo the result. Any randomness needs controllable amount, protected selection, and reproducibility when appropriate. Never silently quantize, normalize, regenerate, or replace existing musical work.

## Interface

Inspect actual offscreen Qt renders, not a generated concept. Check normal and narrow widths plus light/dark variants. Keep a consistent type scale, readable labels, aligned controls, visible keyboard focus, clear active/disabled/armed states, and track color semantics that also have non-color cues. Favor useful edit space and discoverability over decorative surface area. Numeric controls need units, sensible ranges, fine adjustment, and predictable keyboard behavior.

Use `scripts/render_studio_preview.py` for synthetic preview coverage; it disables engine startup, isolates settings, and uses temporary media. Before using or changing any preview script, confirm those properties still hold. Do not run the real-library browser script during unattended work.

## Audio

State the sample rate, block size, duration, workload, host, and measurement method. One block's deadline is frames / sample rate. At the current 48 kHz production rate, 256 frames is 5.33 ms and 512 is 10.67 ms; neither is physical round-trip latency.

For relevant changes, test finite output, silence/tails, extremes, interpolation, denormals, automation ramps, note-off, seek and loop discontinuities, sustained voices, routing, bypass, and native/reference equivalence. Check other rates only where actually supported. Measure callback p99, worst time, and deadline misses; preserve the current working 512-frame dense-session profile. Hardware xruns and cable loopback measurements must be labeled separately from software measurements. Do not touch live devices from the recurring worker.

## Verification commands

The controller independently runs the repository's lockfile check, native build, Ruff lint/format checks, syntax compilation, full offscreen pytest suite, Python DSP fallback tests, callback benchmark, production benchmark, and short callback soak. The initial commands are derived from `scripts/quality.sh` and `.github/workflows/quality.yml`.

Run focused tests during implementation. Add regression tests for behavioral or audio changes; do not create tests that only mirror style choices. Use before/after renders for visual edits. The broad checks run once after the candidate is complete. A failed gate or an uncertain result is pending work, not an improvement to publish.

## Prioritization

Session loss and audio defects outrank expansion. Otherwise choose frequent workflow friction and missing creative control before rare conveniences. Maintain forward movement across interaction quality, musical depth, DSP/runtime, and project/Linux completeness. Keep major architecture work in coherent slices. New controls require functioning model, editing, persistence, engine, and export behavior where applicable; avoid disabled promises and decorative placeholders.

Each completion needs a reproducible musical before/after, relevant evidence, and an honest next limitation. “More polished,” “less AI,” and “best DAW” are directions to investigate, not test results.
