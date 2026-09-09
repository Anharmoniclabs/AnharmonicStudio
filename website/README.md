# Anharmonic Studios website

A responsive, self-contained landing page for GitHub Pages. It includes the completed one-minute commercial, six actual app screenshots, a feature explainer, an open-source section, download options, and a native FAQ. No build step, external fonts, analytics, or JavaScript framework is required.

## Preview

From the repository root:

```bash
python3 -m http.server 8765 --bind 127.0.0.1 --directory website
```

Visit http://127.0.0.1:8765. Opening `index.html` also works for the core page; use the local server for video captions and full browser testing.

## EXE pack and checkout

The page says the source code is free and open under GPL-2.0-or-later. The official Windows EXE pack is coming soon; no price is announced.

No payment destination was supplied, so the page deliberately shows **Coming soon**. It does not collect payment details or pretend to take orders. Source links work immediately.

When sales are ready, set the real HTTPS payment/product URL in `config.js`. The page then changes the pack button, availability badge, status copy, and availability FAQ to match. Use a provider that handles checkout and private delivery of the EXE. Keep paid installers outside the public site and repository. Client-side JavaScript is not a payment or download gate.

## Publish to GitHub Pages

The repository includes `.github/workflows/studio-pages.yml`. It is manually triggered so a change to the app does not automatically replace the published website.

1. Commit the `website/` directory and the new workflow to the repository's default branch.
2. In repository **Settings → Pages**, choose **GitHub Actions** as the source.
3. Run **Publish studio website** from the Actions tab on the default branch.

The expected project URL is `https://anharmoniclabs.github.io/AnharmonicStudio/`. If you change the repository or use a custom domain, update `og:image` in `index.html` to the published absolute URL.

The workflow follows GitHub's [custom Pages workflow documentation](https://docs.github.com/en/pages/getting-started-with-github-pages/using-custom-workflows-with-github-pages) and publishes only `website/`. Website changes do not automatically publish.

## Files

- `index.html`: page structure and copy.
- `styles.css`: responsive desktop, tablet, and mobile styling, including reduced-motion support.
- `app.js`: video overlay, accessible feature tabs, and the pack information dialog.
- `config.js`: optional hosted checkout destination.
- `assets/`: local video, caption track, SVG signature marks, screenshots, and social image.

The local headless browser test harness and captured validation reports are kept
outside the public repository. Desktop/mobile layout, video playback, captions,
workspace tabs, keyboard navigation, EXE pack controls, and local asset links were checked.

The included video and screenshots are from the isolated “Neon Current” demonstration session. The instrumental commercial uses an original soundtrack; its captions reproduce the on-screen feature copy.

The website uses the Anharmonic Studio waveform signature with ink (`#111315`), paper (`#F6F3ED`), white, and signal blue (`#427BFF`). The logo assets are scalable SVGs; `assets/social.svg` is the editable source for the PNG sharing card.
