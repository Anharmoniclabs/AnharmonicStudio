# Desktop and recovery polish

- Display name: Anharmonic Studios. Existing internal application/settings IDs remain stable.
- The application entry point holds a QLockFile for its lifetime to prevent two new application instances sharing recovery/library state. Stale lock handling is provided by Qt. An instance already running an older build must be closed normally by the user before this safeguard applies to it.
- Deferred and unreadable recovery files are archived instead of being overwritten by later edits. Explicit project startup also archives pending recovery first.
- Autosave and close preserve up to ten previous recovery versions, with corresponding history files, in projects/recovery-versions. Deferred recovery archives remain in projects/recovery-skipped-*.json. Open these JSON files through File > Open project.
- F8 toggles the browser; Shift+F8 toggles pads. View menu provides both actions. Panel sizes and visibility persist across normal closes; arrangement focus restores the previous sidebar visibility.
- The local menu launcher checks Python and FFmpeg, logs startup output, rotates logs above 1 MiB, and displays failures with an Open log action when Zenity is available (notification fallback otherwise).
- Local launcher: ~/.local/bin/anharmonic-studios. Logs: ~/.local/state/anharmonic-studios/launch.log, or the corresponding XDG_STATE_HOME location.
- New code-authored vector branding: assets/branding/anharmonic-studios.svg. High-resolution export: anharmonic-studios-2048.png. Original image-generated concept is retained separately. Installed hicolor icons cover 16–512 pixels and scalable SVG.

This pass does not redesign the audio callback or large-file decoding architecture.
