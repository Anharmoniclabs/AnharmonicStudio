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
  if (!api?.AudioEngine) return;

  const BaseAudioEngine = api.AudioEngine;
  const engines = new Set();

  class MobileSafeAudioEngine extends BaseAudioEngine {
    constructor(...args) {
      super(...args);
      engines.add(this);
    }

    async resume() {
      try {
        return await super.resume();
      } catch (error) {
        // iOS can revoke the transient user activation while a file picker is
        // closing. Decoding audio is still legal on a suspended AudioContext,
        // so do not reject an import merely because output is not unlocked yet.
        // The next real tap resumes the exact same context below.
        const message = String(error?.message || error || '');
        if (this.context?.state === 'suspended' && /audio could not start|not.?allowed|user gesture|interaction/i.test(message)) {
          this.sync();
          return this.context;
        }
        throw error;
      }
    }
  }

  api.AudioEngine = MobileSafeAudioEngine;

  // Safari requires resume() to happen synchronously from a user gesture.
  // Run in capture phase so the context is unlocked before pad, preview,
  // transport, sampler, or file-input handlers perform asynchronous work.
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
