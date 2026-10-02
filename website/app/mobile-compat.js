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

  // iPhone and iPad: Web Audio is "ambient" by default, so the ring/silent switch
  // mutes the whole studio. Declare a media playback session (Safari 17+), or on
  // older iOS keep a silent media element playing, which moves the page into the
  // playback category. While the microphone is open the session must allow
  // recording, so getUserMedia switches it to play-and-record until every track ends.
  const nav = window.navigator || {};
  const iOS = /iPad|iPhone|iPod/.test(nav.userAgent || '') || (nav.platform === 'MacIntel' && nav.maxTouchPoints > 1);
  const session = nav.audioSession;
  let openTracks = 0, silentMedia = null;
  const setSession = type => { try { if (session && session.type !== type) session.type = type; } catch { /* Unsupported type on this engine. */ } };
  setSession('playback');
  function silentWav() {
    const rate = 8000, frames = rate / 2, bytes = new DataView(new ArrayBuffer(44 + frames * 2));
    const text = (offset, value) => [...value].forEach((character, index) => bytes.setUint8(offset + index, character.charCodeAt(0)));
    text(0, 'RIFF'); bytes.setUint32(4, 36 + frames * 2, true); text(8, 'WAVE'); text(12, 'fmt '); bytes.setUint32(16, 16, true);
    bytes.setUint16(20, 1, true); bytes.setUint16(22, 1, true); bytes.setUint32(24, rate, true); bytes.setUint32(28, rate * 2, true);
    bytes.setUint16(32, 2, true); bytes.setUint16(34, 16, true); text(36, 'data'); bytes.setUint32(40, frames * 2, true);
    return URL.createObjectURL(new Blob([bytes], { type: 'audio/wav' }));
  }
  function keepMediaSession() {
    if (!iOS || session || silentMedia) return;
    try {
      silentMedia = document.createElement('audio');
      silentMedia.src = silentWav(); silentMedia.loop = true; silentMedia.setAttribute('playsinline', ''); silentMedia.setAttribute('aria-hidden', 'true');
      const pending = silentMedia.play(); if (pending?.catch) pending.catch(() => { silentMedia = null; });
    } catch { silentMedia = null; }
  }
  const mediaDevices = nav.mediaDevices;
  if (mediaDevices?.getUserMedia && !mediaDevices.__anharmonicSession) {
    const getUserMedia = mediaDevices.getUserMedia.bind(mediaDevices);
    Object.defineProperty(mediaDevices, '__anharmonicSession', { value: true });
    mediaDevices.getUserMedia = async constraints => {
      setSession('play-and-record');
      let stream;
      try { stream = await getUserMedia(constraints); } catch (error) { if (!openTracks) setSession('playback'); throw error; }
      for (const track of stream.getAudioTracks()) {
        openTracks += 1; let open = true;
        const close = () => { if (!open) return; open = false; openTracks = Math.max(0, openTracks - 1); if (!openTracks) setSession('playback'); };
        const stop = track.stop.bind(track);
        track.stop = () => { stop(); close(); };
        track.addEventListener('ended', close);
      }
      // Opening the microphone can interrupt the audio engine on iOS; bring it back.
      for (const engine of engines) {
        const context = engine.context;
        if (context && context.state !== 'running' && context.state !== 'closed') { try { await context.resume(); } catch { /* the next tap resumes it */ } }
      }
      return stream;
    };
  }

  // Safari requires resume() to happen synchronously from a user gesture.
  // Run in capture phase so a previously-created suspended (or, on iOS, an
  // interrupted) context is unlocked before pad, preview, transport, sampler,
  // or file-input handlers run.
  const unlockAudio = () => {
    keepMediaSession();
    for (const engine of engines) {
      const context = engine.context;
      if (!context || context.state === 'running' || context.state === 'closed') continue;
      try {
        const pending = context.resume();
        if (pending?.catch) pending.catch(() => {});
      } catch { /* A later user gesture gets another chance. */ }
    }
  };

  document.addEventListener('pointerdown', unlockAudio, { capture: true, passive: true });
  // Older iOS only honours resume() from the end of a touch or a click.
  document.addEventListener('touchend', unlockAudio, { capture: true, passive: true });
  document.addEventListener('click', unlockAudio, { capture: true });
  document.addEventListener('touchstart', unlockAudio, { capture: true, passive: true });
  document.addEventListener('keydown', unlockAudio, { capture: true });
  document.addEventListener('visibilitychange', () => {
    if (!document.hidden) unlockAudio();
  });
})();
