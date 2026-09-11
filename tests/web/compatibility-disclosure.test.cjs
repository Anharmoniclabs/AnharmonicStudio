const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const root = path.join(__dirname, '../../website/app');
const html = fs.readFileSync(path.join(root, 'studio.html'), 'utf8');
const css = fs.readFileSync(path.join(root, 'compatibility.css'), 'utf8');

test('web studio keeps the desktop-processing boundary visible in normal transport UI', () => {
  assert.match(html, /class="engine-boundary"/);
  assert.match(html, /NO DESKTOP PLUGINS \/ NATIVE DSP/);
  assert.match(html, /cannot run native desktop plugins, device routing, or native-only DSP/);
});

test('compatibility disclosure stylesheet is loaded after layout styles', () => {
  const mobile = html.indexOf('href="mobile.css"');
  const compatibility = html.indexOf('href="compatibility.css"');
  assert.ok(mobile >= 0 && compatibility > mobile);
});

test('compatibility badge remains a fixed-size reachable transport item on mobile', () => {
  assert.match(css, /\.engine-boundary\{[^}]*flex:0 0 auto/);
  assert.match(css, /@media\(max-width:760px\).*\.engine-boundary\{[^}]*min-height:40px/s);
});
