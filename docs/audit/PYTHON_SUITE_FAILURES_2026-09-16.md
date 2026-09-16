# Python Suite Failures: 2026-09-16

## Source

- Canceled Source Checks run: `35105329911`
- Test job: `104824934508`
- Tested SHA: `c22bcf9e63107066f41af1ccf1ea82cef96406d0`
- Environment: GitHub Ubuntu 24.04, Python 3.12.14, locked uv environment

## Recovered failure evidence

The job log preserved pytest progress output but not a JUnit artifact before the
20-minute job timeout. It shows numerous `F` markers beginning around 10% and
continuing through approximately 90%, followed by a 30-second faulthandler dump.
The only individual test name preserved in the log is:

- `tests/test_vocal.py::test_dropped_capture_blocks_keep_the_timeline`
  - Traceback: `queue.Queue.join()` in the test, `threading.Condition.wait()`.
  - Subsystem: VocalRecorder capture writer.
  - Classification: `TRANCHE_2_REGRESSION`.
  - Cause: `VocalRecorder.start()` created the capture queue but did not start
    its `_write_capture` worker. No consumer called `task_done()`, so queue join
    could never return.

The exact names and tracebacks of the earlier `F` markers are not recoverable from
this canceled run because the workflow did not publish JUnit output before the
job was canceled. They must be collected by the next immutable CI run after the
fix; no assertion was weakened based on the marker count.

## Reproduction and fix

- Reproduced the hanging test in the locked Python 3.12 environment.
- Started the writer thread from `VocalRecorder.start()`.
- Added bounded `VocalRecorder.wait_until_flushed(timeout=2.0)` so tests and
  internal callers do not depend on raw `Queue.join()`.
- Preserved dropped-frame timeline filling with silence.
- Repeated the parameterized dropped-block test 10 times: 20/20 passed.
- Full pure `tests/test_vocal.py` outside the Qt conftest: 31/31 passed.

## Other known failure

The broader isolated persistence selection also exposed the pre-existing
`ExternalDSP.render_instrument` signature mismatch. That was repaired separately
in commit `c22bcf9` with a focused regression test; it is not a persistence or
vocal queue change.

## Required follow-up

A new immutable SHA must rerun the complete Source Checks workflow. The next run
must preserve per-test JUnit output even if a later test hangs; the suite remains
unverified until its full Python totals and all prior failure names are available.
