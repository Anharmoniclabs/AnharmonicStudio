// Public configuration only. Never put payment secrets or installer URLs here.
// Enable a live offer only after hosted checkout verifies payment and provides
// private downloads. The provider must enforce prices and update entitlements.
window.ANHARMONIC_CONFIG = Object.freeze({
  paymentMode: 'test',
  testCheckoutOpen: true, // Verified $1 one-time Stripe sandbox checkout.
  deliveryReady: false, // Enable only after a hosted, verified purchase downloads an installer.
  salesOpen: false,
  donationsOpen: false,
  links: Object.freeze({
    download: 'https://buy.stripe.com/test_4gMdR877K9rwdfA30g9oc00', // $1 one-time test payment.
    supporter: null,    // Hosted supporter checkout: $45 minimum.
    donation: null      // Separate optional contribution; no download entitlement.
  })
});
