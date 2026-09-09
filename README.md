# Anharmonic Studios

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
- **Instruments:** explore factory sounds, shape analog synth patches, and use the arpeggiator.
- **Notes:** write synth parts or play samples chromatically in the piano roll.
- **Song:** arrange patterns and audio clips, create variations, and automate levels and pan.
- **Mix:** balance tracks with EQ, saturation, compression, delay, and reverb.
- **Vocals:** record takes, build comps, and render pitch correction to a new take.
- **Export:** render the arrangement to a 48 kHz stereo WAV.

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

Create a source archive:

```bash
uv run --no-sync python scripts/build_source_bundle.py dist/AnharmonicStudio-source.zip
```

The Linux packaging script uses `requirements-build.txt` and is intended for a
separate build environment. It creates a local, unsigned Linux bundle, not a
Windows EXE. Generated bundles and installers stay outside version control.

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
