# Recorded orchestra and cinematic palette

Owner request: orchestral instruments and a broader, less generic range of sounds,
with Omnisphere as a reference for variety. The implementation adds actual recorded
instruments to Anharmonic's native instrument engine and ten original layered
presets. It does not implement external VST hosting.

## Finding the sounds

Open **Instruments** and select **Orchestral strings**, **Brass & winds**,
**Orchestral mallets**, or **Cinematic hybrids**. These categories appear first.
Search also matches descriptions and musical uses. Click a sound to load it;
Enter or **Preview sound** auditions it. The keyboard, pattern notes, mixer route,
print-to-pad action and project export use the same prepared instrument.

- Strings: chamber violins, solo violin, spiccato and pizzicato violins, sustained
  and pizzicato cellos.
- Brass and winds: sustained and staccato French horn, vibrato and staccato flute,
  oboe.
- Plucked and struck instruments: harp and marimba.
- Hybrids: Nocturne Strings, Cathedral Glass, Ghost Conservatory, Silk Pulse,
  Gilded Horizon, Clockwork Garden, Moonlit Harp, Ember Ostinato,
  Subterranean Cello and Reversed Pearl.

Brightness, attack, release and width shape the recordings. Layer blend changes
the balance of hybrid sources; Movement and Pulse speed add amplitude motion;
Direction reverses recordings for newly played notes. A dragged sampled-instrument
control is one undo step. Presets, customized values and undo history survive
save/reopen. Unsupported analog oscillator controls are hidden for these sources.

Loading uses a worker and keeps the previous instrument available until preparation
succeeds. A later choice or project replacement invalidates stale completion.
Project load preflights required samples before replacing current music. The app
does not download media at runtime. Missing bundled recordings can be restored
explicitly with `.venv/bin/python scripts/install_orchestra.py`.

## Sources and design decisions

[Versilian's official Community Edition page](https://versilian-studios.com/vsco-community/)
provides CC0 orchestral recordings and permits building instruments from them.
The [official source repository](https://github.com/sgossner/VSCO-2-CE) supplies
the recordings and SFZ mappings. This compact bank contains **181 recordings across
13 articulations**, approximately **62 MiB** of lossless FLAC. Each articulation
uses five recorded root pitches, preserving the selected roots' dynamic layers and
alternate takes. Numeric SFZ pitch centers are retained because octave labels in
filenames are not consistent across sources. Attribution, original file hashes,
pinned revision and the complete CC0 dedication accompany the assets.

[Spectrasonics' oscillator documentation](https://support.spectrasonics.net/manual/Omnisphere/edit_page/oscillator/index.html)
describes sample and synthesis sources. Layered source combinations and useful
macro controls are our response to the owner's reference; these patches contain
no Omnisphere content and make no claim of comparable breadth or fidelity.

Prepared recordings are immutable and shared. The callback selects velocity layers
and alternates takes per MIDI note, transposes with interpolation, crossfades
sustain loops, and applies a continuous envelope, causal tone filter, stereo width
and amplitude motion. File decoding and reverse-tail preparation happen outside
the callback. Common instrument gain preserves relative recorded dynamics.

## Evidence and limits

[Audio reel](evidence/2026-09-05-orchestra/orchestral-palette-demo.wav) starts with
the previous Solar Strings and follows with twelve sampled/hybrid examples.
[Cue times](evidence/2026-09-05-orchestra/demo-cues.json) identify each section.
Individual WAVs, dark/light actual Qt renders at 1440×900 and 1000×900, and
`callback-benchmark.json` are in the same folder. Reproduce with
`QT_QPA_PLATFORM=offscreen .venv/bin/python scripts/render_orchestra_preview.py`.
The renderer uses temporary media/settings with engine startup disabled; it does
not open a desktop window or audio stream. The audio was rendered and measured,
not assessed by a listening panel.

Tests cover multisamples, velocity/takes, forward/reverse loop continuity across
arbitrary block boundaries, long sustain/release, audible macro changes, absence
of callback file reads, callback/FLOAT-export agreement, loading races and failures,
undo/redo, persistence, and invalid project fields. Full validation results are
recorded in MEMORY and the evidence logs.

The dedicated software benchmark uses 48 kHz, 512-frame blocks, eight voices and
750 callback blocks per patch. Chamber Violins measured p99 2.87 ms and worst
2.95 ms; Silk Pulse measured p99 3.70 ms and worst 3.93 ms, against a 10.67 ms
block budget, with zero measured missed deadlines. These short shared-host
measurements do not establish hardware latency or performance at all polyphonies.

This is a compact multisample selection with interpolated pitches and generated
sustain loops, not a complete orchestral library. It has no recorded legato
transitions, continuous dynamic crossfade, keyswitch articulation system, tempo
sync for movement, independent simultaneous instrument tracks, or external VST
hosting. Future work should improve expression and author/listen to musical
phrases and sustained loops before expanding the preset count.
