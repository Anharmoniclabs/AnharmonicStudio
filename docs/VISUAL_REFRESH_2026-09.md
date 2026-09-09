# Native studio visual refresh — September 7, 2026

This change adds original editable SVG interface assets and connects them to the
existing Qt workstation. It is not a raster mockup, an audio-engine change, or a
claim that the larger DAW roadmap is complete.

- Fourteen vector pictograms cover the eight workspaces, identity, vinyl, and
  drum/bass categories. See [the asset sheet](../assets/studio/interface/catalog.svg).
- Studio navigation and full workspace tabs use matching icons. The selected
  editor has a compact orientation header, with captions that elide on narrow
  windows instead of overflowing.
- Browser sound rows show instrument-category badges where space permits. The
  existing audition hit target, text labels, and illustrated record crates stay.
- Artwork follows light/dark and project accent changes. SVG sources and icon
  rasters are cached with bounded caches; painting makes no repeated file reads.
  Missing optional artwork degrades to text-only controls rather than preventing
  startup. No new dependency, network request, timer, or audio work is introduced.

The SVG files were authored for this project in code, not copied from a commercial
DAW. They are distributed under this repository's GNU GPL v2.0-or-later source
license. Existing artwork and its provenance are unchanged. No fonts are added.

## Validation

Run `QT_QPA_PLATFORM=offscreen python -m pytest -q tests/test_visual_assets.py`
and the existing browser, theme, and workspace tests. Tests exercise real Qt
widgets, editor identity, high-DPI pixels, palette changes, and narrow widths.
Local result: 25 new visual checks and 18 existing theme checks passed (43 total,
exit status 0) with PySide6 6.8.3. Actual Notes and Beats widgets were rendered and
reviewed in dark/light and 1000/1440-pixel layouts. Existing browser/workspace tests
reported 53 passed, but that separate process stalled during shutdown; it is not
counted as a clean completed run. Local Python 3.13 differs from CI's locked 3.12
environment, so full repository CI remains a separate gate. The implementation
does not certify hardware latency or repair the previous background-export stall.

Qt APIs: [QIcon](https://doc.qt.io/qtforpython-6/PySide6/QtGui/QIcon.html),
[QSvgRenderer](https://doc.qt.io/qtforpython-6.8/PySide6/QtSvg/QSvgRenderer.html).
