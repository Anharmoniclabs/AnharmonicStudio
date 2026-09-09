# Native release candidates

The canonical public source repository is
https://github.com/Anharmoniclabs/AnharmonicStudio. Source remains free under
GPL-2.0-or-later; official ready-to-run binaries are intended for paid distribution.
There is no activation or subscription gate in the app. Prices and the
checkout/delivery contract are in [DISTRIBUTION.md](../DISTRIBUTION.md).
The public Pages site offers the standard $1 USD release-candidate download, plus
applicable tax. Stripe confirms payment before Cloudflare grants installer access.

## Build outputs

- Linux x86_64: executable folder in a tar.gz, with a per-user `--install` command.
- Windows x86_64: per-user setup EXE, with Start menu entry and uninstaller.
- macOS: separate Apple Silicon and Intel DMGs containing the native .app bundle.

Build each target on its own OS using Python 3.12, a C compiler (Clang on Windows),
FFmpeg/ffprobe, and Inno Setup 6 on Windows:

```sh
uv sync --locked --group dev
uv pip install -r requirements-build.txt
uv run --no-sync python scripts/build_release.py dist/candidate --version 0.1.0-rc.1
```

The build includes Python, Qt, native C DSP, FFmpeg/ffprobe, artwork and dependency
metadata/notices. It also creates a matching source ZIP and SHA256SUMS. Python,
uv and a C compiler are not needed on the recipient machine. The optional neural
stem-separation dependencies/model downloads remain a separate source installation.
The separate experimental ALSA/LV2 C++ engine is not the app's active audio engine
and is not represented as a Windows/macOS feature.

Qt uses its native OS platform plugin. User songs live outside the app: Linux
XDG data home, Windows LocalAppData, and macOS Library/Application Support.
Windows exports run through a separate console worker with no console window;
the main EXE uses the GUI bootloader. Mac bundles include microphone permission text.

## Verification and limits

A build must load native DSP, render the real UI offscreen, save/reopen a project,
export audible WAV through its frozen worker, resample via bundled FFmpeg, release
its UI/engine, and repeat after relocation. Linux and Windows installers are
exercised in temporary locations. Windows also tests uninstallation. macOS checks
DMG integrity after validating the relocated app. Failed builds are not published
as candidates. Build-info and self-check logs record what actually passed.

Native-runner regression tests additionally exercise DSP/reference parity, audio
quality, persistence, recording, tuning and bounded media storage. These are
software checks, not physical microphone/interface or round-trip latency tests.
Candidates remain unsigned (macOS receives PyInstaller's ad-hoc signature).
Developer ID/notarization, Windows signing, third-party corresponding-source
review and physical-device acceptance are still required before calling the
binaries a fully qualified commercial release. Linux needs compatible system
glibc and graphics/audio drivers; the Ubuntu CI build is the portable baseline,
not a build made on a newer local distribution.

## Protecting paid artifacts in a public source repository

The `encrypted release candidates` workflow builds on four native runners. It
runs on release branches or manual dispatch and has read-only repository access.
Only encrypted candidate archives and test-result XML may be uploaded. No job
uploads plaintext installers or creates a public GitHub Release.

`packaging/release-recipient.pem` contains only the public encryption certificate.
The private key stays on the release owner's machine, outside the repository.
Artifacts use OpenSSL CMS with AES-256; download and decrypt them locally:

```sh
openssl cms -decrypt -binary -inform DER -in linux-x86_64.enc \
  -recip /secure/location/cert.pem -inkey /secure/location/key.pem \
  -out linux-x86_64.tar.gz
```

Keep a secure backup of the private key. Public source users can compile their
own builds; encryption controls access to these official compiled artifacts.
The decrypted directory includes the installer, exact source ZIP, notices,
checksums and validation logs. Publish the matching source/notices packages to a public
GitHub source release. Upload only the compiled installers to private Cloudflare R2;
source access must not require payment. Preserve the source release corresponding to
each installer, even when the default branch changes.

Build-system references: [PyInstaller platform requirements](https://pyinstaller.org/en/stable/operating-mode.html),
[PyInstaller subprocess handling](https://pyinstaller.org/en/stable/common-issues-and-pitfalls.html),
[GitHub native runners](https://docs.github.com/en/actions/reference/runners/github-hosted-runners),
[Inno Setup per-user installation](https://jrsoftware.org/ishelp/topic_setup_privilegesrequired.htm).
