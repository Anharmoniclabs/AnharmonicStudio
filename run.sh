#!/usr/bin/env bash
# Launch an already prepared developer environment. See BUILDING.md for setup.
set -euo pipefail
cd "$(dirname "$0")"

if [[ ! -x .venv/bin/python ]]; then
    echo "Developer environment is not prepared. Follow BUILDING.md to install dependencies and build native DSP." >&2
    echo "Ready-to-install packages: https://anharmoniclabs.github.io/AnharmonicStudio/#download" >&2
    exit 1
fi
command -v ffmpeg >/dev/null || { echo "FFmpeg is required; see BUILDING.md for platform prerequisites." >&2; exit 1; }
command -v ffprobe >/dev/null || { echo "ffprobe is required; see BUILDING.md for platform prerequisites." >&2; exit 1; }

# Dependency installation and compilation are explicit development steps.
# Launching does not download packages, create an environment, or rebuild code.
exec .venv/bin/python -m mpclab "$@"
