#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BUILD_DIR="${ANHARMONIC_NATIVE_BUILD_DIR:-$ROOT/native/build}"

cmake -S "$ROOT/native" -B "$BUILD_DIR" -DCMAKE_BUILD_TYPE=Release "$@"
cmake --build "$BUILD_DIR" --parallel
ctest --test-dir "$BUILD_DIR" -C Release --output-on-failure
