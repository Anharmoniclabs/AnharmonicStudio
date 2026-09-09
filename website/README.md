# Anharmonic Studio website

A static, responsive GitHub Pages site with the app tour, original commercial,
free-source links, planned paid-download pricing, and optional support options.
All artwork, screenshots, video, and captions are local. No analytics, external fonts,
payment collection, or JavaScript framework is used.

## Preview and validate

```sh
python scripts/check_website.py
python -m http.server 8765 --bind 127.0.0.1 --directory website
```

Open http://127.0.0.1:8765. Core content, prices, source links, FAQs, and video controls
also work without JavaScript. JavaScript adds workspace tabs, platform details,
video controls, and the release-information dialog.

`check_website.py` checks local references, anchors, duplicate IDs, required content,
and the deployment file allowlist. It prevents installers and private key files from
being included in the Pages directory. It does not implement or audit payment security.

Before shipping changes, check desktop and mobile widths, keyboard navigation,
all four platform options, and both closed and configured checkout states in a browser.
Keep generated screenshots and local browser tooling outside the public repository.

## Paid downloads and donations

The full source is free under GPL-2.0-or-later. Planned official download prices and
update access are defined in [DISTRIBUTION.md](../DISTRIBUTION.md):

- $1 minimum, pay what you can: current major version and its updates.
- $45 or more: current and next major version and their updates.
- $1, $4, $10, or $50 monthly: official releases while subscribed.
- Optional donation of any amount: development support only, with no download access.

All paid download plans include Linux, Windows, Intel Mac, and Apple Silicon Mac.
Installed versions keep working after monthly support ends.

`config.js` starts with `salesOpen: false`, `donationsOpen: false`, and no hosted URLs.
Every unavailable offer opens an information dialog or links to the visible release
status without JavaScript. No checkout is simulated, and no payments are taken.

To open an offer, configure its real HTTPS hosted checkout URL and explicitly enable
sales or donations. Each offer is checked independently. An invalid or missing URL
stays closed even if other offers are enabled. URLs containing credentials or using
non-HTTPS schemes are rejected. No price, payment status, or download entitlement is
trusted from browser state. The provider must enforce those on its server.

Keep installers in private delivery storage, not in `website/`, GitHub Pages, or public
Releases. Never place secrets in this config. See the [delivery integration steps](../DISTRIBUTION.md#connect-checkout-and-private-delivery).

## GitHub Pages

[Publish studio website](../.github/workflows/studio-pages.yml) validates the site and
publishes `website/` when site-related changes reach `main`. Manual dispatch remains
available on the default branch. Pages uses GitHub Actions as its source.

Public URL: https://anharmoniclabs.github.io/AnharmonicStudio/

The outlined logo pack is shared with the desktop app. Asset paths stay relative so the
site works below the repository path. If the public URL changes, update the absolute
social image URL in `index.html`. Increment the stylesheet/script query versions when
shipping changes that should invalidate cached assets.
