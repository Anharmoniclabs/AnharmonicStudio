const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const source = fs.readFileSync(path.join(__dirname, '../../website/app/mobile-compat.js'), 'utf8');

function makeSandbox({ resumeRejects = false } = {}) {
  const listeners = new Map();
  const crypto = require('node:crypto').webcrypto;
  const compatCrypto = {
    getRandomValues: crypto.getRandomValues.bind(crypto),
  };

  class BaseAudioEngine {
    constructor() {
      this.syncCalls = 0;
      this.context = {
        state: 'suspended',
        resumeCalls: 0,
        resume: async () => {
          this.context.resumeCalls += 1;
          if (resumeRejects) throw new Error('Audio could not start. Enable audio for this site and try again.');
          this.context.state = 'running';
        },
      };
    }

    async resume() {
      await this.context.resume();
      if (this.context.state !== 'running') {
        throw new Error('Audio could not start. Enable audio for this site and try again.');
      }
      this.sync();
      return this.context;
    }

    sync() {
      this.syncCalls += 1;
    }
  }

  const document = {
    hidden: false,
    addEventListener(name, callback, options) {
      listeners.set(name, { callback, options });
    },
  };

  const audioApi = Object.freeze({ AudioEngine: BaseAudioEngine });
  const window = {
    crypto: compatCrypto,
    AnharmonicAudio: audioApi,
  };

  const sandbox = { window, document, crypto: compatCrypto, console, Uint8Array };
  vm.runInNewContext(source, sandbox);
  return { sandbox, listeners, BaseAudioEngine };
}

test('mobile compatibility supplies RFC4122-shaped UUIDs when randomUUID is unavailable', () => {
  const { sandbox } = makeSandbox();
  assert.equal(typeof sandbox.window.crypto.randomUUID, 'function');
  const id = sandbox.window.crypto.randomUUID();
  assert.match(id, /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/);
});

test('mobile compatibility preserves the frozen AnharmonicAudio export', () => {
  const { sandbox, BaseAudioEngine } = makeSandbox();
  assert.equal(sandbox.window.AnharmonicAudio.AudioEngine, BaseAudioEngine);
  assert.equal(Object.isFrozen(sandbox.window.AnharmonicAudio), true);
  assert.equal(BaseAudioEngine.prototype.__anharmonicMobileCompat, true);
});

test('suspended Safari-style resume failure does not abort import/decode setup', async () => {
  const { sandbox } = makeSandbox({ resumeRejects: true });
  const Engine = sandbox.window.AnharmonicAudio.AudioEngine;
  const engine = new Engine();
  const context = await engine.resume();
  assert.equal(context.state, 'suspended');
  assert.equal(engine.syncCalls, 1);
});

test('capture-phase user gestures retry the exact known suspended audio context', async () => {
  const { sandbox, listeners } = makeSandbox();
  const Engine = sandbox.window.AnharmonicAudio.AudioEngine;
  const engine = new Engine();

  assert.equal(listeners.get('pointerdown').options.capture, true);
  assert.equal(listeners.get('touchstart').options.capture, true);

  await engine.resume();
  assert.equal(engine.context.state, 'running');
  engine.context.state = 'suspended';
  const calls = engine.context.resumeCalls;

  listeners.get('pointerdown').callback();
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(engine.context.resumeCalls, calls + 1);
  assert.equal(engine.context.state, 'running');
});

test('visible-page transition retries known suspended audio after returning from a picker', async () => {
  const { sandbox, listeners } = makeSandbox();
  const Engine = sandbox.window.AnharmonicAudio.AudioEngine;
  const engine = new Engine();

  await engine.resume();
  engine.context.state = 'suspended';
  const calls = engine.context.resumeCalls;
  listeners.get('visibilitychange').callback();
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(engine.context.resumeCalls, calls + 1);

  engine.context.state = 'suspended';
  sandbox.document.hidden = true;
  listeners.get('visibilitychange').callback();
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(engine.context.resumeCalls, calls + 1);
});
