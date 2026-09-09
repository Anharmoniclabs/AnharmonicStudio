#!/usr/bin/env python
"""Run pytest with a Linux RSS ceiling so regressions cannot exhaust the desktop."""

import argparse
import json
import os
from pathlib import Path
import resource
import subprocess
import sys
import time


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--max-rss-mib", type=float, default=1536)
    parser.add_argument("--report", type=Path)
    args, pytest_args = parser.parse_known_args()
    if args.max_rss_mib <= 0:
        parser.error("memory ceiling must be positive")
    if pytest_args[:1] == ["--"]:
        pytest_args = pytest_args[1:]
    env = os.environ.copy()
    env.update(OPENBLAS_NUM_THREADS="1", OMP_NUM_THREADS="1", MKL_NUM_THREADS="1")
    started = time.monotonic()
    peak = 0
    exceeded = False
    # This handle identifies only our newly spawned headless pytest process.
    # Never search for or signal desktop applications or sibling processes.
    with subprocess.Popen([sys.executable, "-m", "pytest", *pytest_args], env=env) as process:
        while process.poll() is None:
            try:
                status = Path(f"/proc/{process.pid}/status").read_text()
            except FileNotFoundError:
                status = ""
            for line in status.splitlines():
                if line.startswith(("VmRSS:", "VmHWM:")):
                    peak = max(peak, int(line.split()[1]))
            if peak > args.max_rss_mib * 1024:
                exceeded = True
                print("FAIL: pytest exceeded its memory ceiling", file=sys.stderr, flush=True)
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
                break
            try:
                process.wait(timeout=0.25)
            except subprocess.TimeoutExpired:
                pass
        code = 1 if exceeded else process.returncode
    peak = max(peak, resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss)
    report = dict(exit_code=code, peak_rss_kib=peak, seconds=time.monotonic() - started)
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(report, indent=2) + "\n")
    print(f"Test process peak RSS: {peak / 1024:.1f} MiB", flush=True)
    return code if code >= 0 else 128 - code


if __name__ == "__main__":
    raise SystemExit(main())
