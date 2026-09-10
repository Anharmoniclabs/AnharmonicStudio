# Anharmonic Studio website

A static GitHub Pages storefront with the app tour, original commercial, free source,
and a $1 USD one-time desktop download through Stripe. Applicable tax is shown at
checkout. Supporter downloads remain disabled. Optional donations are open separately
at https://ko-fi.com/anharmoniclabs and do not include downloads. No subscription is offered.
The current installers are unsigned 0.1.0-rc.1 release candidates.

The visible notice before pricing and the reminder beside the standard checkout button
explain Windows publisher warnings, Apple's lack of notarization, possible installation
blocks, and Linux prerequisites. They also work without JavaScript. The confirmation
page repeats the notice and links to `#installation-guidance`, which cites official
Apple/Microsoft instructions. These are disclosures, not a security bypass or an extra
purchase-approval step. Official releases intentionally remain unsigned: paid
Windows signing and Apple notarization are not release requirements. Keep
disclosures synchronized with the actual package status. Candidate archives and
installed packages include `packaging/INSTALLATION.txt` with matching guidance;
never instruct customers to disable system-wide security protections.

## Preview and validate

```sh
python scripts/check_website.py
python -m http.server 8765 --bind 127.0.0.1 --directory website
```

Open http://127.0.0.1:8765. Source links, FAQs, prices, and video work without JavaScript.
JavaScript adds workspace tabs and sends the selected platform to checkout. Without it,
checkout's confirmation page offers a platform chooser. The confirmation page requires
JavaScript to verify the purchase and request download links.

The experimental browser DAW is available at http://127.0.0.1:8765/app/studio.html. It
mirrors the desktop Studio shell with project and transport bars, Browser, Pads, and
Song, Beats, Notes, Sampler, Instruments, Autotune, and Mix workspaces. It is a
browser-native port, not the PySide6 desktop window; full audio parity, recording,
plugins, and stem separation remain staged workspaces.

The browser app now loads `app/project-model.js` before `app/studio.js`. This is the
compatibility foundation for the remaining workspace ports: it normalizes legacy
browser projects and desktop-shaped pattern documents into project format v5 state,
including 64 pads, 8 tracks, stable IDs, velocity maps, notes, media manifests, and
transaction history. Save/load uses that normalized document; Undo and Redo operate on
the same transactions. New editors should mutate this store rather than introducing
tab-local state.

The checker rejects installers and unexpected file types in Pages. Before publication,
check mobile and desktop widths, keyboard navigation, all four platform selections,
and closed/test/live checkout configurations. Keep browser evidence outside Git.

## Public source and paid packages

