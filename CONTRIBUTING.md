# Work on the free source

Anharmonic Studio's full application source is GPL-2.0-or-later. Official paid downloads
fund development; a purchase is not required to build the app or contribute.

Self-compiled installations and local toolchain configuration are not covered by
customer support; see [SUPPORT.md](SUPPORT.md). Reproducible application bugs and
contributions are welcome from source users.

Start with the [developer build guide](BUILDING.md), then use an
isolated checkout and temporary project/library data for changes and tests. Never use
valuable recordings as test fixtures. Keep songs, exports, credentials, agent state,
and compiled installers outside version control.

## Check a change

```sh
uv sync --locked --group dev
uv run --no-sync python scripts/build_native.py
uv run --no-sync python -m mpclab --self-check
uv run --no-sync python -m pytest -q tests/test_native_dsp.py tests/test_release_runtime.py tests/test_vocal_workspace.py
```

Use focused tests while developing and the repository's required CI checks before
merging. The self-check runs offscreen with disposable data; it does not verify real
microphones, interfaces, MIDI devices, or round-trip latency. Include platform and
hardware details when reporting a device-specific issue.

For UI changes, check narrow layouts as well as a full desktop window. Preserve keyboard
navigation and access to controls. For audio changes, describe the reproduction, affected
sample rate/buffer settings, and validation against the existing DSP behavior.

## Where changes belong

- `mpclab/`, `native/`: application and audio-engine source.
- `tests/`: meaningful behavior and regression checks.
- `website/`: public Pages site; [preview and checkout setup](website/README.md).
- `packaging/`, `scripts/`: native builds and source archives.
- [DISTRIBUTION.md](DISTRIBUTION.md): public source and planned paid-download terms.

Pull requests should explain the problem, resulting behavior, and validation. Do not
include private payment data, keys, or plaintext official installers. The release
workflow uploads encrypted candidates; the Pages workflow publishes only the website.
