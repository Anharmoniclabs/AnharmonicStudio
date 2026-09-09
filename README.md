# Anharmonic Studio

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="assets/branding/logo-pack/01-Logos/anharmonic-horizontal-white.svg">
  <img src="assets/branding/logo-pack/01-Logos/anharmonic-horizontal-ink.svg" alt="Anharmonic Studio" width="480">
</picture>

A native desktop music studio for sampling, beatmaking, synthesis, arranging,
vocal production, and mixing. Import a sound, cut it into playable slices,
build a rhythm, write notes, arrange a song, and export a stereo WAV.

[Explore the studio and watch the film](https://anharmoniclabs.github.io/AnharmonicStudio/)

## Get Anharmonic Studio

Official **Linux, Windows, Intel Mac, and Apple Silicon Mac downloads** fund development.
Official released binaries are the supported distribution for musicians: download,
install, and open the studio. They bundle the runtime and dependencies, with no compiler
setup or subscription requirement to keep using your installed version.

The current packages are release candidates. Payment and private download delivery are
not open yet; signing/notarization and hardware acceptance remain before launch.

| Planned option | Price (USD) | Includes |
| --- | --- | --- |
| Source code | **Free** | Full source; build it yourself without an account or purchase |
| Official download | **$1 minimum**, pay what you can | Current major version and its updates, on all supported platforms |
| Supporter download | **$45 or more**, one time | Current and next major version and their updates |
| Monthly support | **$1 / $4 / $10 / $50** | Official releases while subscribed; keep using downloaded versions afterward |
| Optional donation | **Any amount** | Development support only; no download entitlement |

These are planned launch prices, not live offers. See [downloads and pricing on the
website](https://anharmoniclabs.github.io/AnharmonicStudio/#download) and the
[distribution plan](DISTRIBUTION.md). Paid installers stay outside this public repository
and Pages site. Source access stays free; recipients retain their GPL rights.

## What is inside

- **Sampler:** import audio, trim ranges, detect transients, and map slices to four 4×4 pad banks.
- **Beats:** program step patterns and control per-hit velocity.
- **Instruments:** explore factory sounds, shape analog synth patches, and record arpeggiator notes.
- **Notes:** write synth parts or play samples chromatically in the piano roll.
- **Song:** arrange patterns and audio clips, create variations, and automate levels and pan.
- **Mix:** balance tracks with EQ, saturation, compression, delay, and reverb.
- **Vocals:** record directly into Song, open recorded clips in Autotune, build comps, and render pitch correction to a new take.
- **Export:** render the arrangement to a 48 kHz stereo WAV.

The optional stem-separation engine requires extra dependencies and an initial
model download. The core studio works locally after installation.

## Free source for developers

The full application source is free under **GPL-2.0-or-later**, including the scripts
used to compile and install it. No purchase is required to study, build, modify, or
share it under that license.

Source builds are intended for developers who maintain their own toolchain,
dependencies, and audio configuration. **We do not provide installation or configuration
troubleshooting for self-compiled copies.** A source checkout does not include a signed
official installer or managed update channel. Reproducible application bug reports and
patches are welcome.

- [Developer build guide](BUILDING.md): prerequisites, native compilation, validation,
  and packaging for Linux, Windows, Apple Silicon Mac, and Intel Mac.
- [Support scope](SUPPORT.md): official builds and self-compiled copies.
- [Contributing](CONTRIBUTING.md): development workflow and bug reports.
- [Native packaging](packaging/RELEASE.md): release checks and corresponding source.

Linux developers can continue using `run.sh` after provisioning their environment;
its usage is documented in the developer guide. All build and installation tools
remain in this repository.

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
- `website/`: the public landing page, pricing, and one-minute commercial.
- `packaging/`: native release instructions and the public encryption certificate.
- [BUILDING.md](BUILDING.md): platform-specific developer build instructions.
- [CONTRIBUTING.md](CONTRIBUTING.md): source development and contribution guide.
- [SUPPORT.md](SUPPORT.md): support scope for official and self-compiled builds.
- [DISTRIBUTION.md](DISTRIBUTION.md): free-source and paid-download model.

Personal libraries, songs, recordings, exports, development docs, agent automation,
credentials, caches, and compiled builds are excluded. This repository starts
with clean source history; it does not import the former development repository's history.

The orchestral recordings are CC0, with their dedication retained alongside the
assets. Other dependencies and media keep their own licenses. See [LICENSE](LICENSE)
and [THIRD_PARTY.md](THIRD_PARTY.md).


## Release candidates

Native 0.1.0-rc.1 candidates passed 227 regression tests per target, frozen-app checks,
and platform packaging checks. The [validated native build run](https://github.com/Anharmoniclabs/AnharmonicStudio/actions/runs/34325465683)
covers Linux, Windows, Intel Mac, and Apple Silicon Mac. Signing/notarization and
physical audio-interface acceptance remain before commercial release.

Release CI uploads encrypted candidates, never plaintext paid installers. See
[the build guide](packaging/RELEASE.md) and [the distribution plan](DISTRIBUTION.md).
