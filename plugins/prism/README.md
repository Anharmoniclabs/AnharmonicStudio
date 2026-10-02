# Anharmonic Prism 1.1

A layered sound workstation built on Studio's native oscillator, envelope and
resonant-filter renderer. Two independent synth layers have 32 voices each;
76 automatable parameters shape 54 factory tones and 120 layered performances.
Both Studio and the separate VST3 have dedicated graphical editors.

## Expanded sound banks

174 sounds are built in. The 96-sound expansion adds twelve presets to each of
eight category banks: Orchestra - Strings, Brass, Winds and Mallets; Cinema -
Hits; Atmosphere - Pads; Motion - Arps; and Laboratory - Textures. These are
original synthesized orchestral interpretations, not recorded acoustic samples.
Choose a bank in the category filter, then load a sound. Arp presets enable their
clocked pattern automatically; hold a chord and adjust project tempo. Hits have
zero sustain and decay naturally. Pads benefit from held chords and their release
tails. Every sound can be edited and saved with its two layers, effects and motion.

## Shape and perform

- **Perform:** blend layers; use Tone, Motion, Space and Texture macros; drag the
  morph pad; draw eight modulation steps. Drawing a step enables modulation if depth was zero.
- **Layer A / B:** independent oscillators, pulse shaping, sub/noise, detune,
  drive, stereo width, ADSR envelopes and resonant filters. Drag the filter pad
  and envelope handles. Load different factory tones into each layer.
- **Motion:** eight arp recipes, chord order, gate and octave controls; step and
  LFO modulation route to cutoff, detune, level, pan or oscillator blend.
- **Effects:** chorus, tempo-synced ping-pong echo, reverb, bit crusher and tremolo.
- **Sound explorer:** search and category filters, starred sounds, layer loading,
  full-performance loading, mutation (native editor), undo and A/B comparison.

In the native VST3, double-click a browser sound or press Enter to load the full
sound, or select one and use Load to A / B. In Studio use Load sound, Load to
layer A / B or Enter. Turn or vertically drag knobs; double-click resets them.
Studio also supports Shift for fine knob movement. The waveform is actual plugin
output. Sound files and DAW state recall the layers, effects, macros and motion.
Legacy tones load with the new features at neutral defaults.

Arp, step motion and echo use DAW tempo. Studio sends project tempo through its
isolated host for live playback and export; the standalone defaults to 120 BPM.
Modulation runs in bounded DSP chunks (up to 64 samples); it is not a per-sample
modulation matrix. Modulation phase follows the instrument clock, not DAW PPQ.
Turning arp on/off releases existing voices. MIDI sustain and ±2 semitone bend
are supported. This release uses original oscillator synthesis and presets;
commercial sample libraries are not included.

## Web studio

The installable web studio runs Prism without the VST3: its engine, arp, motion,
macros and effects are ported to an AudioWorklet (`website/app/instrument-dsp.js`),
and tests require the voice engine to match `Engine.h` sample for sample. Insert
Prism in the web Instruments workspace; projects keep the desktop parameter format.

## Studio and piano roll

In **Instruments**, choose **Prism** and **Insert instrument** for the current
pattern. Native and Prism occupy separate instances; choosing one does not
replace the other. The pattern remembers its selected instrument. Live keys play
that selection only. Enable **Layer current notes** before insertion to copy the
selected instrument's notes into the new sound for stacked playback. Duplicating
a pattern creates independent instrument copies. Live knob gestures update the existing VST3 instance
without reloading; each Studio drag is one undo step. Save/Open `.prism.json`
exchanges the full Prism sound with the separate plugin.

Notes has a 1–256-bar length control for each pattern, New, Extend ×2 and Generate.
Generation creates a separate editable chords, arp, bass or melody pattern using
root, scale, rhythm and variation settings. Existing patterns stay intact. Length
cannot be shortened across existing notes or steps. A shared beat/note pattern
has one duration; create a new note pattern to keep its length separate.

Piano grid shortcuts: Alt+1/2/3/4 select/draw/paint/erase; Ctrl+A select all channel
notes; Ctrl+X/C/V cut/copy/paste; Ctrl+D duplicate (extending the pattern when
needed); Ctrl+Q quantize; Ctrl+L legato; Delete erase; Esc deselect; arrows move;
Shift+arrows move a beat/octave; Ctrl+wheel zoom. Click the ruler to place the
paste cursor; Home/End move it to the start/last note end. Ctrl+Z and Ctrl+Shift+Z
undo/redo. The F1 shortcut sheet lists the bindings.

## Optional Hand FX camera

Studio's Hand FX uses an optional local tracking process. Source developers can
install it using the [camera setup guide](../../BUILDING.md#optional-prism-hand-fx-camera-support).
Start camera explicitly; choose a video capture device, not a metadata-only
node. The preview stays local and is not recorded or uploaded. This source-only
optional setup is not bundled into the current paid installers or standalone
plugin. Closing the panel stops capture; recorded effect moves remain in the song.

## Install the separate plugin

Copy `VST3/Anharmonic Prism.vst3` to your host's plugin folder and rescan:

- Linux: `~/.vst3/`
- Windows: `C:\Program Files\Common Files\VST3\` or the host's configured folder.
- macOS: `~/Library/Audio/Plug-Ins/VST3/`; the macOS pack also builds AU for Logic
  in `~/Library/Audio/Plug-Ins/Components/`.

Use the pack for the target operating system. AAX is not included.

## Build and source

`python scripts/build_prism.py /new/output/Prism-platform --jobs 3` downloads and verifies JUCE 7.0.12, builds with CMake, runs the native tests, stages the bundled plugin, and produces the separate pack. Python 3.12+, CMake 3.22+, and a C++17 compiler are required. Linux also needs ALSA, FreeType, Fontconfig, X11 development packages and their graphics dependencies. WebKit is disabled. macOS builds require Xcode; Windows builds require Visual Studio C++ tools. See the dedicated CI workflow.

The pack contains the corresponding project source and pinned JUCE source archive. The plugin is distributed under GPLv3, consistent with the source engine; framework and embedded SDK notices remain in their source files. The full source is open; official compiled plugin packs follow Studio’s paid-build
distribution policy. CI uploads only encrypted binary packs. Matching source and
notices remain publicly available without payment, and recipients retain GPL rights. JUCE source: https://github.com/juce-framework/JUCE/tree/7.0.12. VST is a trademark of Steinberg Media Technologies GmbH.

Linux packages inherit the build machine's glibc and graphics requirements. Cross-platform targets and CI configuration are not evidence of successful builds, signing, notarization, or testing in every DAW; use the accompanying validation report for tested platforms and hosts. Keep both source archives with public distribution of this pack.

### Movable musical typing

Ctrl+T opens a movable, modeless piano. Drag its “Musical Typing” header to place
it beside Prism. While it is visible, note keys remain active over Studio knobs
and controls; text fields and Ctrl/Alt/Meta shortcuts keep normal editing.
Leaving Studio or hiding the keyboard releases held notes.

White notes use `Z X C V B N M , . /`; lower black notes use
`A S D F G H J K L ; '`. Higher white notes use `Q W E R T Y U I O P [ ]`;
higher black notes use `1 2 3 4 5 6 7 8 9 0`. Upper rows start one octave above
the lower rows. The piano shows all three octaves and labels overlapping keys.
Page Up / Page Down or the OCT buttons shift the base octave.

Prism’s Studio editor now uses a stable blue palette. Presets, automation, knobs
and Hand FX change the sound without changing the panel colors or rebuilding
its stylesheet during performance.
