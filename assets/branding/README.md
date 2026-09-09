# Anharmonic Studio branding

The [complete logo pack](logo-pack) contains 84 files: 46 PNGs, 32 SVGs, two
ICO files, one ICNS file, a PDF guide, and two text documents. Logo lettering
is outlined; the SVGs contain editable paths and require no installed fonts.

- [Visual overview](logo-pack/05-Brand-Guide/Anharmonic-Studio-Logo-Overview.png)
- [Four-page brand guide](logo-pack/05-Brand-Guide/Anharmonic-Studio-Brand-Guide.pdf)
- [Usage and format notes](logo-pack/README.txt)
- [Source and generation notes](logo-pack/06-Source/Design-Notes.txt)

## Application and website mapping

| Placement | Runtime asset | Logo pack master |
| --- | --- | --- |
| Desktop window and Linux launcher | `anharmonic-studios.svg` | `03-App-Icons/anharmonic-app-dark.svg` |
| Native project header | `anharmonic-header.svg` | `01-Logos/anharmonic-horizontal-accent-on-dark.svg` |
| Workspace identity icon | `../studio/interface/identity.svg` | `02-Symbols/anharmonic-symbol-white.svg` |
| Website header and footer | `../../website/assets/wordmark.svg` | `01-Logos/anharmonic-horizontal-white.svg` |
| Website open-source section | `../../website/assets/stacked.svg` | `01-Logos/anharmonic-stacked-accent-on-dark.svg` |
| Website sharing card | `../../website/assets/social.png` | `04-Social/social-share-1600x900.png` |

Runtime header and symbol SVGs use tighter canvases for compact placements;
their paths are the same as the pack masters. The native header replaces the
master's white and blue fills with the current foreground and project accent.
The existing internal app slug, project paths, and session-lock filename remain
stable across the display-name update to “Anharmonic Studio”.

The `anharmonic-studios.*` filenames remain as compatibility entry points for
existing resource consumers. Linux bundles include the artwork under
`_internal/assets`; the installer adds an absolute icon path to its desktop entry.
Windows ICO and macOS ICNS files are supplied as artwork for their respective
packaging tools; they are not application installers.

The website includes SVG and ICO favicons plus a 180 px Apple touch icon.
GitHub Pages continues to use the existing manually triggered workflow.

The complete pack is committed as individual files so changes are reviewable.
Generated ZIPs and application installers remain outside version control.
