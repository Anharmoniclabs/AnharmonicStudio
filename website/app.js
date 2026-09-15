(() => {
  'use strict';
  const video = document.querySelector('#commercial');
  const play = document.querySelector('#film-play');
  const status = document.querySelector('#video-status');
  play.hidden = false;
  video.controls = false;
  play.addEventListener('click', async () => {
    play.hidden = true;
    video.controls = true;
    try { await video.play(); video.focus(); }
    catch { status.textContent = 'Press play in the video controls, or download the film below.'; }
  });
  video.addEventListener('play', () => { play.hidden = true; video.controls = true; status.textContent = ''; });
  video.addEventListener('ended', () => { play.hidden = false; video.controls = false; });
  video.addEventListener('error', () => { play.hidden = true; video.controls = true; status.textContent = 'The film could not load. Use the download link to watch it.'; });

  const workspaces = {
    sample: ['FIND THE MOMENT', 'A whole new life for any sound.', 'Import audio, find the part that moves you, and turn it into playable slices. Trim by hand, detect transients, or map your cuts across four banks of pads.', 'The actual Anharmonic sampler displaying a drum-break waveform, trim controls, and pad mapping.'],
    beats: ['GIVE IT A PULSE', 'Your rhythm. Step by step.', 'Play the pads or draw hits into the step sequencer. Shape velocities, switch pad banks, and build a groove that feels like you.', 'The Anharmonic Beats workspace with kick, snare, hat, and percussion steps in a drum pattern.'],
    synth: ['SHAPE YOUR SIGNATURE', 'Find a sound. Then go further.', 'Explore playable sounds and the built-in analog synth. Adjust oscillators, filters, envelopes, and the arpeggiator to turn a starting point into your signature.', 'The Anharmonic Instruments workspace showing its sound browser, synthesizer visualizer, filter response, and playable keyboard.'],
    notes: ['LET THE MELODY IN', 'Let a sample sing.', 'Play a sample chromatically or write for the synth. Draw, move, and resize notes in the piano roll, with control over pitch, timing, and velocity.', 'The Anharmonic piano roll for writing and editing chromatic sample and synthesizer notes.'],
    song: ['SEE THE BIG PICTURE', 'A loop is only the beginning.', 'Bring patterns and audio together in the song timeline. Layer sections, move clips, create variations, and build the arrangement around your idea.', 'The Anharmonic Song timeline with layered drums, bass, keys, atmosphere, and melody patterns.'],
    mix: ['MAKE EVERY LAYER COUNT', 'Find the balance. Feel the finish.', 'Bring each part into focus with track levels, EQ, saturation, compression, delay, and reverb. Then bounce the arrangement to a stereo WAV.', 'The Anharmonic mixer with eight track channels, a master output, and an effects rack.']
  };
  const tabs = [...document.querySelectorAll('[role="tab"]')];
  function selectTab(tab, focus = false) {
    const key = tab.dataset.workspace;
    const [kicker, title, copy, alt] = workspaces[key];
    tabs.forEach(t => { t.setAttribute('aria-selected', String(t === tab)); t.tabIndex = t === tab ? 0 : -1; });
    document.querySelector('#workspace-panel').setAttribute('aria-labelledby', tab.id);
    document.querySelector('#workspace-image').src = `assets/${key}.png`;
    document.querySelector('#workspace-image').alt = alt;
    document.querySelector('#workspace-kicker').textContent = kicker;
    document.querySelector('#workspace-title').textContent = title;
    document.querySelector('#workspace-copy').textContent = copy;
    document.querySelector('#workspace-count').textContent = `0${tabs.indexOf(tab) + 1} / 06`;
    if (focus) tab.focus({ preventScroll: true });
  }
  tabs.forEach((tab, index) => {
    tab.addEventListener('click', () => selectTab(tab));
    tab.addEventListener('keydown', event => {
      let next;
      if (event.key === 'ArrowRight') next = (index + 1) % tabs.length;
      if (event.key === 'ArrowLeft') next = (index + tabs.length - 1) % tabs.length;
      if (event.key === 'Home') next = 0;
      if (event.key === 'End') next = tabs.length - 1;
      if (next !== undefined) { event.preventDefault(); selectTab(tabs[next], true); }
    });
  });

  const bars = document.querySelector('.sound-bars');
  for (let i = 0; i < 48; i++) {
    const bar = document.createElement('i');
    bar.style.setProperty('--bar-height', `${8 + Math.abs(Math.sin(i * 1.72) * Math.cos(i * .23)) * 35}px`);
    bar.style.setProperty('--bar-delay', `${-i * .073}s`);
    bars.append(bar);
  }

  const descriptions = {
    windows: 'Windows: a per-user installer with a Start menu shortcut and uninstaller.',
    'mac-arm': 'Apple Silicon Mac: a native ARM64 app in a DMG. Copy the app to Applications.',
    'mac-intel': 'Intel Mac: a native x86_64 app in a DMG. Copy the app to Applications.',
    linux: 'Linux: an executable folder in a tar.gz, with an optional per-user install command.'
  };
  document.querySelectorAll('[name="platform"]').forEach(input => {
    input.addEventListener('change', () => {
      document.querySelector('#platform-detail').textContent = descriptions[input.value];
      document.querySelectorAll('.commerce-link[data-checkout]').forEach(link => {
        const url = new URL(link.dataset.checkout);
        if (link.dataset.offer !== 'donation') url.searchParams.set('client_reference_id', `as_v1_${input.value}`);
        link.href = url.href;
      });
    });
  });

  const config = window.ANHARMONIC_CONFIG || {};
  const installationGuidance = document.querySelector('#installation-guidance');
  const revealInstallationGuidance = () => {
    if (location.hash === '#installation-guidance') installationGuidance.open = true;
  };
  window.addEventListener('hashchange', revealInstallationGuidance);
  document.querySelectorAll('a[href="#installation-guidance"]').forEach(link => {
    link.addEventListener('click', () => { installationGuidance.open = true; });
  });
  revealInstallationGuidance();
  const dialog = document.querySelector('#download-dialog');
  dialog.querySelector('.dialog-close').addEventListener('click', () => dialog.close());
  dialog.addEventListener('click', event => {
    const rect = dialog.getBoundingClientRect();
    if (event.target === dialog && (event.clientX < rect.left || event.clientX > rect.right || event.clientY < rect.top || event.clientY > rect.bottom)) dialog.close();
  });
  const details = {
    download: ['OFFICIAL DESKTOP DOWNLOADS', 'The standard download costs $1 USD once, including the current major version and its updates on Linux, Windows, and Mac. Checkout is not open yet.'],
    supporter: ['SUPPORTER DOWNLOAD', 'The planned $45-or-more supporter download includes the current and next major version and their updates, on all supported platforms. Checkout is not open yet.'],
    donation: ['OPTIONAL DONATION', 'Donations will support development without including downloads or update access. Donation payments are not open yet. No payment is being collected.']
  };
  let downloadsOpen = false;
  let testCheckoutOpen = false;
  const testMode = config.paymentMode === 'test';
  document.querySelectorAll('.commerce-link').forEach(link => {
    const offer = link.dataset.offer;
    const testOffer = testMode && offer !== 'donation';
    let checkout;
    try {
      const url = new URL(config.links?.[offer]);
      const stripeTestLink = url.hostname === 'buy.stripe.com' && url.pathname.startsWith('/test_') && !url.port;
      const enabled = offer === 'donation'
        ? config.donationsOpen === true && !stripeTestLink
        : testMode
          ? config.testCheckoutOpen === true && offer === 'download' && stripeTestLink
          : config.paymentMode === 'live' && !stripeTestLink && config.salesOpen === true;
      if (enabled && url.protocol === 'https:' && !url.username && !url.password) checkout = url.href;
    } catch { /* An absent or invalid destination keeps this offer closed. */ }
    if (checkout) {
      link.dataset.checkout = checkout;
      const destination = new URL(checkout);
      const platform = document.querySelector('[name="platform"]:checked').value;
      if (offer !== 'donation') destination.searchParams.set('client_reference_id', `as_v1_${platform}`);
      link.href = destination.href;
      link.target = '_blank';
      link.rel = 'noopener noreferrer';
      link.firstChild.textContent = testOffer ? 'Try $1 test checkout ' : link.dataset.liveLabel + ' ';
      const card = link.closest('article');
      const badge = card.querySelector('.availability');
      const note = card.querySelector('.price-note');
      if (badge) badge.textContent = testOffer ? 'TEST MODE' : 'AVAILABLE';
      if (note) note.textContent = testOffer
        ? (config.deliveryReady === true ? 'Test mode: no real charge. Your selected installer downloads after payment confirmation.' : 'Stripe test mode. No real charge. Automatic downloads are being connected.')
        : 'One-time $1 USD payment, plus applicable tax. Your selected installer starts after payment confirmation.';
      if (testOffer) testCheckoutOpen = true;
      else if (offer === 'donation') document.querySelector('#donation-status').textContent = 'Optional contributions are open. Donations do not include downloads.';
      else downloadsOpen = true;
    } else {
      link.href = '#release-status';
      link.removeAttribute('target');
      if (offer === 'donation') document.querySelector('#donation-status').textContent = 'Donations are not open yet.';
      link.addEventListener('click', event => {
        if (typeof dialog.showModal !== 'function') return;
        event.preventDefault();
        document.querySelector('#dialog-kicker').textContent = details[offer][0];
        document.querySelector('#dialog-copy').textContent = details[offer][1];
        dialog.showModal();
      });
    }
  });
  if (testCheckoutOpen) {
    document.querySelector('#release-status-title').textContent = '$1 test checkout';
    const deliveryStatus = config.deliveryReady === true
      ? 'After Stripe confirms the test payment, your selected installer starts downloading. Keep the confirmation page for downloads later.'
      : 'Automatic installer delivery is being connected and is not available yet.';
    document.querySelector('#release-status-copy').textContent = `Try the one-time checkout in Stripe test mode. No real money is charged. ${deliveryStatus} Live sales are not open yet.`;
    document.querySelector('#availability-answer').textContent = `You can try the $1 one-time Stripe test checkout. ${deliveryStatus} Live sales are not open yet.`;
    document.querySelector('.hero-note strong').textContent = '$1 test checkout available.';
  } else if (downloadsOpen) {
    document.querySelector('#release-status-title').textContent = 'Official downloads are open';
    document.querySelector('#release-status-copy').textContent = 'Choose your computer below. Pay $1 USD once, plus applicable tax, and your selected installer downloads after Stripe confirms payment. These are unsigned 0.1.0-rc.1 release candidates; they are not Apple-notarized, and physical audio-interface testing remains unfinished.';
    document.querySelector('#availability-answer').textContent = 'Yes. The standard $1 USD download is open, plus applicable tax at Stripe checkout. Choose your computer above; its installer starts after payment confirmation. These are unsigned release candidates. The complete source remains free on GitHub.';
    document.querySelector('.hero-note strong').textContent = 'Official downloads available.';
  } else {
    document.querySelector('#release-status-title').textContent = 'Checkout is temporarily unavailable';
    document.querySelector('#release-status-copy').textContent = 'Official checkout is closed. The complete source and build scripts remain free on GitHub.';
    document.querySelector('#availability-answer').textContent = 'Checkout is closed right now. Source code for every platform remains available for free.';
    document.querySelector('.hero-note strong').textContent = 'Free source available.';
    document.querySelector('.pack-card .availability').textContent = 'UNAVAILABLE';
    document.querySelector('.pack-card .price-note').textContent = 'Checkout is closed right now.';
    document.querySelector('.pack-card .commerce-link').firstChild.textContent = 'Download details ';
  }
})();
