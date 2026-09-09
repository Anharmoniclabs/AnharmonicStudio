# Crates and phrase sampling

Owner direction: preserve the detailed record crates, continually improve responsiveness and precise editing, and learn from actual FL Studio trap/house producers. Prioritize sampling a chosen musical phrase and arranging it in the existing song.

## Evidence used

- Navie D, [Trap Drum Patterns Tutorial](https://sozai.app/transcript/trap-drum-patterns-tutorial-fl-studio/): read the accessible transcript, especially 01:39–02:07 (extend the framework to the sample's four-bar length) and 03:34–03:59 (perform kicks with snap disabled, then correct unwanted timing). This is a third-party transcription of the producer's speech, not a verified verbatim transcript. Application: create editable patterns and preserve existing music rather than quantize the whole project.
- The Producer School / Yanick, [Making An Afro House Track in 1 HOUR](https://www.youtube.com/watch?v=Yl_ZB2mYa34): the [available transcript excerpt](https://glasp.co/youtube/Yl_ZB2mYa34) introduces drums, percussion, kick/bass, arrangement, selection and vocals. Only the opening transcript was accessible; the full transcript link failed. No claim of reviewing the full hour.
- The Producer School / Niek, [Prospa production breakdown](https://theproducerschool.com/blogs/featured-blogs/how-to-make-house-music-like-prospa-complete-production-breakdown): the creator's written companion describes separate vocal phrases for pre-drop, hook and hits, plus phrase-ending bass variation. Application: future alternate phrase patterns and vocal-range labels. This is a creator-authored article, not a full video transcript.
- Image-Line's [Slicex manual](https://www.image-line.com/fl-studio-learning/fl-studio-online-manual/html/plugins/Slicex.htm), “Manual tempo setting using a selection” and “Manual grid/tempo alignment using downbeat”: use a known beat count and a musician-chosen anchor. Application: explicitly declare the exact range to be four bars; optionally select 16 beats from a precise start using the source BPM. The app's implementation is original.

## Implemented behavior

The retained `assets/studio/vinyl-crates.png` atlas is painted again on the four browser category buttons. CRATES collapses the artwork to compact rows; selection, keyboard navigation and filtering remain available. Decoding stays cached outside repaint.

In Chop (F2), choose a phrase start, enter source BPM and press **Select 4 bars**, or manually select a known four-bar passage. Preview/refine it, choose **4 / 8 / 16 chops**, then press **Chop range → 4-bar song clip**. It treats the selected passage as four 4/4 bars, divides it on shared sample-frame boundaries, finds unused pads in one bank, builds a new four-bar pattern and places it on the selected clip's lane after its last clip, aligned to a bar. Without a selected clip it uses an empty/new lane. Existing pads and existing pattern events are protected, including events referencing empty pads. Full banks or invalid selections do not create history or partially change music. One undo restores the entire action.

Each new chop has a persisted musical duration. Its playback follows project tempo through repitching, in both callback and export. The pad inspector's TEMPO control can disable sync or change beat length. Speed changes also change pitch, like a record; this is **not pitch-preserving stretching**. Pad transposition is additional to tempo repitch. Small attack/release envelopes soften cut edges. Tempo changes apply on the next hit, including changes after mapping; this does not provide continuous warping of a sounding voice.

Auto-chop now searches for repeating four-bar candidates first, retaining shorter loops as fallback. Two repeated phrases suffice. The detected downbeat remains an estimate: audition and correct the selection. Stereo-to-mono scan preparation now happens in the existing worker, and results cannot auto-map a newly selected sample or a replacement project.

## Next useful work

1. Pitch-preserving offline phrase stretching with cancellation, prepared audio, and transient/tail evidence.
2. Alternate A/B sample flips and house pre-drop/hook variants, generated into separate editable patterns with controllable density and no replacement of existing notes.
3. Beat/downbeat correction and half/double-time controls that preview before mapping; preserve expressive swing and live-recorded drift.
4. Profile sample loading and waveform preparation on long synthetic songs. Improve measured UI stalls without moving disk access into the audio callback.
5. Obtain further accessible producer transcripts with speaker/video identity and exact timestamps; distinguish transcript passages from summaries and creator-authored companion articles.

Validation and actual UI renders are recorded in `docs/agent/evidence/2026-09-05-four-bar-sampling/`. Synthetic audio only; no user media, active DAW, chat, or audio-device configuration was changed.