All source and platform build scripts are public in this repository under GPL-3.0-or-later.
[Matching release source and license notices](https://github.com/Anharmoniclabs/AnharmonicStudio/releases/tag/v0.1.0-rc.1-source)
are public GitHub release assets, accessible without payment. Cloudflare R2 stores only
the compiled Windows EXE, two Mac DMGs, and Linux tar.gz package. The private bucket's
public access stays disabled. Never upload installers to GitHub Releases or Pages.

`config.js` contains public checkout URLs and display flags only. `paymentMode: 'live'`,
`salesOpen: true`, and `deliveryReady: true` enable the standard download. Test mode
requires an explicit Stripe test link and `testCheckoutOpen: true`; it labels checkout
as a simulation with no real charge. Missing or invalid offer URLs remain closed.
Donations use their own `donationsOpen` flag independently of installer payment mode.
Keep their static link in `index.html` synchronized for visitors without JavaScript.
Neither those flags nor a browser redirect authorize a download.

## Private installer service

The deployed service is `delivery/cloudflare/worker.mjs` at
https://anharmonic-downloads.luisminier79.workers.dev. It uses private R2 storage and D1
purchase records. The storefront passes `client_reference_id=as_v1_windows` (or
`mac-arm`, `mac-intel`, `linux`) to the configured Stripe Payment Link. A signed webhook
must confirm the same link, live/test mode, completed payment, $1 USD subtotal, and no
discount. Tax may increase the total. [Stripe's adaptive-pricing event amounts](https://docs.stripe.com/payments/currencies/localize-prices/adaptive-pricing)
remain in the integration currency; localized amounts are separate presentment details.

Successful claims issue 15-minute signed links. Each file request checks the purchase
and revocation database. Failed payments, refunds, and disputes grant no further access;
webhook handling is idempotent and accepts out-of-order revocations. Stripe retries
failed webhooks. Until the confirmation arrives, the page offers retry without asking
for another payment. Streaming downloads support resumption and include SHA-256 hashes.

The service stores a hash of the Checkout Session reference, its payment-intent ID,
platform, release, and creation time. It does not store customer card details, names,
or email addresses. Keep the confirmation URL private and bookmark it for redownloads.
Customer account/email recovery and historical major-version catalogs are not implemented.
The API secret used for account setup is not required by the deployed Worker.

### Deploy or update

Use Node 24, Python 3, and Cloudflare Wrangler. Authenticate with `npx wrangler login`.
For a separate deployment, create a private R2 bucket and D1 database, then update the
resource names and ID in `delivery/cloudflare/wrangler.json`. Apply `schema.sql` once:

```sh
cd delivery/cloudflare
npm ci
npx wrangler d1 execute anharmonic-purchases-live --remote --file schema.sql
npm test
npm run build
npx wrangler deploy --dry-run
```

Prepare native builds using `packaging/RELEASE.md` and `delivery/prepare_catalog.py`.
Publish only the matching source/notices ZIPs to a public GitHub source release. Upload
only the four compiled installer files using `wrangler r2 object put --remote`. Keep the
catalog's object keys, sizes, hashes, release, and public source URLs in sync with those
verified files. Do not replace an existing version's artifacts silently.

Configure a fixed-quantity, one-time $1 USD Stripe Payment Link with promotion codes
disabled. For this deployment, Stripe Managed Payments handles checkout and applicable
tax; the downloadable software product uses tax code `txcd_10202000`. Configure:

- After-payment redirect: `https://YOUR-HOST/download-success#session_id={CHECKOUT_SESSION_ID}`.
- Webhook URL: `https://YOUR-HOST/webhook`, API version `2025-03-31.basil`.
- Events: `checkout.session.completed`, `checkout.session.async_payment_succeeded`,
  `checkout.session.async_payment_failed`, `charge.refunded`, `charge.dispute.created`.
- Worker variables: `PAYMENT_MODE`, `STRIPE_PAYMENT_LINK_ID`, `CATALOG`, `DELIVERY_OPEN`.

Set secrets through Wrangler's hidden prompt, never source files or chat:

```sh
npx wrangler secret put STRIPE_WEBHOOK_SECRET
npx wrangler secret put DOWNLOAD_SIGNING_KEY
```

Use the endpoint's Stripe signing secret and a separate randomly generated key of at
least 32 characters. Do not configure a Stripe API key on the Worker. Deploy with
`npx wrangler deploy`; confirm `/health` reports ready. Keep purchasing disabled until
payment verification, platform selection, full installer hashes, refunds, and browser
downloads pass against the hosted service. Open checkout and update `config.js` only
then. Preserve D1 purchase records across deployments and releases. Do not delete or
recreate production webhooks to rotate secrets while sales are active.

The delivery workflow runs 21 Worker runtime checks and the optional Python service's
31 checks. Hosted setup additionally verifies all actual installer bytes and automatic
browser delivery using temporary signed fixtures, then removes those fixture records.
Those checks do not submit a real card payment. A Stripe sandbox configuration should
be used for repeatable payment tests; never submit test cards to live checkout.

`delivery/server.py` remains an alternative Flask/container implementation with its own
private local catalog and Stripe API verification. It is not the deployed service.
Its environment settings are defined in that module; test with
`python -m pytest -q delivery/test_server.py` after installing its requirements.

## GitHub Pages

The [Pages workflow](../.github/workflows/studio-pages.yml) validates and publishes only
`website/` when site changes reach `main`.
Public URL: https://anharmoniclabs.github.io/AnharmonicStudio/

Asset paths are relative. Increment stylesheet/script query versions when changing
cached assets. The Worker confirmation page is deployed separately; Pages publication
does not update it. Its public asset build admits only the three confirmation-page files.
