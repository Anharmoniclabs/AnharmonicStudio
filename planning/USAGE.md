# Capability foundations — development branch

This branch implements the first bounded set of requirements from the owner's
[216-row comparison and 194-work-package backlog](README.md).
It is not a declaration that the complete backlog or release acceptance is done.

## Scalable mixer

In the native Mix workspace, stop playback and finish/save any recording, then
click **+ TRACK**. The command palette also offers **Add mixer track**. New tracks
receive independent persistent IDs. Rename, gain, pan, mute, solo, routing,
automation, plugin-chain addressing and exports use the actual track count.
Add-track supports normal Undo/Redo. The limit is 128 mixer tracks.

In the browser Mix workspace, use **+ Mixer track**. Native project JSON can retain
and route all 128 channels. Browser playback/export honors the supported mixer
controls on high-numbered channels. Native-only processors still produce the
documented compatibility warning; this does not make browser/native DSP identical.

Legacy eight-track projects remain valid. Older application versions that only
support eight tracks cannot open enlarged projects; use this branch or a later
compatible build. Make a backup before moving a project between versions.

This removes fixed-eight-track truncation, but does **not** implement multiple
independent synth/plugin instruments (`01-02`), arbitrary physical input/output
channel layouts, or qualify 128 simultaneous tracks at every buffer size/device.

## Named timeline markers, cues and regions

The native Song workspace has pinned marker/region lanes. Use the **Markers**
menu or command palette to create markers, cue points or named ranges; choose
names and colors, navigate previous/next, edit/delete, or use a region as the
loop range. Dragging and resizing follow the arrangement snap setting.
**Ctrl+Alt+M** adds a marker. Changes participate in project Save/Open and Undo/Redo.

Export produces JSON or spreadsheet-safe CSV metadata with beat positions.
This is not a CD cue sheet, audio render, timecode or tempo-map export.
Limits: 4,096 entries per project and positions up to 100,000 quarter-note beats.
Marker metadata is preserved by the browser model; the new visual editor is native.

## Audio-file analysis

Choose **File → Analyze rendered audio…**, or **Analyze rendered audio** in the
command palette. Select an existing audio file; analysis runs in a cancelable
background job without opening an audio device or changing the source. Save the
completed report as JSON or plain text.

The report includes format, sample rate, channels, duration, sample peaks and
their locations, RMS, DC offset, nonfinite samples, full-scale/overload counts,
consecutive full-scale runs, and stereo correlation/mid-side statistics.
Full-scale samples are a warning signal, not proof that clipping occurred.

**LUFS, loudness range and true peak/dBTP are not measured.** RMS is not perceptual
loudness and stored-sample peaks do not detect inter-sample peaks. This advances
`12-03` and `12-09`, but does not finish the loudness, live-scope or delivery-target
requirements. The report and dialog state these limitations explicitly.

The same analyzer is available without a GUI:

```sh
python -m mpclab.audio_analysis mix.wav --json mix-analysis.json --text mix-analysis.txt
```

## Release policy

Packages remain **unsigned**, with the existing
[platform installation instructions](../packaging/INSTALLATION.txt). No signing
purchase, history rewrite, live-site deployment or Cloudflare binary promotion
is part of this feature change. Windows, Linux and both Mac targets must qualify
from one source commit, with checksums, installation/device evidence and unchanged
performance gates, before a new public download is promoted.
