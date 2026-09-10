# Unsigned release acceptance

Use the exact candidate installer on each supported target: Windows x86_64,
Linux x86_64, Intel Mac, and Apple Silicon Mac. This checklist records work still
to perform; it is not a passed report. Signing and notarization are not required.

Record the reviewer, date, OS version, CPU, audio interface/driver, sample rate,
buffer size, candidate version, source commit, and installer SHA-256 with each
result. Preserve failures as well as successes. Use disposable audio and back up
existing projects; do not change global security or audio settings to force a pass.

## Installation and update

- Compare the downloaded file with the official SHA-256 checksum.
- Follow [INSTALLATION.txt](INSTALLATION.txt) on a clean, supported user account.
  Record any publisher warning and whether app-specific approval is available.
  An organization-policy block is a compatibility limitation, not permission to
  disable security. Malware or damaged-file alerts require investigation.
- Confirm the installed app opens without Python, a compiler, or the source tree.
- Confirm existing projects and media are unchanged after installation/update.
- Verify launch integration and removal; uninstall must not remove user songs.

## Real audio and project workflows

- At low monitoring volume, test the intended input/output device, device switching,
  playback, Stop, metronome, song/pattern looping, and a sustained mixed session.
  Record observed glitches and the configured rate/buffer; offline CI is not a
  physical-device test or a measurement of round-trip latency.
- Import disposable mono/stereo files. Test pad banks, sample trim/reverse/loop,
  piano notes, arrangement edits, channel routing, mute/solo, and enabled effects.
- Record disposable microphone/interface audio; test permission denial, recovery,
  selected track placement, and playback of the saved take.
- Save, reopen the test DAW, and export. Compare the audible content,
  arrangement length, and media references.
- Test the supported MIDI devices and any trusted third-party plugins intended for
  this release. Record untested devices/plugins explicitly instead of marking them
  passed. Keep third-party processing isolated as designed.

## Browser and distribution

- Exercise the target browsers with imported audio and a real, permissioned input;
  automated Chromium fixtures do not cover every browser or microphone driver.
- Check that unsupported native processing is rejected or disclosed. The standalone
  browser is not a bit-identical port of desktop DSP or a native plugin host.
- Verify the exact matching public source archive and third-party source/notices.
- Stage private downloads without changing the live catalog. Verify all four
  downloaded installer hashes, source links, installation guidance, paid-access
  checks, and denial of unpaid/expired access using the existing delivery tests.
- Preserve the previous immutable release for rollback. Promote only the reviewed
  candidate set; do not mix commits or platform versions.

## Seal the evidence

Keep nonempty platform-specific reports. Only after real acceptance may the
release owner set `physical_audio_tested: true`, create `release-acceptance.json`,
and regenerate `SHA256SUMS` covering the final installer, build manifest, reports,
and installation guidance. The required evidence schema is documented in
[prepare_catalog.py](../delivery/prepare_catalog.py).

Run catalog preparation with `--production`; the default mode prepares candidates
and does not certify acceptance. Neither mode uploads files or changes Cloudflare.
Never fill an unperformed check with a passing value merely to satisfy the gate.
