// Public configuration only. Never put payment secrets or installer URLs here.
// Enable an offer only after hosted checkout verifies payment and provides
// private downloads. The provider must enforce prices and update entitlements.
window.ANHARMONIC_CONFIG = Object.freeze({
  salesOpen: false,
  donationsOpen: false,
  links: Object.freeze({
    download: null,     // Hosted pay-what-you-can checkout: $1 minimum.
    supporter: null,    // Hosted supporter checkout: $45 minimum.
    subscription: null, // Hosted $1 / $4 / $10 / $50 monthly plan selection.
    donation: null      // Separate optional contribution; no download entitlement.
  })
});
