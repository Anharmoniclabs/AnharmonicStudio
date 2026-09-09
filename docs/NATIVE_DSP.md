# Native synth acceleration and project-safety follow-up

Reviewed September 5, 2026.

The subtractive synth has an optional C renderer for its oscillator, envelope,
nonlinear filter and stereo output loops. It releases the Python GIL during
those loops. This is acceleration of the existing synth, not a replacement of
the whole audio engine with an allocation-free native render graph.

## Build and fallback

`./run.sh` builds the versioned local binary before starting the application.
A C99 compiler and the system math library are required. The binary cache under
`.native/` is ignored by Git; changing the source or compilation flags selects
a new binary. Build failure leaves the Python reference renderer available.
Direct module launches can prepare acceleration with:

```sh
uv run --no-sync python scripts/build_native.py
uv run --no-sync python -m mpclab
```

Use `MPC_NATIVE_DSP=0` to select the Python reference for comparison. The audio
status tooltip identifies the active backend. Startup exercises a private synth
voice before opening PortAudio so initialization does not happen on the first
played note. The warmup audio is never sent to an output device.

## Correctness and performance

The native/reference comparison covers all ten factory patches at 44.1, 48 and
96 kHz, odd block lengths, delayed note starts, timed gates and manual note-off.
The production workstation and its effects still use **48 kHz**; these tests
are not support claims for running the entire workstation at other rates.

Reproduce the benchmark with:

```sh
uv run --no-sync python scripts/bench_production.py --rates 48000 --frames 256 512 --workloads piano mixed --seconds 5
MPC_NATIVE_DSP=0 uv run --no-sync python scripts/bench_production.py --rates 48000 --frames 256 512 --workloads piano mixed --seconds 5
```

Saved measurements are in [native-production.json](benchmarks/native-production.json)
and [python-production.json](benchmarks/python-production.json). Runs are short,
device-independent CPU measurements. Their cold-start samples are reported
separately; they are not real output-underrun or measured interface-latency tests.
The mixed workload includes sequenced pads, six pitched notes per chord,
arrangement automation, inserts and shared effects sends. Do not infer
hour-long or concurrent-render reliability from this benchmark.

Native memory is checked at the Python boundary for dtype, contiguity and shape.
The application still allocates Python/NumPy objects per block and shares mutable
project state with its callback. Full engine ownership and hard-real-time safety
remain architecture work.

## Project safety

- Save remembers the opened project path, even after changing its display name.
- Save As (`Ctrl+Shift+S`, File menu) selects a new active destination. Cancellation
  or write failure preserves the previous destination.
- Opening a different project from the File dialog offers Save/Discard/Cancel
  when there are unsaved changes.
- A new beat-builder project clears the previous save destination so it cannot
  silently overwrite the project it replaced.
- A failed recovery save refuses the DAW close and reports the error. It does not
  silently throw away the session.

Regression tests cover saved-path retention, Save As cancellation and failure,
and refusing a close when the recovery write fails. Existing history and
recovery tests remain part of the full quality suite.

## Verification record

The full local quality run during this follow-up passed: **325 tests passed,
3 hardware tests skipped**, dependency lock verification, Ruff lint/format,
Python compilation, and callback smoke checks. Its two-second soak completed
1,631 callbacks with no late callbacks and no RSS growth. A separate run with
`MPC_NATIVE_DSP=0` passed all 33 synth/workstation fallback tests. C99 syntax
checking with `-Wall -Wextra -Werror` also passed.

Additional loop-boundary and export-isolation changes arrived in the shared
workspace during verification. The subsequent focused transport/workstation
run passed **27 tests**. These results describe the files exercised at those
points; they do not certify subsequent edits from another session.
