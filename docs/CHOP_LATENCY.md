# Chop interaction latency

The chop editor was rebuilding its entire button strip on each slice selection.
Waveform/playhead repaints also repeated the waveform scans and painted every
slice boundary. A waveform click emitted both a slice audition and a cursor
scrub. Bursts of scrub commands constructed every obsolete preview voice before
the callback could render the newest one.

The editor now retains its buttons and updates selection in place. Chop and
region buttons audition on press. Cached waveform and annotation layers keep
selection, hover, trimming and playhead overlays responsive; zoom, source,
marker, display scale and palette changes invalidate the relevant caches.
Waveform clicks send one slice audition; the ruler still scrubs from the cursor.
Within each callback, preview commands coalesce to the latest position, with
stop/panic cancellation preserving command order. Pad and musical events retain
their existing queue behavior.

## Reproduced measurements

Offscreen Qt benchmark: a 240-second, 48 kHz stereo record with 256 chops,
1680 × 1040 window, 12 selection/repaint iterations. These measure application
work, **not physical input-to-headphone latency**.

| Work | Before | After |
| --- | ---: | ---: |
| Slice selection and UI event processing, median | 425.57 ms | 8.50 ms |
| Waveform repaint, median | 54.26 ms | 2.58 ms |
| Drain 1,000 queued scrub requests, stress case | 208.03 ms | 0.16 ms |
| Voices created by that burst | 1,000 | 1 |

[Before data](benchmarks/chop-before.json), [after data](benchmarks/chop-after.json).
Reproduce using `.venv/bin/python scripts/bench_chop.py`. The script uses a
temporary library, memory-only settings and disabled audio startup; it creates
only offscreen windows.

Read-only inspection of the running DAW found USB EarPods playback at 48 kHz,
an actual 512-frame PipeWire quantum, and callback FIFO priority 70. Two live
PipeWire snapshots reported no errors. This is a short snapshot, not a loaded
chopping soak or a loopback measurement. The existing 512-frame profile was
retained; shrinking it would not resolve the measured 426 ms editor stall.

Regression coverage checks one preview per click, immediate button-press
dispatch, latest-position playback in the next rendered block, stop/panic
ordering, retained buttons and cache invalidation after edits/zoom.
Validation: 363 tests passed, 3 hardware-only tests skipped; the final
press-dispatch change also passed all 20 focused audio-control tests. Lint,
formatting, native build and callback checks passed. Warm piano/mixed callback
smoke tests and the two-second callback soak reported no deadline misses.
The separate cold piano probe peaked at 10.69 ms against a 10.67 ms period;
this work does not establish a smaller production buffer as safe.

The running process cannot load these Python changes in place. Save the project
and reopen MPC Labs normally to activate them. No DAW, audio server or chat
application was restarted by this work.
