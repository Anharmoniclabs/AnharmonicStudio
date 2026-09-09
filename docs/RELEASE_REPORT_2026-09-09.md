# Linux release-candidate work — 9 September 2026

The application now has a locally tested Linux bundle and per-user installer,
separate storage for songs, functioning exports from the bundled executable,
and another corrected arrangement defect. This is engineering evidence for a
release candidate, not an A++ certification or a cross-platform release.

**Snapshot boundary:** newer concurrent edits arrived in the shared workspace
during final validation, affecting the engine, Browser, main window and track
recording, and adding vocal-pitch UI work. The final workspace check found a
lint error in that new module and formatting differences. These incoming edits
were preserved. The passing results and installer below describe the tested
snapshot, not blanket approval of the newer working tree. Its runtime source
hashes and the detected later-file list are saved with the measurements.

## What changed for musicians

- Updating an installed app keeps songs outside its program folder. Installation
  copies a new app version before updating the application-menu entry; a failed
  copy leaves the existing launcher intact.
- The installed app can save/reopen projects, export in its separate worker
  process and convert audio with the system's FFmpeg. Those paths were tested
  from both the initial bundle and an installed copy in a folder with spaces.
- Export snapshots use library disk storage instead of RAM-backed `/tmp`.
- Beat detection processes short chunks instead of constructing giant arrays
  for the entire recording. Its results match the reference across chunk seams.
- Fresh projects select a drawable pattern. A Qt placeholder had left the
  picker unselected, preventing drawing/duplication and leaving the inspector
  hidden. Explicit audio-sample selection still behaves as intended.
- Hardware checks collect callback failures and assert them on the test thread,
  avoiding false passes when the audio library merely prints a callback error.

The earlier memory ownership, recording recovery and timer-lifetime changes
remain covered by the full suite. No language rewrite was necessary for these
fixes.

## Completed checks

| Check | Result |
|---|---|
| Final full application/native/controller suite | 1,076 passed; 3 hardware tests skipped; 555.26 seconds |
| Full-suite peak RSS | 624.8 MiB; below the 1,536 MiB guard |
| Python DSP fallback | 58 passed; 201.4 MiB peak RSS |
| Arrangement fix follow-up | 50 passed, including both previously failing cases |
| Analysis/reference checks | 21 passed |
| Synthetic ten-minute audio analysis | 1.92 seconds; 19.14 MiB additional peak RSS |
| Frozen and installed app | Offscreen UI, project round-trip, actual subprocess export, FFmpeg conversion and window release passed |
| Installer rollback | Failed installation preserves the previous launcher/build and songs |
| Archive | 174.8 MiB; checksum and member paths verified; no personal projects/library |
| Serial ten-minute mixed playback | Passed unchanged thresholds; 4/56,250 late blocks (0.0071%); p99 5.69 ms; 8.50 MiB RSS growth |
| Static checks | Ruff lint/format, syntax, lockfile and diff checks passed; workflow YAML parsed |

The first full run found two arrangement failures; both were corrected and the
entire suite was rerun successfully. The final packaged runtime contains that
fix. GitHub workflows were updated locally; no push, remote CI success or public
binary publication is claimed.

The separately generated `AnharmonicStudio-working-source-unvalidated.zip`
contains the later working tree. It is explicitly labeled unvalidated and must
not be mistaken for a matching, tested source release of this installer.

## Timing evidence

The first ten-minute mixed-instrument/effects soak ran alongside builds and the
full test suite. At 48 kHz / 512 frames, 94 of 56,250 callbacks (0.167%) exceeded
the 10.67 ms processing deadline, failing the unchanged 0.1% late-block threshold.
The p99 was 7.51 ms, worst 22.99 ms, RSS growth 6.44 MiB and all audio was finite.
This is a real failed stress measurement; it has not been omitted or relabeled.

A subsequent one-minute diagnostic passed with zero misses, p99 6.26 ms and
1.96 MiB RSS growth. A separate mixed-workload comparison also had zero misses:
p99 5.76 ms at 512 frames and 7.62 ms at 1024 frames (21.33 ms deadline).

After the final full suite and all builds finished, the ten-minute test was
repeated on its own with the same thresholds. It passed: 4/56,250 blocks
(0.0071%) exceeded the deadline, p99 was 5.69 ms, worst elapsed time 14.26 ms,
and RSS grew 8.50 MiB (45.08 → 53.58 MiB). Audio stayed finite throughout.
This is a threshold pass, not a claim of zero late blocks.

For those four late blocks, measured callback-thread CPU time was 8.01–8.48 ms,
below the 10.67 ms deadline; none crossed a loop seam. Across the run, worst
thread CPU time was 9.89 ms. The difference points to scheduling/waiting time,
but does not establish that physical-device playback would be glitch-free.

These are software processing measurements, including operating-system scheduling
delays. The diagnostic additionally records callback-thread CPU time. They are
not physical round-trip latency or driver-xrun measurements.

## Local candidate and remaining release work

The tested candidate is at
`dist/linux-release-check-2026-09-09/AnharmonicStudio-linux.tar.gz`.
SHA-256:
`ef40a496753d03ccff8dbc8ff979cba1979f452590012425e61fd3462eabd60d`.
Extract the entire folder. `./AnharmonicStudio --install` installs it for the
current user; `--self-check` validates it without opening audio devices.

It includes Python/Qt and the compiled audio helper, but requires system FFmpeg
and compatible Linux graphics/audio libraries. The local build uses CachyOS
x86-64 and glibc 2.44. The configured Ubuntu build needs a successful remote run
and clean-machine acceptance; Windows/macOS bundles are not supplied. Optional
AI stem separation still uses the source installation.

Real microphone/interface recording, USB reconnect, cable-loopback latency,
long musician sessions, other promised platforms, signing, full dependency
source/notice review and the private official download service remain release
gates. A read-only Linux inventory found built-in audio and USB EarPods; none of
their audio streams were opened by this validation.

[Raw measurements and build receipts](benchmarks/release-2026-09-09/),
[installation instructions](INSTALL.md), and
[remaining acceptance procedure](RELEASE_ACCEPTANCE.md).
