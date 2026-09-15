# Anharmonic Prism 1.0

A 32-voice stereo synth built from Anharmonic Studio's native oscillator, envelope and resonant-filter renderer. Includes 54 oscillator sounds (24 new), eight arpeggiator recipes, pulse shaping, sub/noise, stereo spread, filter and pitch LFOs, drive, chorus, tempo-synced ping-pong echo and reverb.

The instrument is included with Studio and also packaged separately. VST3 runs in compatible hosts; the macOS build also produces AU for Logic. This is not an AAX release. Each operating system needs its own binary; a Linux build cannot run in a Windows or macOS DAW.

## Install and play

Copy `VST3/Anharmonic Prism.vst3` to your user plugin folder, then rescan instruments in your DAW:

- Linux: `~/.vst3/`
- Windows: a VST3 folder configured in your host, or `C:\Program Files\Common Files\VST3\` with administrator access.
- macOS: `~/Library/Audio/Plug-Ins/VST3/`; for Logic copy the AU component to `~/Library/Audio/Plug-Ins/Components/`.

Insert Prism on a MIDI instrument track. Select a sound, send notes, and use the three control pages for tone, motion and effects. Double-click a control to reset it. The host can automate all 32 parameters. MIDI pitch bend spans ±2 semitones; sustain and note-off are supported. The arp follows host BPM (120 BPM when no tempo is supplied) and advances at sample boundaries. Its phase starts with the instrument's clock; bar/PPQ alignment is not implemented. Turning arp on/off releases previous notes; play the chord again.

The scope shows actual plugin output. Mutate changes a bounded selection of tone controls. Store A and Recall A compare sounds; Undo restores parameter edits. Host sessions recall the complete parameter state. Save/load `.prism.json` exchanges oscillator tone and arp settings with Studio. Plugin effects are included in plugin sound files; Studio uses mixer-track effects and does not translate the plugin's chorus/reverb/echo implementation. Sample-library instruments are not embedded in Prism.

In Studio's Instruments tab, **Use Prism VST3** loads the bundled plugin into the external-instrument slot. The existing built-in synth remains separately available. Use Devices & Plugins for external instrument routing/controls. To transfer the built-in tone, save a Prism sound and load it in the plugin. Track-effect recipes affect all sources routed to that mixer track and use the project's shared delay/reverb sends.

## Build and source

`python scripts/build_prism.py /new/output/Prism-platform --jobs 3` downloads and verifies JUCE 7.0.12, builds with CMake, runs the native tests, stages the bundled plugin, and produces the separate pack. Python 3.12+, CMake 3.22+, and a C++17 compiler are required. Linux also needs ALSA, FreeType, Fontconfig, X11 development packages and their graphics dependencies. WebKit is disabled. macOS builds require Xcode; Windows builds require Visual Studio C++ tools. See the dedicated CI workflow.

The pack contains the corresponding project source and pinned JUCE source archive. The plugin is distributed under GPLv3, consistent with the source engine; framework and embedded SDK notices remain in their source files. This is an open-source release pack, not a proprietary-licensed plugin. JUCE source: https://github.com/juce-framework/JUCE/tree/7.0.12. VST is a trademark of Steinberg Media Technologies GmbH.

Linux packages inherit the build machine's glibc and graphics requirements. Cross-platform targets and CI configuration are not evidence of successful builds, signing, notarization, or testing in every DAW; use the accompanying validation report for tested platforms and hosts. Keep both source archives with public distribution of this pack.

### Studio sound lab

In Studio, open **Instruments → Launch Prism** or select the **Prism** tab.
The **Native instrument** tab keeps the built-in sound browser and controls.
Switching back to Prism restores its tone within the current project session.

Prism's Studio editor has rotary controls (vertical drag, Shift for fine control,
double-click to reset), waveform selectors, a cutoff/resonance XY pad, a live
output scope, and oscillator, envelope, motion/arp and effects pages. Edits are
sent to the running VST3 instance without reloading or releasing held notes.
Project saves retain the normalized plugin parameters; each knob drag is one
undo gesture. Store A and Swap A/B compare two sounds. The separate VST3 pack
continues to provide its native JUCE editor in other hosts.

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
