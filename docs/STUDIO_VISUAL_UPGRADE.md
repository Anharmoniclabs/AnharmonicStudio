# The Crates — studio visual upgrade

The built-in image model generated the [studio concept](design/studio-concept.png)
and [transparent vinyl-crate atlas](../assets/studio/vinyl-crates.png). The exact
prompts and provenance are saved in [PROMPTS.md](../assets/studio/PROMPTS.md).
Both images live in the project; their original generated files were retained.

The concept guided a working Qt implementation. The screenshot below is the
actual application, rendered offscreen with a disposable demo library. It is
separate from the image model's concept and does not alter a user's library.

![Implemented Studio workspace](previews/studio-crates.png)

## Working interface

- **Studio / Ctrl+9** opens the Playlist above a docked Channel Rack or Mix
  Console. It uses the same editors as the focused Steps, Arrange and Mixer
  tabs, retaining selection, zoom, edits and undo. No duplicate editor timers
  or audio engine are created.
- **The Crates** has clickable milk crates filled with vinyl records: Drums,
  Melodic, Vinyl and Vocals. Compact 52px cards leave room for a collapsible
  instrument → original pack folder or local source → sound hierarchy.
  Counts, names and durations come from the real library.
- Grouping uses sample names, pack folders and stem tags. Drum-machine names
  such as `E808_BD-01` map to Kicks. Unknown material remains available under
  Vinyl / Unsorted samples. “Vinyl” is a collection view, not a source-provenance
  claim. No files are moved or renamed by categorization.
- Search combines with the selected crate, source filter and existing pack
  folder filter, tucked into the Filters drawer. All Sounds returns to the full collection within
  the current search and filters. Importing a sound
  reveals it even if the previous search would hide it.
- Click a row's play icon, double-click, or press Enter to audition. Dragging
  retains the original clip ID for pads and Playlist. Folders cannot audition
  or drag as samples. Search opens matching folders and restores the previous
  expansion state when cleared. Numbered samples and tempo folders sort naturally.
- Graphite surfaces, amber defaults, distinct track hues, machined switches,
  native transport icons and metallic fader caps carry through the workstation.
  Existing project accent choices remain supported.
- Crate art is decoded/downsampled once and cached on the UI side. Smaller
  windows retain compact cards and scroll the console inside its dock. Stem
  separation remains available in its expandable drawer.

More actual screenshots: [console](previews/studio-console.png),
[piano roll](previews/studio-piano.png), [narrow window](previews/studio-narrow.png),
[light theme](previews/studio-light.png).

Recreate previews with `.venv/bin/python scripts/render_studio_preview.py`.
The script uses Qt's offscreen platform, a temporary library and disabled audio
startup. It never displays a desktop window or changes the live project.

The redesign loads on the next normal DAW launch. No running DAW or active chat
window was closed or restarted during this work.

## Real-library organization check

Read-only inspection found 426 sounds: 48 local recordings/kit sounds/stems and
378 linked MusicRadar 808 samples. All 378 pack sounds now land in drum categories:
29 kicks, 20 snares, 11 rimshots, 15 claps, 25 hi-hats, 16 cymbals, 36 toms,
67 percussion hits and 159 loops. Loop tempo folders remain visible. Authoritative
stem tags take precedence over song-title words; Trap Kit 808 bass sounds land
in Bass. Repeated recording names display distinct sample IDs without renaming files.

The browser list occupies 588 of 850 vertical pixels with filters and stems
collapsed. [Actual-library browser preview](previews/browser-organized.png).
Reproduce this read-only check using
`.venv/bin/python scripts/render_browser_preview.py`.

Validation after browser organization: **358 tests passed, 3 hardware-only tests skipped**. Crate selection,
instrument grouping, search composition, icon/keyboard audition, imported-sound
visibility, alpha assets, editor reuse and the expandable stem drawer are
covered, along with real 808 percussion naming, loop classification, folder
expansion, natural ordering, duplicate identity and sound-only drag routing.
Lint, formatting, syntax, native build and callback quality checks
passed. The 512-frame piano/mixed smoke cases had no DSP deadline misses.
