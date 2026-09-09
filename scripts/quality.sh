#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

uv lock --check
uv run --no-sync python scripts/build_native.py
uv run --no-sync ruff check mpclab tests scripts
uv run --no-sync ruff format --check mpclab tests scripts
uv run --no-sync python -m compileall -q mpclab tests scripts
uv run --no-sync python scripts/run_tests.py -- -q -o faulthandler_timeout=30
MPC_NATIVE_DSP=0 uv run --no-sync python scripts/run_tests.py -- -q -o faulthandler_timeout=30 tests/test_sample_instruments.py tests/test_synth.py tests/test_engine_synth.py
uv run --no-sync python scripts/bench_callback.py --seconds 0.5 --blocks 512 256
uv run --no-sync python scripts/bench_production.py --seconds 0.5 --rates 48000 --frames 512 --workloads piano mixed --max-p99-load 1.0 --max-late-fraction 0.05
uv run --no-sync python scripts/soak_callback.py --seconds 2 --blocksize 512 --voices 8 --max-late-fraction 0.2
