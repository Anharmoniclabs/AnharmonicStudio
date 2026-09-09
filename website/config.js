// Public configuration only. Never put payment secrets or installer URLs here.
// Enable a live offer only after hosted checkout verifies payment and provides
// private downloads. The provider must enforce prices and update entitlements.
window.ANHARMONIC_CONFIG = Object.freeze({
  paymentMode: 'live',
  testCheckoutOpen: false,
  deliveryReady: true, // Display only; the Worker verifies payment before delivery.
  salesOpen: true,
  donationsOpen: false,
  links: Object.freeze({
    download: 'https://buy.stripe.com/5kQaEWbqY2pKak2fbOco000', // $1 USD once, plus applicable tax.
    supporter: null,    // Hosted supporter checkout: $45 minimum.
    donation: null      // Separate optional contribution; no download entitlement.
  })
});
