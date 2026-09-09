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

- $1 USD, one time: current major version and its updates.
- $45 or more: current and next major version and their updates.
- Optional donation of any amount: development support only, with no download access.

All paid download plans include Linux, Windows, Intel Mac, and Apple Silicon Mac.
Installed versions keep working without recurring payments.

`config.js` enables the verified $1 one-time Stripe test Payment Link.
The button and release status explicitly identify test mode. Live installer sales stay closed. Ko-fi donations are open at
https://ko-fi.com/anharmoniclabs and operate independently of Stripe test mode. `deliveryReady` remains false until the private service below has been
deployed and a real Stripe sandbox purchase has downloaded its selected installer.
Every unavailable offer opens an information dialog or links to the visible release
status without JavaScript. Checkout runs on Stripe; the website does not collect
payment details.

To test the standard download, set an active `https://buy.stripe.com/test_…` URL in
`links.download` and enable `testCheckoutOpen`. The site labels the button and release
status as testing, with no real charge. Payment Links do not
use the publishable key; no Stripe secret belongs in the site.

To open live offers later, change `paymentMode` to `live`, configure their real HTTPS
hosted checkout URLs, and explicitly enable sales. Donations use their own `donationsOpen` flag and
remain independent of the installer payment mode. Keep the donation link and status
in `index.html` in sync for visitors without JavaScript. Each offer is checked
independently. An invalid or missing URL stays closed even if other offers are enabled. URLs containing credentials or using
non-HTTPS schemes are rejected. No price, payment status, or download entitlement is
trusted from browser state. The provider must enforce those on its server.

Keep installers in private delivery storage, not in `website/`, GitHub Pages, or public
Releases. Never place secrets in this config. See the [delivery integration steps](../DISTRIBUTION.md#connect-checkout-and-private-delivery).

## Private installer service

`delivery/server.py` runs separately on a Python/container host with HTTPS and persistent
private storage. The storefront sends the selected platform to Stripe using
`client_reference_id=as_v1_windows` (or `mac-arm`, `mac-intel`, `linux`). The service
retrieves the Checkout Session from Stripe, verifies the configured Payment Link and
price, $1 USD subtotal, one-time mode, completed payment, and test/live mode. Taxes
may increase the total. Refunds and disputes block even previously issued links.

Prepare the four decrypted native release directories in one private folder, using
the process in `packaging/RELEASE.md`, then run:

```sh
python delivery/prepare_catalog.py /private/release
```

This verifies build checksums and matching versions/source commits, creates source
and notice packages, and writes `catalog.json`. The service validates all files at
startup; it refuses missing or corrupt installers. Keep this directory outside Pages.

Set these variables in the host's secret/environment settings, never in Git or chat:

- `STRIPE_SECRET_KEY`: the Stripe sandbox secret key for the account owning the link.
- `STRIPE_WEBHOOK_SECRET`: the signing secret for this service's webhook endpoint.
- `STRIPE_PAYMENT_LINK_ID`: the link's `plink_…` ID from Stripe, not its public URL.
- `STRIPE_PRICE_ID`: the link's $1 one-time `price_…` ID.
- `DOWNLOAD_SIGNING_KEY`: a separately generated random secret of at least 32 characters.
- `PAYMENT_MODE=test`.
- `DELIVERY_CATALOG`: absolute path to the private `catalog.json`.
- `DELIVERY_DB`: a writable, persistent SQLite file path for purchase records.

Install `delivery/requirements.txt` and run `gunicorn --bind 0.0.0.0:8080 --workers 2
--threads 4 --timeout 120 'delivery.server:create_app()'` from the repository root.
Alternatively build with `docker build -f delivery/Dockerfile -t anharmonic-delivery .`.
The container expects a private volume mounted at `/data`, readable by UID 10001,
with its database directory writable by that user. Back up the purchase database.
Expose it through HTTPS; disable query-string logging at the proxy because the
purchase reference grants download access. Gunicorn access logging is off by default.
Apply request limits at the host/proxy before opening public traffic.

In the Stripe Dashboard, edit the existing test Payment Link's **After payment**
setting to redirect to `https://YOUR-DELIVERY-HOST/download-success?session_id={CHECKOUT_SESSION_ID}`.
Add a webhook at `https://YOUR-DELIVERY-HOST/webhook`, subscribing to
`checkout.session.completed` and `checkout.session.async_payment_succeeded`, and
store its signing secret above. The webhook and return page both record a purchase
idempotently. Downloads recheck Stripe; a redirect or browser flag alone grants nothing.

Make a sandbox purchase for each platform and confirm the correct file starts
downloading. The page includes manual buttons, expiring links that can be refreshed,
resumable downloads, checksums, and matching source/notices. Older paid sessions
without a platform reference show all four choices. Their Checkout Session ID can
be opened at the same success URL; changing the redirect does not reopen old receipts.
Do not share a customer's success URL. Once the hosted flow passes, set
`deliveryReady: true` in `config.js`. This flag changes wording only, never authorization.

Run service checks with `python -m pytest -q delivery/test_server.py` after installing
its requirements and pytest. These checks also run in the dedicated delivery workflow.
The current service covers the configured standard-download price and one major
version. Supporter offers remain disabled. Customer email/account recovery and
multi-major-version catalogs are not implemented; do not open those offers until they are.

## GitHub Pages

[Publish studio website](../.github/workflows/studio-pages.yml) validates the site and
publishes `website/` when site-related changes reach `main`. Manual dispatch remains
available on the default branch. Pages uses GitHub Actions as its source.

Public URL: https://anharmoniclabs.github.io/AnharmonicStudio/

The outlined logo pack is shared with the desktop app. Asset paths stay relative so the
site works below the repository path. If the public URL changes, update the absolute
social image URL in `index.html`. Increment the stylesheet/script query versions when
shipping changes that should invalidate cached assets.
