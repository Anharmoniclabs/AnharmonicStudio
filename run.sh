#!/usr/bin/env bash
# Launch Anharmonic Studio. Everything runs locally; nothing leaves this machine.
set -euo pipefail
cd "$(dirname "$0")"

command -v ffmpeg >/dev/null || { echo "ffmpeg is required: sudo pacman -S ffmpeg"; exit 1; }
command -v uv     >/dev/null || { echo "uv is required: https://astral.sh/uv"; exit 1; }

# --inexact preserves the optional stem engine when launching the lightweight
# core install. --no-sync on the run keeps launches fast and deterministic.
uv sync --locked --quiet --inexact
# Compile once, before Python imports the synth or opens an audio stream.
# The source hash selects the binary, so edits rebuild automatically.
if [[ "${MPC_NATIVE_DSP:-1}" != "0" ]]; then
    uv run --no-sync python scripts/build_native.py || \
        echo "Continuing with Python DSP; larger audio buffers may be needed." >&2
fi
exec uv run --no-sync python -m mpclab "$@"
