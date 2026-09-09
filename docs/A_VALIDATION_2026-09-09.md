# A-target validation — 9 September 2026

The current Linux application's software and playback checks meet the targets
of this pass. Microphone capture acceptance remains pending user consent; this
report does not certify physical recording, USB hot-unplug or round-trip latency.

## Changes and evidence

- Replaced scalar NumPy clipping in the callback's effects path with scalar
  arithmetic and reused convolution overlap buffers. Direct before/after
  comparisons produced bit-identical samples. In those microbenchmarks, the
  track-effects check took 0.199 s versus 0.242 s (18% less time), and reverb
  took 0.295 s versus 0.567 s (48% less). These are workload-specific results,
  not a claimed percentage improvement for every project.
- Variable-block convolution tests match direct mathematical convolution,
  including one-frame blocks, loop seams and reset. Native effects/new-feature
  follow-up: 63 passed; convolution regression: 3 passed.
- The complete snapshot run had 1,093 passing tests, three hardware skips and
  one obsolete menu-label expectation. The label now correctly expects
  “Record vocals in Song”; all four menu/ownership tests passed afterward.
  This covers 1,094 unique passing cases across the full run and follow-up;
  it is not represented as a single all-green full-suite invocation.
  Full-run peak RSS was 632.2 MiB, below the 1,536 MiB guard.
- Python fallback: 77 passed, including effects tests; peak RSS 201.1 MiB.
- Newer arpeggiator recording and vocal-interface changes were included in the
  source snapshot. A synthetic A3 recording opened from Song into the pitch
  editor and was detected at MIDI 57.066 (A3 = 57). The actual offscreen UI
  render was inspected. No microphone was used for that fixture.
- Lint, formatting, syntax and locked dependency checks passed on the snapshot.
  Linux bundle, disposable user installation, installed-copy self-check,
  project save/load, real export subprocess and FFmpeg conversion passed.

## Real audio-server playback under load

An explicitly silent output stream used the existing PipeWire 48 kHz / 256-frame
graph, with a 512-frame PortAudio callback. Only the test's own stream was opened
and stopped. Routing and graph settings were not changed; no microphone was
opened. The rendered workload included piano, pads, effects, looping and metronome,
but the final output was zeroed before reaching the output device.

The ten-minute run overlapped the full test suite and additional validation work:

| Measurement | Result |
|---|---:|
| Callbacks | 56,244 |
| Reported output underruns | 0 |
| Callback exceptions / nonfinite blocks | 0 / 0 |
| Stream continuously active | Yes |
| p99 processing elapsed time | 5.10 ms |
| Processing deadline | 10.67 ms |
| Late processing blocks | 1 (0.00178%) |
| Worst elapsed / thread CPU time | 10.99 / 8.29 ms |
| Callback scheduler | FIFO 70 |
| RSS start / peak | 43.39 / 73.33 MiB |
| RSS growth | 29.95 MiB |

It passed the 75%-of-deadline p99 and 0.1% late-block limits, and the 64 MiB live
process growth limit; actual growth was also below 32 MiB. The memory figure
includes opening the real audio host and retaining measurement arrays.
The host's reported 10.67 ms output latency is an estimate, not a cable-loopback
round-trip measurement.

This is stronger playback evidence than the earlier device-free stress failure:
the actual PortAudio callback received real-time scheduling and reported zero
underruns while other jobs ran. It does not erase the earlier result or guarantee
zero glitches with every device, project, driver or system workload.

## Reviewable artifacts

All application code was frozen before the full test/build run. The only
subsequent snapshot change was the corrected test label, incorporated into the
source archive. Application hashes matched the working tree at the intermediate
check. More concurrent edits arrived before final handoff; their file list is
recorded in `later-runtime-edits.json`. The results apply to the immutable
candidate and matching source archive, not to those subsequent edits.

- Linux candidate: `dist/a-validation-2026-09-09/linux/AnharmonicStudio-linux.tar.gz`
- Matching source: `dist/a-validation-2026-09-09/AnharmonicStudio-source.zip`
- Source SHA-256: `6655f79162bd524094e0788af20687cfd44d69e2db10917fdf719fb81d6d3edf`
- Linux SHA-256: `c50dee0926e5fa194618bb5352825b04c9a9cb71f3bc89e9758b0efb1e116a09`
- [Raw logs, measurements and inspected UI](benchmarks/a-validation-2026-09-09/)

This candidate targets the tested CachyOS x86-64 environment and compatible Linux
libraries; FFmpeg remains a system dependency. It is unsigned and unpublished.
Clean-machine/other-platform installers, dependency source/notice review and the
official distribution service remain separate release work.

The five-second EarPods microphone test was requested but has not been run.
Do not mark physical recording acceptance complete until consent and the actual
capture/reopen verification are recorded. Manual USB reconnect and longer
musician sessions remain useful acceptance checks beyond this software pass.
