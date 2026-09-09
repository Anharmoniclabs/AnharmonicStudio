# Pads and Song track controls

The owner requested a pads-only sidebar, native integration of the old Track
tab, more top metering, and the supplied Downloads logo.

The sidebar now contains the bank buttons, pads and pad parameters. New sessions
show it by default; saved visibility choices are respected. Selecting a Song
track or adding/arming a recording track opens an 80-pixel control strip above
the timeline, without changing the selected pad or taking over its sidebar.
The Song toolbar's Track controls button collapses this strip. Input settings
expand it to expose input setup, count-in, gain and monitoring. Existing capture,
routing, undo, take recovery and Autotune actions retain their original handlers.

The fixed top meter area reads existing engine stereo RMS, output sample peak
and callback load, plus the active Song/Autotune recorder input peak. Inactive
inputs show idle, rather than retaining the last take's level. The output peak
holds until clicked or reset with Enter; Space retains global play/pause.
These are dBFS sample/RMS meters, not true-peak or loudness measurements. No
audio callback processing or device configuration changed.

The supplied A mark is used unchanged in the header, window icon and workspace
identity artwork. Source provenance is in `assets/branding/OWNER_ASSETS.md`.

Verification: 70 initial related tests passed; 85 subsequent focused tests
passed, including recording, pad selection, expandable settings, telemetry,
theme, editor ownership, keyboard transport and audio controls. Ruff checks and
diff whitespace checks passed. Real Qt previews use isolated settings,
temporary synthetic media, no audio devices and the offscreen platform.

Previews: `previews/pads-workspace/{song,input-settings,narrow,light}.png`.
The preview meter levels are explicitly supplied demonstration telemetry.
The active DAW was not restarted; source changes appear on its next launch.
