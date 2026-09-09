# Anharmonic Studio sound and latency guide

This document describes the audio path that exists now, how to choose a buffer,
and what is still experimental. The short version: use **BUILD / MIX SAFE**
(512 frames) while compiles or model jobs share the machine, and **PRODUCTION**
(256) for tracking sessions that pass the measured headroom checks. The 128-frame profile is explicitly experimental.

## Audio buffer profiles

The transport bar changes the fixed PortAudio callback size. All profiles run
at the current fixed engine rate of 48 kHz.

| Profile | Frames | One block | Intended use |
|---|---:|---:|---|
| **EXPERIMENTAL · LIGHT** | 128 | 2.67 ms | experimental, very light pad work on a tuned wired system; never assume a full project is safe |
| **PRODUCTION** | 256 | 5.33 ms | writing, finger drumming, and normal production after checking the active wired device |
| **BUILD / MIX SAFE** | 512 | 10.67 ms | builds, model jobs, and dense sessions where stability matters more than pad response |
| **SAFE / BT** | 1024 | 21.33 ms | dense sessions, scheduler jitter, or Bluetooth where stability matters more than response |

Changing profile restarts the output stream, clears live voices and effect
state, and can make a brief gap. The selected profile is saved in application
settings. **SAFE / BT does not remove Bluetooth delay**; A2DP adds codec and
radio buffering beyond the application's block.

One block is a scheduling period, not an end-to-end latency claim. A newly
pressed pad can wait up to roughly one period for the next callback, then still
passes through PortAudio, PipeWire or JACK, the device buffer, conversion, and
the speaker or headphones.

### Linux and CachyOS runtime

`run.sh` builds the optional source-hashed native DSP before the app imports
its audio engine. A C compiler is needed; failure leaves the Python reference
available and is reported in the AUDIO menu. Oscillators, envelopes, synth
filters, and compressor/reverb smoothing execute compiled loops that release
the GIL. The original algorithms remain covered by numerical equivalence tests.
The synth warms up before its output stream opens.

At application startup, BLAS/OMP pools are limited to one thread. The DAW names
its own main thread `anharmonic-daw`, requests nice -4 when permitted, and uses
a 1 ms Python thread switch interval. Nice -4 matches the installed CachyOS
Player-Audio class. Only the PortAudio callback requests FIFO 70; existing
FIFO/RR policy is preserved. Export runs in its own interpreter with lower
priority and separate caches, so encoding does not share playback's GIL.

