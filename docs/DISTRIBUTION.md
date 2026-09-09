# Anharmonic Studio distribution model

Anharmonic Studio follows a free-source / paid-official-build model inspired by
projects such as Ardour.

## Source code

The complete Anharmonic Studio application source is published in this public
repository under the GNU General Public License, version 2 or (at your option)
any later version (`GPL-2.0-or-later`).

Anyone may clone or download the repository, install the documented dependencies,
build the native helper code, modify the application, and run the resulting build
without paying Anharmonic Labs.

The normal source route remains:

```bash
git clone https://github.com/Anharmoniclabs/AnharmonicStudio.git
cd AnharmonicStudio
./run.sh
```

The project does not require a paid activation key to run a source build.

## Official ready-to-run binaries

Anharmonic Labs may charge for access to official ready-to-run installers and
application bundles for Windows, macOS, and Linux. Payment is for the convenience
and service surrounding the official build: packaging, tested dependency bundles,
code signing where available, release engineering, update access, and support.

Official paid installers are distributed through an Anharmonic Labs download or
checkout service. They are not published as free binary assets in this public
GitHub repository or attached to public GitHub Releases.

The source corresponding to an official GPL build remains available under the GPL.
Payment for an official binary does not remove or reduce the recipient's GPL rights.
In particular, a recipient may redistribute a GPL-covered binary under the GPL's
terms. The commercial model therefore charges for the official distribution
service and convenience, not for exclusive permission to run the program.

## Releases

Public Git tags and source archives may be used for releases and reproducible source
history. Public release automation must not upload the paid official `.exe`, `.msi`,
`.dmg`, `.pkg`, `.app` bundle, or Linux installer as a freely downloadable release
asset.

Build automation for paid official binaries should deliver its output to a private
release channel or paid download backend rather than a public GitHub artifact.

## Third-party software and media

The GPL applies to the original Anharmonic Studio source covered by this repository.
Dependencies, optional model weights, sample packs, recordings, imported media, and
other third-party material retain their own licenses and distribution terms. See
[`THIRD_PARTY.md`](../THIRD_PARTY.md).

## Branding

The GPL grants copyright permissions to the covered source code. It does not grant
permission to falsely represent an unofficial build as an official Anharmonic Labs
release. Third-party builds should be identified as third-party builds rather than
Anharmonic Labs official binaries.


## Reproducible source bundle

Build a local source ZIP from the current checkout, including current source
fixes, using:

```bash
uv run --no-sync python scripts/build_source_bundle.py /tmp/AnharmonicStudio-source.zip
```

The builder prints a SHA-256 checksum, normalizes archive ordering, timestamps
and file modes, and excludes personal `library/`, `projects/`, `exports/`, native
build output, and generated evidence/previews. It fails on source symlinks and
publishes the ZIP by atomic replacement. Repeating the command with identical
source produces identical bytes. This is a source distribution requiring the
documented Linux dependencies; signed standalone application bundles and the
private paid download service remain separate release deliverables.

## Local Linux candidate

```bash
uv sync --locked
uv pip install --python .venv/bin/python -r requirements-build.txt
uv run --no-sync python scripts/build_linux_bundle.py dist/linux-candidate
```

Choose a new output directory for each build. The builder compiles native DSP,
collects runtime code/resources and available dependency notices, then runs the
frozen application's real project/export/FFmpeg/UI check. It installs another
copy into a disposable prefix and repeats the check after relocation. Only
successful candidates become visible at the requested output path. The resulting
tar archive includes a SHA-256 sidecar and Python dependency/build versions.

This is a Linux folder bundle with a per-user installer, not an AppImage or a
signed cross-platform release. It needs the target system's FFmpeg, graphics/audio
drivers and compatible glibc. PyInstaller recommends building on the oldest Linux
system supported; it does not bundle glibc. See
[PyInstaller platform notes](https://pyinstaller.org/en/stable/usage.html).
The local CachyOS build must not be presented as Ubuntu-compatible evidence.

The quality workflow builds and checks an Ubuntu candidate without uploading
binaries. The separate manual `extended release validation` workflow preserves
only software timing/memory measurements. Neither workflow publishes an official
installer. Signing, complete third-party source/notice review and delivery through
the private download service remain release requirements.
