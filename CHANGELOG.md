# Changelog

## 2026-10-02 — installable phone studio (web)

The browser studio at `app/studio.html` is now an installable app that works like a
phone music studio. These are web changes; the desktop packages are unaffected.

- Install to the home screen on iPhone, iPad and Android, or as a desktop app. The
  studio opens full screen and keeps working offline; projects stay on the device.
- Phone layout with a bottom tab bar, full-screen pads, a touch keyboard with glide,
  multi-touch chords and a chord strip, a transport sheet and a library sheet. Pinch
  zooms the Song timeline and sampler waveform.
- Real-time recording replaces compressed MediaRecorder capture: uncompressed audio at
  the device rate, input meter, count-in, optional monitoring and latency-compensated
  placement. Arming a Song row and pressing Record counts in and starts the song.
- Performance recording captures pads and keys into the looping pattern, one undo step
  per take.
- Each pattern can own its instrument; 17 grouped oscillator presets.
- Fix an empty pad waveform drawing as a solid block and the Song playhead sitting
  slightly left of the lanes.

Physical iOS and Android devices and real microphones were not tested in CI; the
browser checks use Chromium with a synthetic input stream.

## 2026-09-27 — recording and instrument workflow (next candidate)

These changes are available in source on `main`. The paid download catalog still
serves **0.1.0-rc.1** until a new, separately versioned installer set passes its
build and delivery checks. Source availability does not mean an installed copy
has updated itself.

- Show live PipeWire USB outputs in audio setup, with a refresh action for
  newly connected interfaces. Remove the Agent swarm harness from Studio
  menus and startup.
- Refine auto-chops with stereo-safe quiet boundaries before attacks, quieter
  phrase edges without changing loop length, attack-cleanliness scoring and
  a shortlist that favors distinct hits. Add a reproducible local song/stem
  audition audit with pad exports and engine playback checks.
- Compact the branding header and Song toolbars. Group marker actions into one
  dropdown, automatically collapse empty marker lanes, and keep one Zoom menu.
  Show the recording destination and count-in in the header (Record tooltip on
  smaller windows), following pattern, armed audio and Song performance routing.
- Group the desktop header into File, Edit, View, Transport, Sound, Tools and
  Help. Recording and markers live under Transport; instruments, plugins, routing
  and device settings live under Sound. Commands search replaces the duplicate
  Project dropdown, and Browser/Pads buttons reflect panel visibility.
- Record MIDI keys, typing-keyboard chords and sample pads from Beats, Notes or
  Song. Count-in clicks and a shared recording deadline preserve the first hit
  after count-in without recording the count-in performance.
- Keep hardware pad recording separate from pitched notes. Use combined MIDI
  routing with drum pads on channel 10 and keys on another channel; learn the
  controller's actual pad layout instead of assuming a model's factory mapping.
- Continue playback while switching workspaces. Space and Stop control transport;
  changing the editor does not stop or rewind the song.
- Insert independent Native or Prism instruments into each pattern. Select the
  instrument for live input. “Layer current notes” copies its notes into the new
  instrument; a live chord still plays only the selected instrument. Duplicating
  a pattern copies its instrument instances so patch changes stay independent.
- Record directly into Song, including joining playback. With no armed row,
  recording creates a notes destination; an explicitly armed audio row retains
  audio capture. Turning Record off ends the take while playback continues.
- Undo and redo preserve unchanged sample caches, instrument hosts and settings.
  Note edits avoid whole-project reloads; failed restores retain recoverable
  history. History changes are guarded during recording.
- Recover Prism from transient render backlog instead of leaving it permanently
  silent. Queued MIDI preserves note-off and sustain-release order; actual
  process failures and render timeouts still report errors. Recovery does not
  promise dropout-free audio under arbitrary load.
- Correct shared plugin delay compensation for blocks longer than the delay,
  and allocate effect buffers using the new engine’s sample rate.
- Restore visible sampler slice markers during Undo without reloading audio.
- Keep Prism’s blue editor palette stable across presets, automation and Hand FX.
- Improve sample-to-Notes routing, input ownership and short plugin render chunks.
- Exclude Linux webcam metadata nodes from the Hand FX camera picker. Optional
  camera setup is documented; camera support was verified locally with ten
  processed frames, without saving video. The user also confirmed live preview and hand tracking
  on the HP True Vision camera.

### Validation and limits

Linux desktop work used an MPK Mini Mk II, sample pads and independent Prism
instances. The user confirmed first-note/chord capture and separate pad/key
routing earlier in the session. Final hardware audibility after the Prism
recovery change still needs confirmation. A real Prism process survived three
injected 80 ms stalls, resumed audible output and released to silence. The
plugin/Prism regression group passed 67 tests before release-wide validation.
These results do not certify other controllers, operating systems or physical
round-trip latency. See [current release gates](CURRENT_RELEASE_STATUS.md).

### Distribution

The complete application, native engine, Prism source, dependencies' notices and
build scripts remain open source. Official compiled packages are paid downloads;
CI artifacts containing binaries must be encrypted. Public GitHub releases may
contain matching source and notices, never plaintext paid installers or plugin
packs. Existing GPL rights are unchanged.
