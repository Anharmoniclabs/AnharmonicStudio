#!/usr/bin/env bash
# Read-only evidence audit; never applies system changes.
set -euo pipefail
exec python3 "$(dirname "$0")/audio_audit.py" "$@"
