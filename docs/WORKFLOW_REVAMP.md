# Focused beat and sampling workflow

The default Studio workspace now gives one editor the full work area. The primary navigation is Beats, Sample, Instruments, Notes, Arrange, and Mix. More opens vocal recording, automation, or the original workspace tabs. Existing keyboard shortcuts and editor instances are retained.

## Make a beat

Choose Pocket (hip-hop), Circuit (house), or Midnight (trap), then **New beat with sounds**. This creates an editable two-bar pattern using eight free pads. Existing patterns and occupied pads are preserved. The project tempo stays unchanged. Press Play to hear it, click cells to edit hits, and use Undo to remove the new pattern and kit assignment. If no eight-pad group is free, the action reports this without replacing sounds.

The three kits contain 24 original procedural one-shots: kick, snare, closed hat, clap, open hat, tom, rim, and shaker. Samples are generated locally on first use, stored in the library, and reused on subsequent kit loads. No external sound-pack downloads are required. Source: mpclab/factory.py.

Step lanes are taller with larger labels. Pattern creation, duplication, renaming and clearing live in the Pattern menu.

## Sample

The primary path is **Import audio → Preview selection → Send to pad**. Select the destination directly from A1–D16, then send the range or use **Send + next pad**. The waveform retains its trimming, snap, loop, and zoom controls. Auto Chop and sensitivity are directly above the waveform. More expands grid slicing, pad auto-mapping, and four-bar phrase controls in one place. Selecting a browser sample while in Studio opens Sample inside the same workspace.

## Instruments

There are 30 built-in synth presets, including 20 new keys, basses, pads and leads. Category filtering, name completion, visible sound cards, and preview help audition them. Click to load; double-click or Enter previews. Brightness, Soft attack, Release and Width are available immediately. Sound design & arpeggiator expands the full existing controls.

New sounds include Soft Electric Keys, Bright Electric Keys, Felt Synth Keys, Tape Electric, Music Box, Muted Mallet, Rubber Clav, Velvet Organ, Clean Sub, Round Finger Bass, Garage Bass, Reese Motion, Slow Horizon, Warm Cinema, Air Choir, Frozen Glass, Mono Neon, Singing Triangle, Arcade Pulse and Wide Saw.

These are original patches for the existing shared synth, not acoustic piano sample libraries or VST plugins. This change does not add plugin hosting or separate patches per MIDI track. Loading a preset preserves the selected mixer route, stops old synth voices and remains undoable. Preview bypasses note recording.

## Validation

Automated tests cover drum audio bounds and fades, reuse of generated library sounds, valid groove timing, playable output from every new synth patch, preserving occupied pads, factory-beat undo, instrument filtering and preview, workspace reparenting, and existing production behavior. UI previews use a disposable project and the offscreen Qt backend without opening desktop windows or audio devices.
