#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BUILD_DIR="${ANHARMONIC_NATIVE_BUILD_DIR:-$ROOT/native/build}"

missing=()
for pkg in portaudio-2.0 alsa lilv-0; do
  if ! pkg-config --exists "$pkg"; then
    missing+=("$pkg")
  fi
done
if ((${#missing[@]})); then
  printf 'Missing native development packages: %s\n' "${missing[*]}" >&2
  printf 'Debian/Ubuntu: sudo apt install build-essential cmake pkg-config portaudio19-dev libasound2-dev liblilv-dev\n' >&2
  exit 2
fi

cmake -S "$ROOT/native" -B "$BUILD_DIR" -DCMAKE_BUILD_TYPE=Release
cmake --build "$BUILD_DIR" --parallel
printf 'Native engine built: %s/libanharmonic_native.so\n' "$BUILD_DIR"
