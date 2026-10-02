(() => {
  'use strict';

  // Installable-app plumbing: service worker registration, install prompts,
  // update notices and standalone-mode detection. Nothing here touches project
  // data; the studio keeps working unchanged when service workers are absent.
  const $ = selector => document.querySelector(selector);
  const standalone = () => window.matchMedia('(display-mode: standalone)').matches || window.matchMedia('(display-mode: fullscreen)').matches || window.navigator.standalone === true;
  const isiOS = () => /iPad|iPhone|iPod/.test(navigator.userAgent) || (navigator.platform === 'MacIntel' && navigator.maxTouchPoints > 1);
  const status = message => { const element = $('#status'); if (element && message) element.textContent = message; document.dispatchEvent(new CustomEvent('anharmonic-status', { detail: message })); };
  const api = { installPrompt: null, registration: null, waiting: null, standalone: standalone(), listeners: new Set() };
  const emit = () => api.listeners.forEach(listener => { try { listener(api); } catch { /* listener error must not break the studio */ } });

  document.documentElement.dataset.display = api.standalone ? 'standalone' : 'browser';
  window.matchMedia('(display-mode: standalone)').addEventListener?.('change', () => { api.standalone = standalone(); document.documentElement.dataset.display = api.standalone ? 'standalone' : 'browser'; emit(); });

  window.addEventListener('beforeinstallprompt', event => {
    // Keep the browser's mini-infobar away; the studio offers its own Install control.
    event.preventDefault();
    api.installPrompt = event; emit();
  });
  window.addEventListener('appinstalled', () => { api.installPrompt = null; api.standalone = true; status('Anharmonic Studio is installed. Open it from your home screen or app list to use it full screen, offline.'); emit(); });

  api.canInstall = () => !api.standalone && (Boolean(api.installPrompt) || isiOS());
  api.install = async () => {
    if (api.installPrompt) {
      const prompt = api.installPrompt; api.installPrompt = null; emit();
      prompt.prompt();
      const choice = await prompt.userChoice.catch(() => ({ outcome: 'dismissed' }));
      if (choice.outcome !== 'accepted') { api.installPrompt = prompt; emit(); status('Install cancelled. You can install the studio later from the Project menu.'); }
      return choice.outcome;
    }
    const dialog = $('#install-dialog');
    if (dialog) {
      const steps = $('#install-steps');
      if (steps) steps.textContent = isiOS()
        ? 'In Safari, tap the Share button, then “Add to Home Screen”, then Add. The studio opens full screen from the icon, keeps working offline, and stores projects on this device.'
        : 'Open this page in Chrome, Edge or Samsung Internet and choose “Install app” or “Add to Home screen” from the browser menu. The studio then opens full screen and works offline.';
      dialog.showModal();
    } else status('Use your browser menu to add Anharmonic Studio to the home screen.');
    return 'manual';
  };
  api.applyUpdate = () => {
    if (!api.waiting) return false;
    api.waiting.postMessage({ type: 'anharmonic-sw-skip-waiting' });
    return true;
  };

  if ('serviceWorker' in navigator && (location.protocol === 'https:' || ['localhost', '127.0.0.1'].includes(location.hostname))) {
    let reloading = false;
    navigator.serviceWorker.addEventListener('controllerchange', () => {
      // Only reload when the user asked for the update; a first install or an
      // automatic takeover must never interrupt a recording or unsaved edit.
      if (api.reloadRequested && !reloading) { reloading = true; location.reload(); }
    });
    navigator.serviceWorker.addEventListener('message', event => {
      if (event.data?.type === 'anharmonic-sw-version') { api.version = event.data.version; emit(); }
    });
    window.addEventListener('load', async () => {
      try {
        const registration = await navigator.serviceWorker.register('sw.js', { scope: './' });
        api.registration = registration;
        const track = worker => {
          if (!worker) return;
          worker.addEventListener('statechange', () => {
            if (worker.state === 'installed' && navigator.serviceWorker.controller) { api.waiting = registration.waiting || worker; emit(); status('A studio update is ready. Choose “Reload for update” in the Project menu when you are not recording.'); }
          });
        };
        if (registration.waiting && navigator.serviceWorker.controller) { api.waiting = registration.waiting; emit(); }
        track(registration.installing);
        registration.addEventListener('updatefound', () => track(registration.installing));
        navigator.serviceWorker.controller?.postMessage({ type: 'anharmonic-sw-version' });
        if (!navigator.serviceWorker.controller) {
          const worker = registration.installing || registration.waiting || registration.active;
          worker?.addEventListener('statechange', () => { if (worker.state === 'activated') { api.offlineReady = true; emit(); } });
          if (registration.active?.state === 'activated') { api.offlineReady = true; emit(); }
        } else { api.offlineReady = true; emit(); }
      } catch (error) { api.error = error; emit(); }
    });
  }
  window.AnharmonicPWA = api;
})();