The app requests `PIPEWIRE_LATENCY=<frames>/48000`. This is **not proof of the
negotiated graph quantum**; inspect `pw-top` and PipeWire metadata. Forcing a
quantum changes the shared graph and should not happen during a take. See
[PipeWire's node properties](https://docs.pipewire.org/page_man_pipewire-props_7.html).

The small callback working set is page-locked when permitted, with mappings
released when buffers are replaced. Sample packs remain pageable. An 8 MiB
memlock limit is not automatically insufficient; the AUDIO menu reports the
actual locked size and scheduling result.

On this machine, performance governor/EPP, the CachyOS preemptible 1000 Hz
kernel, RT privileges and ananicy-cpp were already active. No global scheduler,
swap, kernel, or graph setting was changed. The installed `game-performance`
wrapper adds no CPU-profile benefit while performance is already selected.
CachyOS advises against combining GameMode niceness with ananicy-cpp; see its
[gaming guidance](https://wiki.cachyos.org/configuration/gaming/). sched_ext can
be evaluated separately, but switching it globally is not a substitute for
fixing callback deadline misses. See the
[CachyOS scheduler guide](https://wiki.cachyos.org/configuration/sched-ext/).

## Reading the callback diagnostics

The right side of the transport reports:

- the most recent callback as `% dsp`;
- the selected block period;
- PortAudio's reported output-latency estimate; and
- output underruns as `xrun`.

Hover it for p50, p99, worst callback time, and worst-case headroom over the
latest 512 callbacks. It also shows xruns since the last timing reset and
real-time cache misses accumulated since engine start. A cache miss is silenced
and counted instead of reading a sample from disk inside the callback. Load or
select media before performing so it can be decoded and cached on the UI
thread.

For a usable profile, require zero xruns, p99 no greater than half the block
period, and the worst callback below the period. For live performance, aim for
p99 below one quarter of the period. Test the densest musical passage,
not an idle project, for at least a minute.

The repeatable DSP-only test is:

```bash
uv run --no-sync python scripts/bench_callback.py
```

Release candidates also provide two longer-running gates:

```bash
uv run --no-sync python scripts/soak_callback.py       # one-hour callback soak
./scripts/run_hardware_tests.sh                         # physical devices only
```

The hardware suite deliberately remains opt-in: CI cannot prove USB switching,
PipeWire routing, cable-loopback accuracy, or hot-unplug recovery without the
release workstation and interface. Follow its printed hot-unplug procedure with
a disposable take; never unplug the device carrying an active user session.

The reference runs below hold either 4 or 16 pitched pad voices and retrigger
them every 250 ms. Gate-mode retriggers replace the previous voice for the same
pad, so the labelled voice count remains bounded instead of accumulating long
one-shots. They exclude PortAudio, the sound server, hardware, and OS scheduling,
so they are a floor rather than a promise.

### Production measurement

See [the September runtime report](LATENCY_RUNTIME_2026-09.md) and raw JSON in
`docs/benchmarks/` for piano, pads, mixed effects, and rate/buffer comparisons.
The old pads-only test does not establish piano or effects headroom.

```bash
./scripts/quality.sh
.venv/bin/python scripts/bench_production.py --seconds 2
.venv/bin/python scripts/soak_production.py --seconds 60 --frames 512
```

The rate matrix measures 44.1, 48 and 96 kHz. **Only 48 kHz is supported for
production mixing**: library imports and effects currently use that rate.
Non-48 kHz benchmark cases disable inserts/sends and are DSP experiments.
At 128 frames the existing synth voice cap is four; at 256 and above it is
eight. Increasing the rate also halves the deadline at the same frame count.

Song loops split processing at the sample boundary and render the remaining
part of the host buffer immediately. Prepared filters and microphone cue audio
survive that split. Fractional-sample loop lengths still quantize to samples.
The rhythm no longer loses the remainder of an entire callback at each wrap.

## Current sound-quality path

### High-quality library import

Imports become canonical stereo, 48 kHz, 24-bit PCM WAV files. FFmpeg first
uses SoXR at 28-bit precision with a 0.95 cutoff and high-pass triangular
dither. If the installed FFmpeg cannot run SoXR, import retries with its native
SWResampler using a 64-tap exact-rational configuration. The original external
file is not rewritten. Playback decodes the library copy to float32.

SoXR improves sample-rate conversion on import. It does not change the live
algorithm used when a pad is pitched.

### Pad cleanup

The selected pad has two non-destructive preparation tools:

- **NORMALIZE** sets pad gain so the selected source range peaks at -1 dBFS.
  Boost is capped at 4× (about +12 dB), and the source WAV is unchanged. This is
  peak normalization, not perceived-loudness or LUFS matching.
- **TIGHTEN** trims leading and trailing near-silence relative to the slice's
  own peak, retaining approximately 1 ms before and 3 ms after the detected
  body. The pad's start/end points change; the source WAV is unchanged.

Tighten first, then normalize if both are wanted. Audition the result in
context: noise, ambience, and reverb tails can be musically intentional.

### Cue bus, meters, and output protection

Browser/CHOP audition and the metronome use an independent cue bus. Track 1
mute, pan, inserts, and sends cannot hide or colour preview audio. The cue is
currently an internal bus, not a separately routable headphone output; it joins
the output after the master fader and before final limiting.

Mixer bars use a logarithmic -60 to 0 dBFS display. The filled bar follows RMS
level and the decaying line follows sample peak. Track meters are post-insert,
post-fader, and post-pan. The master meter is after master processing, the
master fader, cue summing, and the limiter. These are digital level meters, not
analogue VU, LUFS, or oversampled true-peak meters.

The final stereo-linked limiter has a -1 dBFS sample-peak ceiling and continuous
release state across callbacks. It is output protection, not a substitute for
gain staging or a true-peak delivery limiter.

## Choosing a working profile

1. Use a wired output while playing piano or pads; Bluetooth codec buffering
   remains outside the application's control.
2. Run `./scripts/tune_system.sh` for a read-only host audit. Missing access to
   a server socket is reported as unavailable, not diagnosed as broken audio.
3. Test the densest passage with normal production applications still running.
   Watch xruns, p99 and worst callback time. The health prompt warns at 50% p99
   load or a worst callback reaching the deadline; it does not change buffers
   automatically during playback.
4. Use 512 frames for dense mixes or concurrent builds. Try 256 for tracking
   only after the actual session passes. 128 remains experimental.
5. Change profiles between takes: restarting an audio stream interrupts sound.
   Keep the current chat and other required applications available.

## Remaining limitations

- **Pitch quality:** live pad pitch deliberately uses bounded linear
  interpolation. Export uses a separate eight-lobe windowed-sinc path with a
  rate-dependent low-pass for cleaner pitched bounces; it is not a time-stretch
  or formant-preserving algorithm.
- **Loop seams:** pads and playlist audio save an adjustable 0–50 ms tail/head
  crossfade. The default is 5 ms and is applied identically in playback and
  export; use zero for deliberately hard waveform loops.
- **Hard real time:** tone impulse responses and their fixed-block filter
  spectra are prepared outside the callback, pad-render scratch is reused, and
  Linux pins the small callback working set and requests FIFO scheduling. The
  full Python callback still creates objects/lists and NumPy
  temporaries, can resize if the host requests an unexpected block, executes
  FFT-based DSP, and shares project state. It is not allocation-free or a hard
  real-time guarantee.
- **Offline memory:** WAV export consumes fixed-size blocks and writes them
  directly to disk, retaining only active voices and eight block-sized track
  buses. The compatibility `render_offline()` API still joins blocks when a
  caller explicitly asks for one in-memory array.
- **Latency truth:** the UI shows block duration and PortAudio's output estimate,
  not a measured key-to-speaker or input-to-output round trip. The AUDIO SETUP panel offers cable-loopback calibration; an actual
  connected loopback is required. Block duration alone cannot replace it.
- **Meters:** there is no LUFS history, crest-factor view, inter-sample true
  peak, or clip-event log yet.
- **Routing:** cue level and hardware destination are not independently
  configurable yet.
- **Elastic audio:** playlist clips still have no tempo stretch/warp. Pitching
  a pad changes its duration.
