# Free source, paid official builds

Anharmonic Studio's application source remains free under GPL-3.0-or-later.
Anyone can obtain, build, study, modify, and share the code under that license.
Purchasing an official download does not replace or restrict the recipient's GPL rights.

Official packages for Linux x86_64, Windows x86_64, Intel Mac, and Apple Silicon Mac
are intended for paid distribution. There are no feature tiers, subscription checks,
or activation requirements in the application. An installed version continues working
without recurring payments.

## Download pricing

The standard download is $1 USD once, plus applicable tax. Supporter downloads
remain planned. Optional donations are open separately on
[Ko-fi](https://ko-fi.com/anharmoniclabs); they grant no installer access.

| Option | Price (USD) | Official download access |
| --- | --- | --- |
| Source code | Free | Build the full application yourself; no payment or account required |
| Official download | $1, one time | All supported platforms for the current major version and its updates |
| Supporter download (planned) | $45 or more, one time | All supported platforms for the current and next major version and their updates |
| Donation on Ko-fi | Any amount, optional | Development support only; does not grant downloads or update access |

The standard download costs $1 USD once. The separate donation option does not
include a download and must not be presented as a purchase.

## Developer source and supported distribution

Official released packages are the supported distribution for musicians. Source builds
are intended for developers, who maintain their own compiler, dependencies, and audio
configuration. Installation and configuration troubleshooting for self-compiled copies
is not provided. See [SUPPORT.md](SUPPORT.md) and [BUILDING.md](BUILDING.md).

The source remains complete and buildable. `run.sh`, native sources, dependency locks,
and platform packaging definitions remain available; no artificial obstacles are added.

## Release status

Native 0.1.0-rc.1 candidates have passed software checks on all four targets. They remain
unsigned candidates. Signing/notarization, physical audio-interface acceptance, and
third-party corresponding-source review remain unfinished. Checkout identifies these
as release candidates; purchase does not imply those checks are complete.

The [candidate build guide](packaging/RELEASE.md) covers native packaging and encrypted
CI artifacts. Application source for every delivered build, notices, and checksums must
accompany its distribution. Preserve exact source versions when preparing customer packages.

## Checkout and private delivery

GitHub Pages is the public storefront. Stripe collects a one-time payment, then redirects
the buyer to the Cloudflare confirmation page. A signed Stripe webhook must confirm the
configured $1 USD Payment Link and completed payment before download access is granted.
The selected platform starts downloading automatically; all four platforms are included.

Cloudflare R2 contains only compiled Windows EXE, macOS DMG, and Linux tar.gz packages.
The bucket has no public download domain. The Worker streams files using expiring signed
links and checks its purchase database on every download. Refund and dispute webhooks
revoke access. Keep the confirmation-page address private and save it for redownloads.
Email/account recovery and older-major-version catalogs are not implemented yet.

All application source, native engine code, platform build scripts, and dependency locks
are public in this repository. The exact release source and platform license notices are
also [public release assets](https://github.com/Anharmoniclabs/AnharmonicStudio/releases/tag/v0.1.0-rc.1-source).
They require no payment or account. Do not upload installers to GitHub Releases or Pages;
do not put source packages behind the payment gate.

The Worker uses a Stripe webhook signing secret and a separate download signing key.
It does not require or store a Stripe API secret key. No secret belongs in public code,
website configuration, build artifacts, or chat. See the [deployment instructions](website/README.md#private-installer-service).

Prices, payment status, and download access are enforced by Stripe and the delivery
service. The flags and links in `website/config.js` only control storefront routing and
wording. Failed, incomplete, or unsigned payment events grant no access. Hosted checks
use temporary signed fixtures to verify all four actual installer hashes, automatic
browser delivery, and revocation; they do not constitute a real card purchase.
