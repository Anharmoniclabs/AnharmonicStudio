const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const app = path.join(__dirname, '../../website/app');
const html = fs.readFileSync(path.join(app, 'studio.html'), 'utf8');
const manifest = JSON.parse(fs.readFileSync(path.join(app, 'manifest.webmanifest'), 'utf8'));
const worker = fs.readFileSync(path.join(app, 'sw.js'), 'utf8');

test('studio links its manifest and opts into home-screen app mode on iOS and Android', () => {
  assert.match(html, /<link rel="manifest" href="manifest.webmanifest">/);
  assert.match(html, /name="apple-mobile-web-app-capable" content="yes"/);
  assert.match(html, /rel="apple-touch-icon"/);
  assert.match(html, /<script src="pwa.js" defer><\/script>/);
});

test('manifest opens the studio standalone with installable icons', () => {
  assert.equal(manifest.display, 'standalone');
  assert.equal(manifest.scope, './');
  assert.match(manifest.start_url, /^\.\/studio\.html/);
  const sizes = manifest.icons.map(icon => icon.sizes);
  assert.ok(sizes.includes('192x192') && sizes.includes('512x512'));
  assert.ok(manifest.icons.some(icon => icon.purpose === 'maskable'));
  for (const icon of manifest.icons) assert.ok(fs.existsSync(path.join(app, icon.src)), icon.src);
});

test('service worker precaches every studio script and stylesheet the page loads', () => {
  const loaded = [...html.matchAll(/(?:src|href)="([^"]+\.(?:js|css))"/g)].map(match => match[1]);
  assert.ok(loaded.length >= 8);
  for (const file of loaded) assert.ok(worker.includes(`'${file}'`), `${file} is not precached`);
  assert.ok(worker.includes("'recorder-worklet.js'"), 'the recorder worklet must work offline');
});

test('service worker never caches non-GET or cross-origin requests and versions its cache', () => {
  assert.match(worker, /request\.method !== 'GET'/);
  assert.match(worker, /url\.origin !== self\.location\.origin/);
  assert.match(worker, /const VERSION = '[^']+'/);
  assert.match(worker, /caches\.delete/);
});

test('updates wait for the user instead of reloading mid-recording', () => {
  const pwa = fs.readFileSync(path.join(app, 'pwa.js'), 'utf8');
  assert.doesNotMatch(worker, /self\.skipWaiting\(\);\s*\}\);\s*$/m, 'install must not skip waiting unconditionally');
  assert.match(worker, /anharmonic-sw-skip-waiting/);
  assert.match(pwa, /reloadRequested/);
});
