#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

echo "Physical audio validation will briefly open every advertised output."
echo "Keep the active chat open; this script does not control any windows or processes."
MPC_HARDWARE_TEST=1 uv run --no-sync pytest -q tests/hardware

echo
echo "For calibrated input latency, connect a physical output to an input and use"
echo "AUDIO SETUP → MEASURE LOOPBACK inside Anharmonic Studio."
echo "For hot-unplug validation, record a disposable take, unplug only the target"
echo "audio interface, confirm the take remains recoverable, then reconnect it."
