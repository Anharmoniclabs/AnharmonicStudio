/* Phone viewport and progressive landscape enhancement. Audio and project
 * state are deliberately untouched when resizing, rotating or entering fullscreen. */
(() => {
  'use strict';
  const root = document.documentElement;
  const mobile = matchMedia('(max-width:760px), (max-width:1100px) and (max-height:600px) and (pointer:coarse)');
  const portrait = matchMedia('(orientation:portrait)');
  const installed = () => navigator.standalone === true || matchMedia('(display-mode:standalone)').matches;
  let frame = 0, dismissed = false, automaticAttempted = false;
  const hint = document.createElement('aside');
  hint.className = 'orientation-hint';
  hint.setAttribute('aria-label', 'Landscape view');
  const text = document.createElement('span');
  text.textContent = 'More room sideways. Rotate your phone for landscape.';
  const dismiss = document.createElement('button');
  dismiss.type = 'button'; dismiss.textContent = '×';
  dismiss.setAttribute('aria-label', 'Dismiss landscape tip');
  hint.append(text, dismiss); document.body.append(hint);
  dismiss.addEventListener('click', () => { dismissed = true; sync(); });
  function sync() {
    frame = 0;
    // VisualViewport follows the Safari address bar and on-screen keyboard.
    // Do not disable browser zoom: zooming text should remain available.
    const viewport = window.visualViewport;
    const height = viewport && viewport.scale === 1 ? viewport.height : window.innerHeight;
    root.style.setProperty('--studio-height', Math.round(height) + 'px');
    root.dataset.phone = String(mobile.matches);
    root.dataset.orientation = portrait.matches ? 'portrait' : 'landscape';
    hint.hidden = !mobile.matches || !portrait.matches || dismissed;
    const button = document.querySelector('#landscape-mode');
    if (button) button.textContent = document.fullscreenElement ? 'Exit full screen' : 'Landscape / full screen';
  }
  const schedule = () => { if (!frame) frame = requestAnimationFrame(sync); };
  window.addEventListener('resize', schedule, { passive: true });
  window.addEventListener('orientationchange', schedule, { passive: true });
  window.visualViewport?.addEventListener('resize', schedule, { passive: true });
  document.addEventListener('fullscreenchange', schedule);
  mobile.addEventListener?.('change', schedule);
  portrait.addEventListener?.('change', schedule);
  const report = message => document.dispatchEvent(new CustomEvent('anharmonic-status', { detail: message }));
  async function lockLandscape() {
    if (typeof screen.orientation?.lock !== 'function') return false;
    try { await screen.orientation.lock('landscape'); return true; }
    catch { return false; } // Safari/OS policy may decline; portrait stays usable.
  }
  document.querySelector('#landscape-mode')?.addEventListener('click', async () => {
    document.querySelector('#transport-sheet')?.close();
    if (document.fullscreenElement) {
      try { await document.exitFullscreen(); screen.orientation?.unlock?.(); } catch { /* remain usable */ }
      return;
    }
    // Fullscreen must be requested directly from the tap, before an await.
    try { if (root.requestFullscreen) await root.requestFullscreen(); } catch { /* e.g. iPhone Safari */ }
    const locked = await lockLandscape();
    if (!locked && portrait.matches) {
      dismissed = false; sync();
      report('Turn your phone sideways. If it stays in portrait, turn off Portrait Orientation Lock in Control Center.');
    }
  });
  // Installed browsers which permit orientation locking get one best-effort
  // automatic request on the first real tap. Never force fullscreen on launch.
  document.addEventListener('pointerdown', () => {
    if (automaticAttempted || !mobile.matches || !installed()) return;
    automaticAttempted = true; void lockLandscape();
  }, { passive: true });
  sync();
})();
