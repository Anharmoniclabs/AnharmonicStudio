# Anharmonic Trap Foundry

64 original synthesized trap one-shots. Finished WAVs are stereo or mono,
48 kHz / 24-bit PCM, with faded boundaries and peak headroom. No resampled
recordings, artist samples, or third-party drum packs are used.

12 tuned 808s, 8 kicks, 8 snares, 6 claps, 10 closed hats, 6 open hats,
10 percussion hits and 4 effects. The 808 filenames identify C1 (MIDI 24)
or C2 (MIDI 36). Mono bass and kicks retain low-end phase coherence;
stereo claps, open hats and effects use a quiet side layer.

Open Studio's Browser → All Sounds or Drums. Search `AH_` or a sound name,
preview, then drag to a pad. The Trap Foundry kit in Song / beat tools loads
eight matching drum voices and an editable groove. The 808s are available
separately so you can tune them to the song. New pads and Notes instruments use
their root note from the manifest automatically. Replacing an existing sound
preserves that pad's manually configured root note.

Rebuild with `.venv/bin/python scripts/build_trap_foundry.py`. The manifest
records every filename, root note, format, peak level and SHA-256 checksum.
Nonlinear voices render at 96 kHz and are low-pass filtered before conversion.

## License

These original generated sound assets are dedicated to the public domain under
CC0 1.0 Universal: https://creativecommons.org/publicdomain/zero/1.0/ .
You may use, modify and distribute them, including in commercial music, without
attribution. The synthesis script remains under the repository's GPL license.
