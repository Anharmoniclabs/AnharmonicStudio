/* Anharmonic Studio service worker: offline app shell for the installable web studio.
 *
 * Bump VERSION whenever any precached file changes. The new worker installs a
 * fresh cache beside the old one and only takes over when every client has
 * reloaded, or immediately when the page asks after the user chooses Reload,
 * so a half-updated shell never runs. Project data lives in IndexedDB and
 * localStorage, never in this cache. */
'use strict';

const VERSION = '2026-10-02-3';
const CACHE = 'anharmonic-studio-shell-' + VERSION;
const SHELL = [
  './',
  'index.html',
  'studio.html',
  'studio.css',
  'mobile.css',
  'compatibility.css',
  'project-model.js',
  'audio-engine.js',
  'waveform-view.js',
  'instrument-dsp.js',
  'instrument-bank.js',
  'instrument-worklet.js',
  'factory-kits.js',
  'recorder.js',
  'recorder-worklet.js',
  'vocal-dsp.js',
  'vocal-worker.js',
  'autotune-worklet.js',
  'mobile-compat.js',
  'pwa.js',
  'studio.js',
  'manifest.webmanifest',
  '../assets/mark.svg',
  '../assets/apple-touch-icon.png',
  '../assets/pwa/icon-192.png',
  '../assets/pwa/icon-512.png',
  '../assets/pwa/icon-maskable-512.png'
];
const shellURLs = new Set(SHELL.map(path => new URL(path, self.registration.scope).href));

self.addEventListener('install', event => {
  event.waitUntil((async () => {
    const cache = await caches.open(CACHE);
    // Bypass the HTTP cache so a new VERSION always fetches the matching files.
    await cache.addAll(SHELL.map(path => new Request(path, { cache: 'reload' })));
  })());
});

self.addEventListener('activate', event => {
  event.waitUntil((async () => {
    const names = await caches.keys();
    await Promise.all(names.filter(name => name.startsWith('anharmonic-studio-shell-') && name !== CACHE).map(name => caches.delete(name)));
    await self.clients.claim();
    const clients = await self.clients.matchAll({ type: 'window' });
    clients.forEach(client => client.postMessage({ type: 'anharmonic-sw-activated', version: VERSION }));
  })());
});

self.addEventListener('message', event => {
  if (event.data?.type === 'anharmonic-sw-skip-waiting') self.skipWaiting();
  if (event.data?.type === 'anharmonic-sw-version') event.source?.postMessage({ type: 'anharmonic-sw-version', version: VERSION });
});

self.addEventListener('fetch', event => {
  const request = event.request;
  if (request.method !== 'GET') return;
  const url = new URL(request.url);
  if (url.origin !== self.location.origin) return;
  // Strip the start_url/shortcut query so installed launches hit the cached page.
  const key = shellURLs.has(url.origin + url.pathname) ? url.origin + url.pathname : null;
  if (!key) return;
  event.respondWith((async () => {
    const cache = await caches.open(CACHE);
    const cached = await cache.match(key);
    if (cached) return cached;
    try {
      const response = await fetch(request);
      if (response.ok) cache.put(key, response.clone());
      return response;
    } catch (error) {
      if (request.mode === 'navigate') {
        const fallback = await cache.match(new URL('studio.html', self.registration.scope).href);
        if (fallback) return fallback;
      }
      throw error;
    }
  })());
});
