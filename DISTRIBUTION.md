# Free source, paid official builds

Anharmonic Studio's application source remains free under GPL-2.0-or-later.
Anyone can obtain, build, study, modify, and share the code under that license.
Purchasing an official download does not replace or restrict the recipient's GPL rights.

Official packages for Linux x86_64, Windows x86_64, Intel Mac, and Apple Silicon Mac
are intended for paid distribution. There are no feature tiers, subscription checks,
or activation requirements in the application. An installed version continues working
when monthly support ends.

## Planned launch pricing

Prices are in USD. Checkout is not open yet; these are the launch plans to configure,
not currently purchasable products.

| Option | Planned price | Official download access |
| --- | --- | --- |
| Source code | Free | Build the full application yourself; no payment or account required |
| Official download | Pay what you can, $1 minimum | All supported platforms for the current major version and its updates |
| Supporter download | $45 or more, one time | All supported platforms for the current and next major version and their updates |
| Monthly support | $1, $4, $10, or $50 per month | Official releases while subscribed; downloaded versions continue working after cancellation |
| Donation | Any amount, optional | Development support only; does not grant downloads or update access |

The pay-what-you-can download is the option for someone who wants to contribute an
amount of their choice and receive a build. The separate donation option must not be
presented as a purchase. No tax-deductibility claim is made for contributions.

This structure follows the separation of free source, paid builds, and optional support
in [Ardour's FAQ](https://ardour.org/faq.html) and
[subscription options](https://community.ardour.org/subscribe), checked September 9, 2026.
Anharmonic Studio is an independent project, not an Ardour product or affiliate.

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
third-party corresponding-source review remain before commercial release.

The [candidate build guide](packaging/RELEASE.md) covers native packaging and encrypted
CI artifacts. Application source for every delivered build, notices, and checksums must
accompany its distribution. Preserve exact source versions when preparing customer packages.

## Connect checkout and private delivery

GitHub Pages is the public storefront. It must never contain paid installers, private
keys, checkout secrets, or a JavaScript-only payment gate. Keep binaries in private
storage or a commerce provider's protected delivery system. Public source stays on GitHub.

Before opening an offer:

1. Configure the provider's product and price rules to match the table above. Let the
   provider collect payment details; this static site collects none.
2. Verify completed payment on the server/provider, including amount, currency, product,
   and payment status. A return URL or browser flag is not proof of purchase.
3. Grant download access by product, major-version coverage, and subscription status.
   Make payment events idempotent and handle failed payments, refunds, cancellation,
   and redownloads. Donation events grant no download entitlement.
4. Deliver short-lived links or authenticated downloads from private storage. Provide
   the matching source archive, license/dependency notices, and checksums alongside builds.
5. Test purchase, cancellation, refund, and expired-link paths in the provider's test mode.
   Confirm an ended subscription never changes the installed application's behavior.
6. Set the public hosted URLs in `website/config.js`. Set `salesOpen: true` only after
   delivery and release checks are complete. Donations have a separate `donationsOpen`
   flag. Unconfigured or invalid offer URLs stay closed, even if other offers are open.

The checkout provider is responsible for authoritative pricing, buyer notices, payment
verification, receipts, and download entitlements. Links in `config.js` are routing only.
Changing that file does not implement access control. Keep checkout and donation settings
consistent with this document and the website copy.
