const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const root = path.join(__dirname, '../../website/app');
const html = fs.readFileSync(path.join(root, 'studio.html'), 'utf8');
const css = fs.readFileSync(path.join(root, 'mobile.css'), 'utf8');

test('mobile hardening stylesheet is loaded after base studio styles', () => {
  const base = html.indexOf('href="studio.css"');
  const mobile = html.indexOf('href="mobile.css"');
  assert.ok(base >= 0 && mobile > base, 'mobile.css must override studio.css');
});

test('mobile project actions scroll instead of being clipped', () => {
  assert.match(css, /\.project-bar\s*\{[\s\S]*overflow-x:\s*auto/);
  assert.match(css, /\.project-actions\s*\{[\s\S]*min-width:\s*max-content/);
});

test('mobile layout accounts for dynamic viewport and device safe areas', () => {
  assert.match(css, /100dvh/);
  assert.match(css, /env\(safe-area-inset-left\)/);
  assert.match(css, /env\(safe-area-inset-right\)/);
  assert.match(css, /env\(safe-area-inset-bottom\)/);
});

test('mobile side panels are bounded and independently scrollable', () => {
  assert.match(css, /\.browser-panel\.open,[\s\S]*\.pads-panel\.open\s*\{[\s\S]*max-height:/);
  assert.match(css, /overscroll-behavior:\s*contain/);
  assert.match(css, /-webkit-overflow-scrolling:\s*touch/);
});

test('primary mobile controls keep usable touch height', () => {
  assert.match(css, /\.project-actions button,[\s\S]*\.pad-bank button\s*\{[\s\S]*min-height:\s*40px/);
});
