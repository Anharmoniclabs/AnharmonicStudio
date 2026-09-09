# Reliability work and release gates

This report describes engineering evidence, not an A++ certification.

The [subsequent Linux release-candidate report](RELEASE_REPORT_2026-09-09.md)
contains the latest complete suite (1,076 passing tests), installer checks and
longer timing evidence. The first-pass measurements below are retained as history.

## Changes

- Forward and reverse library audio share a 256 MiB decoded heap budget.
  Overflow is decoded in fixed blocks to disk-space-reserved anonymous file mappings in
  `library/_cache/`, preserving realtime cache lookups and active voices.
  Waveform/overview caching has a separate 16 MiB budget; analysis uses bounded
  scratch blocks and includes the final partial waveform bucket.
- Recording queue entries carry frame positions. Dropped software queue blocks
  become silence at the same position, including trailing drops. Monitoring
  errors cannot stop dry capture. A stalled writer returns a retryable error.
- Capture decoding uses bounded heap/file-backed storage. Original WAV spools
  survive decode, writer and library-save failures; the Song and Vocal panels
  support retry and explicit discard. Pending takes block project replacement
  and closing. Successful library saves flush the WAV before releasing recovery.
- Qt editor references to the workstation are non-owning. Hidden controls have
  explicit parents, accepted window closes destroy the native window, and UI
  timers stop. Delayed callbacks have QObject contexts; late worker results
  are ignored after their destination is destroyed. Session history and pattern
  actions now live in separate modules.
- Default pytest discovery includes `automation/`. Local quality checks, CI and
  the automation controller run pytest through a 1536 MiB RSS guard. Exceeding
  the ceiling fails the check and terminates only the spawned test child.
  The production benchmark now supports enforceable p99/deadline thresholds.
- Source installation instructions cover clean Linux hosts and format 3.
  A deterministic source ZIP builder excludes personal media and build output.

## Validation

All musical fixtures and UI previews use disposable synthetic projects.

| Check | Result |
|---|---|
| Full native/application/controller suite | 1,063 passed, 3 opt-in hardware tests skipped; 624.71 seconds |
| Full-suite peak process RSS | 625.1 MiB; memory ceiling was not triggered |
| Python DSP fallback | 58 passed; 201.1 MiB peak RSS |
| Final storage/library follow-up | 20 passed, including two added cases for disk reservation failure and detached source slices |
| Static checks | Locked dependencies, Ruff lint/format and syntax checks passed |
| Native builds | C DSP helper and C++ engine built; native engine ABI loaded |
| Actual Qt renders | Normal dark and narrow light layouts rendered and inspected offscreen |
| Source bundle | Deterministic ZIP builder verified; extracted source imports and builds checked |

The full suite preceded the final disk-reservation and detached-slice safeguards;
the 20-test storage/library follow-up verifies those changes. The Python fallback
checks exercise the reference path separately from the native suite.

At 48 kHz / 512 frames (10.67 ms deadline), each production workload ran for
937 blocks, representing 10 seconds of audio:

| Workload | p50 | p99 | Worst steady block | Missed deadlines |
|---|---:|---:|---:|---:|
| Piano | 1.44 ms | 2.17 ms | 2.46 ms | 0 |
| Mixed pads, piano and effects | 3.67 ms | 5.43 ms | 6.62 ms | 0 |

The 30-second wall-clock soak exercised 16 pad voices and 26,164 callbacks:
zero late callbacks, 4.44 ms worst block and zero peak-RSS growth. The separate
16-voice buffer comparison supports keeping 512 frames as the default; 256
frames used 51% of its deadline at p99 and did not meet that benchmark's desired
headroom. These are software measurements under desktop/concurrent validation
load, not hardware latency measurements. [Raw metrics and host context](benchmarks/reliability-2026-09-09/).

An earlier full-suite reproduction ended in a native segmentation fault after
771.9 seconds, with peak RSS of 8,156,792 KiB. This exposed retained Qt/Python
window graphs in addition to the cache and capture defects. The new lifetime
regression checks that repeated closed windows release their engines; it must
pass independently of the suite's memory guard.

## Language and realtime boundaries

The GUI and coordination remain Python/Qt. The active engine already delegates
native synth/effects work to C; the separate C++ native engine builds but is not
the GUI playback backend. The faults above concern memory ownership, queue
semantics and recovery, so translating the whole application would not by itself
resolve them.

File mappings bound retained decoded heap; they do not cap total RSS or promise
zero disk page faults. Active voices can retain older buffers, and Qt, DSP,
operating-system pages and offline analysis consume additional memory. Cache
preparation still runs outside the audio callback and can take time for large
projects. The Python callback is not allocation-free hard realtime.

## Remaining release gates

- Physical recording/playback, USB disconnect/reconnect and cable loopback
  latency on the intended interfaces; unattended software tests cannot certify
  hardware xruns or round-trip latency.
- Extended real-session soak tests on release machines, including large projects
  on slow or nearly full storage and optional stem separation under playback.
- Signed, validated bundles for each promised platform, plus the private paid
  download/update service. A local Linux folder bundle and per-user installer
  now pass frozen and relocated-copy checks; that candidate still requires
  system FFmpeg and build-compatible Linux libraries. Windows/macOS and clean
  Ubuntu release-machine acceptance remain pending. See [installation](INSTALL.md).

Recovery WAVs after an interrupted session are available under
`projects/recordings/` and can be imported through the Browser. Device-level
input overflows are counted; silence reconstruction here covers software queue
loss with known frame positions, not audio the hardware never delivered.

## Release follow-up

The next pass separates bundled application resources from writable user songs,
adds the frozen export-worker entry point, and restores system library paths
when launching FFmpeg or PipeWire utilities. Export snapshots use library disk
storage instead of the potentially RAM-backed `/tmp`. Beat analysis streams
256 spectral windows at a time and preserves results across chunk boundaries.

The Linux builder exercises the actual bundled application, installs it into a
disposable prefix with spaces, and repeats its checks from the relocated copy.
User installation preserves previous build folders and replaces the menu entry
only after the new copy succeeds. The hardware test harness now checks callback
observations from the test thread; exceptions inside PortAudio callbacks cannot
silently turn an interrupted stream into a passing test.

The first ten-minute mixed-workload soak during concurrent builds/tests failed
its strict late-block threshold: 94/56,250 blocks (0.167%) exceeded 10.67 ms.
Audio stayed finite and RSS grew 6.44 MiB. The failure remains in the
[raw follow-up measurements](benchmarks/release-2026-09-09/) rather than being
hidden by a relaxed threshold. A subsequent one-minute diagnostic and the
512/1024-frame processing comparison had zero over-budget blocks. Hardware
latency and driver underruns remain unmeasured in these software checks.

The final ten-minute run after builds/tests finished passed the same thresholds:
4/56,250 late blocks (0.0071%), p99 5.69 ms, 8.50 MiB RSS growth and no invalid
audio. Late blocks used less than 10.67 ms of thread CPU time; elapsed wall time
still exceeded the deadline. These results support the current architecture
while leaving scheduling and physical-device acceptance to verify.

See [release acceptance](RELEASE_ACCEPTANCE.md) for the remaining musician,
device and distribution checks.
