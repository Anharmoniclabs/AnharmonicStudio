# Anharmonic Studio

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="assets/branding/logo-pack/01-Logos/anharmonic-horizontal-white.svg">
  <img src="assets/branding/logo-pack/01-Logos/anharmonic-horizontal-ink.svg" alt="Anharmonic Studio" width="480">
</picture>

A native desktop music studio for sampling, beatmaking, synthesis, arranging,
vocal production, and mixing. Import a sound, cut it into playable slices,
build a rhythm, write notes, arrange a song, and export a stereo WAV.

[Explore the studio and watch the film](https://anharmoniclabs.github.io/AnharmonicStudio/)

## Open source and the Windows pack

The application source code is free under **GPL-2.0-or-later**. Download, build,
modify, and run the source without buying an official installer.

The official Windows EXE pack is coming soon. Release details will be announced
on the website; no price is announced yet. Source access remains free.
Packaged installers are distributed separately from this public repository.
Recipients retain the rights provided by the GPL.

## What is inside

- **Sampler:** import audio, trim ranges, detect transients, and map slices to four 4×4 pad banks.
- **Beats:** program step patterns and control per-hit velocity.
- **Instruments:** explore factory sounds, shape analog synth patches, and record arpeggiator notes.
- **Notes:** write synth parts or play samples chromatically in the piano roll.
- **Song:** arrange patterns and audio clips, create variations, and automate levels and pan.
- **Mix:** balance tracks with EQ, saturation, compression, delay, and reverb.
- **Vocals:** record directly into Song, open recorded clips in Autotune, build comps, and render pitch correction to a new take.
- **Export:** render the arrangement to a 48 kHz stereo WAV.
- **Devices:** automatically connect MIDI inputs, remember controller mappings, and load external VST3 instruments and effects.

The optional stem-separation engine requires extra dependencies and an initial
model download. The core studio works locally after installation.

## Run from source on Linux

Requires Python 3.12, [uv](https://astral.sh/uv), FFmpeg (including ffprobe),
a C compiler, and the system libraries needed by Qt and PortAudio.

```bash
git clone https://github.com/Anharmoniclabs/AnharmonicStudio.git
cd AnharmonicStudio
./run.sh
```

The launcher installs the locked Python environment and builds the native DSP
helper. For system dependencies on Debian/Ubuntu:

```bash
sudo apt-get install build-essential ffmpeg libegl1 libgl1 libopengl0 libxkbcommon0 libportaudio2 libsndfile1
```

To install the optional stem engine:

```bash
./install-separation.sh
```

To build the additional native engine, install CMake, pkg-config,
PortAudio development files, ALSA development files, and Lilv development files,
then run `bash scripts/build-native.sh`.

## Validate or build a package

```bash
uv sync --locked --group dev
uv run --no-sync python scripts/build_native.py
uv run --no-sync python -m mpclab --self-check
uv run --no-sync python scripts/run_tests.py -- -q
```

The self-check uses a disposable offscreen session and does not open audio devices.
Hardware tests are optional and separate from ordinary automated checks.

## MIDI controllers and plugins

Open **Tools → Devices & Plugins**. MIDI inputs are checked every second and
reconnected automatically. Choose **Keys**, **Pads**, or the combined mode
(keys plus pads on MIDI channel 10). Use **MIDI Learn** to assign unfamiliar
pad layouts, transport buttons, knobs, and faders. Mappings are remembered;
unplugging a controller releases its held notes. MPK/MPC compatibility depends
on the device exposing a standard MIDI input; proprietary modes may require
the manufacturer's driver or a MIDI/controller-mode setting.

The plugin scanner checks standard installation folders and folders you add.
Load one VST3 instrument for synth notes and one master effect. Parameters and
presets save with the project and are used during WAV export. Pitch bend and
unmapped MIDI CCs reach the external instrument during live playing. Native
plugin windows, MIDI output/clock synchronization, and recording expression
automation are not implemented yet. Plugin controls use the studio's parameter
panel; applying changes reloads that plugin.

Plugins must match your operating system and processor. VST2, CLAP, and LV2
are listed but cannot be loaded by this host. Audio Unit support is available
through the host on macOS; this integration has only been tested on Linux.
Linux checks include the Nekobi instrument and MVerb effect; other plugins may
need different bus layouts or host features. Each plugin runs in a separate
process. A failed instrument is silenced and a failed effect is bypassed;
failed exports report the error. Live monitoring adds two audio buffers per
loaded plugin (about 21 ms each at 48 kHz / 512 frames).

The Audio interfaces tab refreshes available outputs, including Linux PipeWire
devices. Choose the desired routing in Audio setup; connecting hardware does
not automatically change your recording input or output.

Run `bash scripts/quality.sh` for the complete source checks, tests, Python audio
fallback checks, and callback benchmarks. Start with the default 48 kHz /
512-frame buffer; smaller buffers need more processing headroom.

If saving a recording fails, use **Retry save** in Song or **Retry save take** in
the vocal editor. Recovery WAVs remain in `projects/recordings/` until the take
is saved or explicitly discarded; after an interrupted session, import them
through the Browser. Keep `library/` alongside your source-checkout projects.

Create a source archive:

```bash
uv run --no-sync python scripts/build_source_bundle.py dist/AnharmonicStudio-source.zip
```

The Linux packaging script uses `requirements-build.txt` and is intended for a
separate build environment. It creates a local, unsigned Linux bundle, not a
Windows EXE. Generated bundles and installers stay outside version control.

## Logo pack

The [complete 84-file logo pack](assets/branding/logo-pack) includes outlined SVGs,
transparent PNGs, light/dark and accent versions, Windows and macOS icons,
favicons, social artwork, and a four-page usage guide.
The desktop uses `assets/branding/anharmonic-studios.svg` for its icon and
`assets/branding/anharmonic-header.svg` for the theme-aware project header.
The pack's [usage notes](assets/branding/logo-pack/README.txt) cover its formats.

## Repository contents

- `mpclab/`: application and audio engine source.
- `native/`: C++ engine source and build configuration.
- `assets/`: application artwork and licensed factory instrument recordings.
- `scripts/`: build, installation, and validation tools.
- `tests/`: automated tests.
- `website/`: the public landing page and one-minute commercial.

Personal libraries, songs, recordings, exports, development docs, agent automation,
credentials, caches, and compiled builds are excluded. This repository starts
with clean source history; it does not import the former development repository's history.

The orchestral recordings are CC0, with their dedication retained alongside the
assets. Other dependencies and media keep their own licenses. See [LICENSE](LICENSE)
and [THIRD_PARTY.md](THIRD_PARTY.md).
