# Release workflow pass — 2026-09-05

The owner asked to use the Anharmonic agent, research producer workflows in FL Studio and Ableton, improve the layout and controls, and remove the social-app appearance of the buttons. This interactive pass used the local `anharmonic-studio` skill and manually integrated its compatible held pitch-control candidate from run `20260905T190300.009765Z`. The controller's schedule and held-run status were not changed.

## Research and interpretation

YouTube search results, video descriptions and chapter listings were available; full video playback was unavailable. These references are not a claim to have watched the videos end to end. Official manuals corroborate the workflow decisions.

| Reference | Accessible evidence | Application here |
|---|---|---|
| Image-Line: [FL Studio Getting Started](https://www.youtube.com/watch?v=ort7ZLY-3zo), [official introduction](https://www.image-line.com/fl-studio-news/getting-started-tutorial) | Introductory beat-making walkthrough; chapters progress through browser, sample placement, steps, swing, playlist, variations and piano roll. | Keep sample discovery near the editor; make sending a pattern to the arrangement an explicit, accessible action. |
| Image-Line: [Basic workflow manual](https://www.image-line.com/fl-studio-learning/fl-studio-online-manual/html/basics_workflow.htm) | Instruments feed patterns; Playlist clips arrange material; mixer routing remains a distinct part of the process. | Preserve pattern identity and routing when navigating; double-click an arrangement pattern to edit its source. |
| Ableton: [Moving between Session and Arrangement Views](https://www.youtube.com/watch?v=OaQpybm5Pcw), [official workflow catalogue](https://www.ableton.com/en/live/learn-live/workflows/), [Session View manual](https://www.ableton.com/en/manual/session-view/) | The official tutorial catalogue covers moving between the two views. The manual describes improvisation with clips/scenes and recording into Arrangement. | Reduce the friction between an editable idea and its arranged form. Anharmonic's implementation is explicit pattern placement, not a new Session View or scene launcher. |
| Audioreakt: [From Session view to Arrangement view](https://www.youtube.com/watch?v=GGQAR3RFyCk) | Producer description and chapter list cover scenes, moving them into arrangement, then polishing an industrial techno track. | Keep the return path into editing fast and preserve the original pattern rather than flattening it. This is supporting process context, not technical API evidence. |

Flat graphite surfaces, restrained amber states, compact category controls and instrument rows are our design choices for this application. The references do not prove that a particular palette or button shape is universally better.

## Implemented behavior

- **Pattern to arrangement:** `Send pattern to Arrange` is available in Beats and Notes. It appends the current pattern after clips on the selected lane, otherwise reuses its pattern lane, a free lane, or a new lane. Forward snapping avoids overlapping an off-grid tail. The result is selected and scrolled into view. Empty patterns explain the next action. Undo/redo preserves pattern timing, velocities, gates and routing. Playback, recording, loop and Pattern/Song mode remain under the musician's control.
- **Return to editing:** double-click a pattern clip to open Notes when it contains notes, otherwise Beats. Editing still changes the shared source pattern; a status message makes that relationship explicit.
- **Find sounds:** Ctrl+F reveals and focuses sample search without replacing the active editor. Instruments use a compact list with composed category/name search, an empty-result message, a persistent patch header and adjacent macros/routing. Down enters results; Enter loads and previews. Preview does not write notes into a pattern. Sound design and the arpeggiator remain available in the expanded editor.
- **Visual hierarchy:** neutral dark/light surfaces, flat small-radius controls, underlined workspace selection, compact sound categories, optional workflow tips and selective amber actions. Keyboard focus, disabled controls, record states, selection and custom-accent text contrast remain explicit.
- **Sample tuning:** direct entry to 0.01 semitone, cent keyboard steps, coarse Page Up/Down adjustment, Reset/Alt+0, Escape cancellation and one undo operation per slider gesture. Retired controls cannot mutate a newly selected pad.

The instrument browser exposes the existing 30 factory instruments; this pass does not add external VST hosting.

## Verification and visual evidence

Behavior regressions cover safe arrangement placement, empty patterns, selection/reveal, undo/redo, editor navigation, sample search focus, instrument filtering without edits, keyboard audition, tuning cancellation and stale controls. A synthetic melodic pattern is saved, reopened and exported as an actual 48 kHz WAV. Tuning callback and export renders agree within 0.02 cent at the measured settings.

`scripts/render_studio_preview.py` uses temporary synthetic media and isolated settings, mocks engine startup, and renders offscreen. It now includes a loaded sliced waveform and the pad inspector. Before images are in `evidence/2026-09-05-release-workflow/`; final dark/light/narrow images are in `../previews/`:

- `studio-crates.png`, `studio-narrow.png`, `studio-light.png`
- `revamp-instruments.png`, `revamp-instruments-narrow.png`, `revamp-instruments-light.png`
- `revamp-sampling.png`, `revamp-sampling-narrow.png`, `revamp-sampling-light.png`
- `revamp-sampling-inspector.png`, `revamp-sampling-inspector-light.png`

Final verification outcomes are recorded in `MEMORY.md`. These software checks do not establish hardware latency, supported external-plugin compatibility or distributable-package readiness. Those remain separate release acceptance tasks.
