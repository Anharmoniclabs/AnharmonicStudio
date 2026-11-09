/* Preserve published Studio bookmarks after the Labs home takes over the root.
 * Relative resolution also supports GitHub Pages at /AnharmonicStudio/.
 * The homepage itself works without JavaScript.
 */
(() => {
  'use strict';
  const studioAnchors = new Set([
    'hero-title', 'film', 'commercial', 'film-play', 'film-description', 'video-status',
    'studio', 'studio-title', 'tab-sample', 'tab-beats', 'tab-synth', 'tab-notes',
    'tab-song', 'tab-mix', 'workspace-panel', 'workspace-image', 'workspace-kicker',
    'workspace-title', 'workspace-copy', 'workspace-count', 'showcase', 'showcase-title',
    'details-title', 'latest-updates', 'updates-title', 'download', 'pricing-title',
    'release-status', 'release-status-title', 'release-status-copy', 'platform-detail',
    'installer-notice-title', 'purchase-install-note', 'support', 'donation-status',
    'open-source', 'source-title', 'faq-title', 'availability-answer',
    'installation-guidance', 'privacy', 'download-dialog', 'dialog-kicker',
    'download-dialog-title', 'dialog-copy'
  ]);

  function forwardStudioBookmark() {
    let fragment;
    try { fragment = decodeURIComponent(window.location.hash.slice(1)); }
    catch { return; }
    if (!studioAnchors.has(fragment)) return;
    const destination = new URL('studio.html', window.location.href);
    destination.search = window.location.search;
    destination.hash = window.location.hash;
    window.location.replace(destination.href);
  }

  forwardStudioBookmark();
  window.addEventListener('hashchange', forwardStudioBookmark);
})();
