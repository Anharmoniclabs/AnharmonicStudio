# Piano, MPC pads and CachyOS runtime — 2026-09-05

Production mixing remains at **48 kHz**. Use **512 frames** for dense mixes;
256 is a tracking option when the actual session passes its headroom checks.
128 remains experimental. Higher sample rates are not a free latency upgrade.

## Measured DSP cost

Host: Ryzen 5 7430U, CachyOS `7.2.0-1-cachyos`, performance governor and EPP,
preemptible 1000 Hz kernel, ananicy-cpp active, sched_ext disabled. Existing
PipeWire clock was 48 kHz/256 frames. The active output was the onboard speaker;
the preferred USB EarPods device was not connected. No system settings changed.

The benchmark uses the real engine: eight-note piano chords, sixteen pitched
pads, or a mixed arrangement with six-note chords, eight pads, three insert
chains, delay, reverb, and master automation. Piano means the application's
synth played from its piano roll, not an external hardware piano. At 128 frames
the existing synth cap limits both note workloads to four voices.

| 48 kHz workload | Frames | Deadline | Before p99 | Final p99 | Final worst | Final late blocks |
|---|---:|---:|---:|---:|---:|---:|
| 16 pads | 256 | 5.33 ms | 1.51 ms | 1.61 ms | 1.80 ms | 0 |
| 8-note piano | 256 | 5.33 ms | 5.58 ms | 1.83 ms | 1.93 ms | 0 |
| Mixed + effects | 256 | 5.33 ms | 7.40 ms | 4.31 ms | 4.98 ms | 0 |
| 16 pads | 512 | 10.67 ms | 1.47 ms | 1.72 ms | 1.79 ms | 0 |
| 8-note piano | 512 | 10.67 ms | 8.07 ms | 1.20 ms | 1.22 ms | 0 |
| Mixed + effects | 512 | 10.67 ms | 10.06 ms | 3.04 ms | 3.13 ms | 0 |

The final piano p99 at 256 is about **67% lower**; mixed p99 at 512 is about
**70% lower**. Pad-only cost was not improved and varied slightly upward.
Mixed 256 still exceeds the recommended 50% p99 budget despite passing this
short deadline test. Mixed 128 missed 15 steady-state deadlines in the final
run. These results support 512 for dense production.

Before runs cover 1.5 simulated audio seconds per case; final runs cover two.
The first eight blocks are excluded from steady-state timing and their worst
cost is saved separately. CPU scheduling and frequency variation affect these
short runs. These are not physical latency or long-term xrun measurements.
Raw data: [before](benchmarks/latency-before.json),
[intermediate envelope/filter acceleration](benchmarks/latency-native.json),
[final rate matrix](benchmarks/latency-after.json).

## Sustained runtime validation

The [60-second paced 512-frame run](benchmarks/production-paced-512.json)
rendered 5,625 mixed callbacks with effects, metronome and repeated song-loop
boundaries. It reported **zero DSP deadline misses**, p99 **4.98 ms**, worst
**7.60 ms** against a **10.67 ms** period. p99 wake lateness was 0.036 ms.
This test uses wall-clock pacing but no audio device; xruns are therefore null,
not zero.

The silent tests exercised the real PipeWire/PortAudio callback with all test
output zeroed before delivery. Each opened and closed only its own stream.

| 60-second live test | Callbacks | p99 | Worst | DSP deadline misses | PortAudio xruns |
|---|---:|---:|---:|---:|---:|
| 256 frames | 11,248 | 3.83 ms | 7.19 ms | 19 | 0 |
| 512 frames | 5,623 | 4.24 ms | 7.56 ms | **0** | **0** |

Both reported **FIFO 70**. At 512 frames, PortAudio's output latency estimate
was **10.67 ms**; this is not a physical measurement. The 512 test retained the
existing `256/48000` host latency request and let the adapter accommodate its
larger application callbacks. It did not request a new desktop quantum.
The 256 result demonstrates why zero reported xruns alone is insufficient:
callbacks still missed their deadlines. These are finite one-minute tests,
not a guarantee for every project or device.

Raw live data: [256](benchmarks/production-live-256.json),
[512](benchmarks/production-live-512.json). Reproduce on this graph with:

