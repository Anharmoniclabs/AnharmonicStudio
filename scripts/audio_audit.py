#!/usr/bin/env python3
"""Read-only CachyOS/PipeWire runtime audit; no privileged fixes or restarts."""

import gzip
from pathlib import Path
import platform
import resource
import shutil
import subprocess


def read(path):
    try:
        return Path(path).read_text().strip()
    except OSError:
        return "unavailable"


def command(*args):
    if not shutil.which(args[0]):
        return "not installed"
    try:
        result = subprocess.run(args, capture_output=True, text=True, timeout=5)
        return result.stdout.strip() or result.stderr.strip() or f"exit {result.returncode}"
    except (OSError, subprocess.TimeoutExpired) as exc:
        return str(exc)


def main():
    print(f"Anharmonic Studio audio audit · {platform.release()}")
    print("Read-only; server access may be unavailable inside a sandbox.")
    for label, path in (
        ("Governor", "/sys/devices/system/cpu/cpu0/cpufreq/scaling_governor"),
        ("EPP", "/sys/devices/system/cpu/cpu0/cpufreq/energy_performance_preference"),
        ("sched_ext", "/sys/kernel/sched_ext/state"),
        ("VM swappiness", "/proc/sys/vm/swappiness"),
    ):
        print(f"{label}: {read(path)}")
    print(f"RT priority soft/hard: {resource.getrlimit(resource.RLIMIT_RTPRIO)}")
    print(f"Locked memory bytes soft/hard: {resource.getrlimit(resource.RLIMIT_MEMLOCK)}")
    print("The DAW requests callback FIFO 70 and pins a small working set; an 8 MiB limit")
    print("is not automatically a failure. Inspect the app's actual mlock/RT result.")
    try:
        with gzip.open("/proc/config.gz", "rt") as handle:
            config = handle.read().splitlines()
        print(
            "Kernel: "
            + " · ".join(
                line
                for line in config
                if line.startswith(
                    (
                        "CONFIG_PREEMPT=",
                        "CONFIG_PREEMPT_RT=",
                        "CONFIG_PREEMPT_DYNAMIC=",
                        "CONFIG_HZ=",
                    )
                )
            )
        )
    except OSError:
        print("Kernel build configuration unavailable")
    print("A kernel name or tick rate alone cannot establish an audio buffer floor.")
    print("CachyOS services: " + command("systemctl", "is-active", "ananicy-cpp", "scx_loader"))
    print("game-performance: " + (shutil.which("game-performance") or "not installed"))
    print("Do not stack GameMode niceness with ananicy-cpp. The DAW uses its own")
    print("Player-Audio-style main-thread priority; export has lower priority.")
    print("PipeWire clock metadata:\n" + command("pw-metadata", "-n", "settings"))
    print("Active devices and routes:\n" + command("wpctl", "status"))
    print("48 kHz is the production DSP rate. 256 frames = 5.33 ms per block;")
    print("512 = 10.67 ms. These are periods, not measured round-trip latency.")
    print("Validate the busiest passage: zero xruns, p99 < 50% of the period,")
    print("worst callback < period. Change buffers between takes, then measure again.")
    print("No global scheduler, kernel, swap, Bluetooth or graph changes are implied.")


if __name__ == "__main__":
    main()
