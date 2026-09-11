(() => {
  'use strict';

  // Older iOS Safari releases do not expose crypto.randomUUID(), while the
  // studio uses it for project/media identifiers. Keep the same UUID shape
  // using getRandomValues so the app does not fail before its UI can render.
  if (window.crypto && !window.crypto.randomUUID && window.crypto.getRandomValues) {
    try {
      Object.defineProperty(window.crypto, 'randomUUID', {
        configurable: true,
        value: () => {
          const bytes = new Uint8Array(16);
          window.crypto.getRandomValues(bytes);
          bytes[6] = (bytes[6] & 0x0f) | 0x40;
          bytes[8] = (bytes[8] & 0x3f) | 0x80;
          const hex = [...bytes].map(value => value.toString(16).padStart(2, '0'));
          return `${hex.slice(0, 4).join('')}-${hex.slice(4, 6).join('')}-${hex.slice(6, 8).join('')}-${hex.slice(8, 10).join('')}-${hex.slice(10).join('')}`;
        }
      });
    } catch { /* A locked crypto object is uncommon; leave native behavior intact. */ }
  }

  const api = window.AnharmonicAudio;
  const prototype = api?.AudioEngine?.prototype;
  if (!prototype || prototype.__anharmonicMobileCompat) return;

  const engines = new Set();
  const originalResume = prototype.resume;

  // AnharmonicAudio is intentionally Object.freeze()'d. Patch the class
  // prototype instead of replacing the exported constructor, so mobile
  // compatibility does not violate the public API's immutability contract.
  Object.defineProperty(prototype, '__anharmonicMobileCompat', {
    configurable: false,
    enumerable: false,
    value: true,
  });

  prototype.resume = async function(...args) {
    engines.add(this);
    try {
      return await originalResume.apply(this, args);
    } catch (error) {
      // iOS can revoke transient user activation while a file picker closes.
      // Decoding is still legal on a suspended AudioContext, so allow import to
      // finish and unlock this exact context on the next real user gesture.
      const message = String(error?.message || error || '');
      if (
        this.context?.state === 'suspended'
        && /audio could not start|not.?allowed|user gesture|interaction/i.test(message)
      ) {
        this.sync();
        return this.context;
      }
      throw error;
    }
  };

  // Safari requires resume() to happen synchronously from a user gesture.
  // Run in capture phase so a previously-created suspended context is unlocked
  // before pad, preview, transport, sampler, or file-input handlers run.
  const unlockAudio = () => {
    for (const engine of engines) {
      const context = engine.context;
      if (context?.state !== 'suspended') continue;
      try {
        const pending = context.resume();
        if (pending?.catch) pending.catch(() => {});
      } catch { /* A later user gesture gets another chance. */ }
    }
  };

  document.addEventListener('pointerdown', unlockAudio, { capture: true, passive: true });
  document.addEventListener('touchstart', unlockAudio, { capture: true, passive: true });
  document.addEventListener('keydown', unlockAudio, { capture: true });
  document.addEventListener('visibilitychange', () => {
    if (!document.hidden) unlockAudio();
  });
})();