```bash
.venv/bin/python scripts/soak_production.py --live --seconds 60 --frames 512
```

The live option checks for an existing 48 kHz/256-frame graph before opening
a silent stream. It needs access to the user's PipeWire socket.

## Sample rate and playable response

| Rate | 128 frames | 256 frames | 512 frames | Status |
|---|---:|---:|---:|---|
| 44.1 kHz | 2.90 ms | 5.80 ms | 11.61 ms | synth/pad benchmark only |
| 48 kHz | 2.67 ms | 5.33 ms | 10.67 ms | production engine, library and effects |
| 96 kHz | 1.33 ms | 2.67 ms | 5.33 ms | synth/pad benchmark only |

Each number is one callback period. Key-to-sound response also includes GUI
input delivery, the wait for the next callback, PortAudio/server buffering,
and device conversion. The AUDIO SETUP cable-loopback tool needs a physical
connection to measure round-trip latency. No cable-loopback result is claimed
in this report. Non-48 kHz benchmark runs disable inserts and sends because
the current production effects use 48 kHz coefficients and delay lengths.

## Changes that prevent avoidable stutters

- Compiled oscillator/envelope/filter loops preserve the reference patch sound
  and release Python's GIL. Compiled one-pole recurrences replace repeated FFTs
  in compressor/reverb smoothing. Tests compare reference/native output across
  patches, 44.1/48/96 kHz, odd buffer sizes, gates and release transitions.
- Native libraries build before launch; temporary synth voices warm up before
  opening live audio. Missing acceleration is visible in the AUDIO menu.
- A loop wrap now splits the current buffer and immediately renders the next
  part of the loop. It retains time previously dropped at every wrap. Filter
  spectra for shorter slices are prepared in advance, and microphone cue audio
  spans the boundary. Fractional-sample lengths still quantize to samples.
- WAV export uses a separate lower-priority process, snapshots cached samples,
  publishes atomically and cancels cooperatively. The playback interpreter no
  longer shares export DSP/encoding work.
- DAW startup limits BLAS/OMP pools, requests audio-app niceness when allowed,
  and sets a 1 ms interpreter switch interval. The actual callback retains or
  requests FIFO independently. Old locked scratch mappings are released safely.
- Health warnings begin at 50% p99 load or an isolated deadline overrun. They
  recommend a buffer change between takes instead of restarting mid-playback.

## CachyOS choices

The machine already has performance CPU policy and useful RT privileges.
The DAW requests nice -4, matching the installed Player-Audio class, rather
than installing a rule for every Python process. Export inherits a lower
priority. No chat process or window is targeted.

CachyOS documents `game-performance` as a temporary performance-profile wrapper
and warns against stacking GameMode's niceness with ananicy-cpp. On this host
performance was already selected, so another wrapper would not remove the
measured DSP bottleneck. See [CachyOS gaming](https://wiki.cachyos.org/configuration/gaming/).

sched_ext has audio-oriented choices, but changing the machine-wide scheduler
requires comparative workload measurements; it is not applied speculatively.
See [CachyOS sched_ext](https://wiki.cachyos.org/configuration/sched-ext/) and
[the kernel's policy scope](https://docs.kernel.org/scheduler/sched-ext.html).

`PIPEWIRE_LATENCY` expresses a request. The AUDIO menu now labels it that way;
actual clock/device data come from PipeWire. No forced quantum/rate is used.
See [PipeWire properties](https://docs.pipewire.org/page_man_pipewire-props_7.html).

`./scripts/tune_system.sh` now reports evidence without prescribing a kernel
replacement, unlimited memlock, global graph changes, or disabling Bluetooth.

## Practical limits

Validation: **333 tests passed, 3 hardware-only tests skipped**; the separate
Python DSP fallback suite passed **35 tests**. Ruff lint/format, syntax checks,
native compilation with strict C warnings, pad benchmark and soak gates passed.
The new production benchmark is also included in the quality script and CI.

Python still orchestrates the callback and allocates some objects; NumPy/FFT
mixing remains. This is not hard real time. CPU-heavy jobs, GUI stalls, thermal
throttling and device problems can still cause xruns. Use the densest passage
and the real device to select buffers. The updated code takes effect on the
next normal DAW launch; the running DAW and active chat were not restarted.
