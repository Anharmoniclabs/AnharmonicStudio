# Native release candidates

The canonical public source repository is
https://github.com/Anharmoniclabs/AnharmonicStudio. Source remains free under
GPL-3.0-or-later; official ready-to-run binaries are intended for paid distribution.
There is no activation or subscription gate in the app. Prices and the
checkout/delivery contract are in [DISTRIBUTION.md](../DISTRIBUTION.md).
The public Pages site offers the standard $1 USD release-candidate download, plus
applicable tax. Stripe confirms payment before Cloudflare grants installer access.

## Build outputs

- Linux x86_64: executable folder in a tar.gz, with a per-user `--install` command.
- Windows x86_64: per-user setup EXE, with Start menu entry and uninstaller.
- macOS: separate Apple Silicon and Intel DMGs containing the native .app bundle.

Build each target on its own OS using Python 3.12, a C++17 compiler (Clang on Windows),
FFmpeg/ffprobe, and Inno Setup 6 on Windows:

```sh
uv sync --locked --group dev
uv pip install -r requirements-build.txt
uv run --no-sync python scripts/build_release.py dist/candidate --version 0.1.0-rc.1
```

The build includes Python, Qt, the portable C++ DSP/output core, FFmpeg/ffprobe, artwork and dependency
metadata/notices. It also creates a matching source ZIP and SHA256SUMS. Python,
uv and a C compiler are not needed on the recipient machine. The optional neural
stem-separation dependencies/model downloads remain a separate source installation.
The historical ALSA/LV2 prototype is opt-in and is not the app's active audio
engine. The production C++ core is shared across all four targets; Python retains
the project scheduler, UI and isolated plugin coordination.

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
The Windows PowerShell, Intel Mac, Apple Silicon Mac, and Linux jobs also run
simulated MIDI connection/mapping tests, plugin-process crash/hang tests, and a
ten-minute paced piano/pad/effects session. Memory usage is measured on each OS.
Public test reports include callback timing, memory growth, and platform details;
only encrypted installer artifacts leave the build jobs. Actual third-party plugin
tests remain opt-in and are reported as skipped when no trusted plugin is installed.

Run the matrix from GitHub Actions → **encrypted release candidates** → **Run workflow**,
or `gh workflow run release-candidates.yml --ref main`. This creates fresh native
environments and tests built installers; it does not replace the packages sold through
Cloudflare. Shared-runner timing is a regression check, not measured hardware latency.
The CI limits are p99 callback work within one 512-frame buffer, at most 5% callbacks
over that budget, and at most 64 MiB of sampled memory growth during the session.
The separate extended Linux validation retains stricter performance thresholds.

Official packages are intentionally unsigned under the owner's distribution
policy (macOS receives PyInstaller's ad-hoc signature, not a verified Developer
ID signature or notarization). Paid signing and notarization are not release
requirements. Every candidate includes [INSTALLATION.txt](INSTALLATION.txt), and
the storefront and checkout must disclose the unsigned status before purchase.
Do not disable OS security or promise installation on policy-managed computers.

Functional/software checks, installer and export checks, matching source and
checksums, third-party corresponding-source review, and physical-device
acceptance remain required for production qualification. Unsigned status must
never be presented as security certification. Linux needs compatible system
glibc and graphics/audio drivers; the Ubuntu CI build is the portable baseline,
not a build made on a newer local distribution.

Use [ACCEPTANCE.md](ACCEPTANCE.md) to record platform-specific installation,
physical-device, project, browser, and private-download evidence before promotion.

Release source ZIPs use committed Git blobs and executable modes, with stored ZIP
entries. This avoids checkout line-ending, permission, and compression-library
differences across native runners; all four candidates must have the same source
SHA-256. Development source snapshots can still include local working changes.

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
