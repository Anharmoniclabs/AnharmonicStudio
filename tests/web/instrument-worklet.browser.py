#!/usr/bin/env python3
"""Check the Native/Prism AudioWorklet against the verified DSP code in Chromium.

instrument-dsp.test.cjs proves the JavaScript voice engine matches the desktop
C++ engine. This test proves the AudioWorklet host applies note events at the
exact sample frame: an OfflineAudioContext render must equal the same DSP run
directly in the page. Only a new headless browser is launched and closed.
"""

import argparse
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import threading

from playwright.sync_api import sync_playwright


class Handler(SimpleHTTPRequestHandler):
    def log_message(self, *_args):
        pass


SCRIPT = r"""async () => {
  const results = [];
  const check = (name, condition, detail = '') => results.push({ name, passed: Boolean(condition), detail });
  const { NativeInstrument, PrismProcessor, prismDefaults, prismLoadSound } = AnharmonicDSP;
  const frames = 48000;
  const events = [
    { type: 'on', note: 60, velocity: .8, id: 1, frame: 0 },
    { type: 'on', note: 67, velocity: .6, id: 2, frame: 5000 },
    { type: 'off', note: 60, id: 1, frame: 12345 },
    { type: 'off', note: 67, id: 2, frame: 30000 }
  ];
  async function worklet(options) {
    const context = new OfflineAudioContext(2, frames, 48000);
    await context.audioWorklet.addModule('instrument-worklet.js');
    const node = new AudioWorkletNode(context, 'anharmonic-instrument', { numberOfInputs: 0, numberOfOutputs: 1, outputChannelCount: [2], processorOptions: { ...options, events } });
    node.connect(context.destination);
    const buffer = await context.startRendering();
    return [buffer.getChannelData(0), buffer.getChannelData(1)];
  }
  function direct(instrument) {
    const left = new Float32Array(frames), right = new Float32Array(frames);
    const queue = [...events];
    for (let start = 0; start < frames; start += 128) {
      const l = new Float32Array(128), r = new Float32Array(128);
      instrument.readPatch(); let at = 0;
      while (queue.length && queue[0].frame < start + 128) {
        const event = queue.shift(), offset = Math.max(0, event.frame - start);
        if (offset > at) { instrument.render(l, r, offset - at, at); at = offset; }
        if (event.type === 'on') instrument.noteOn(event.note, event.velocity, 1, event.id);
        else if (instrument instanceof PrismProcessor) instrument.noteOff(event.note); else instrument.release(event.id, event.note, 0);
      }
      if (at < 128) instrument.render(l, r, 128 - at, at);
      instrument.effects(l, r, 128);
      left.set(l, start); right.set(r, start);
    }
    return [left, right];
  }
  const compare = (a, b) => { let worst = 0, peak = 0; for (let c = 0; c < 2; c++) for (let i = 0; i < frames; i++) { worst = Math.max(worst, Math.abs(a[c][i] - b[c][i])); peak = Math.max(peak, Math.abs(a[c][i])); } return { worst, peak }; };
  const patch = { osc1: 'square', osc2: 'saw', cutoff: 1200, resonance: .6, release: .2, volume: .5 };
  const native = compare(await worklet({ kind: 'native', patch }), direct(new NativeInstrument(48000, patch)));
  check('native worklet matches the verified DSP sample for sample', native.worst < 1e-6 && native.peak > .05, JSON.stringify(native));
  const silentBefore = (await worklet({ kind: 'native', patch }))[0].slice(0, 1).every(value => value === 0);
  check('notes start exactly at their frame', silentBefore);
  const values = Array.from(prismLoadSound(prismDefaults(), AnharmonicInstrumentBank.performances.find(item => item.effects && item.effects.space > 0) || AnharmonicInstrumentBank.performances[0]));
  const prismDirect = new PrismProcessor(48000, values); prismDirect.tempo = 120;
  const prism = compare(await worklet({ kind: 'prism', values, bpm: 120 }), direct(prismDirect));
  check('prism worklet with layers and effects matches the verified DSP', prism.worst < 1e-6 && prism.peak > .01, JSON.stringify(prism));
  const arpValues = prismDefaults(); arpValues[21] = 1;
  const arp = await worklet({ kind: 'prism', values: Array.from(arpValues), bpm: 150 });
  let onsets = 0, quiet = true; for (let i = 0; i < 12345; i++) { const loud = Math.abs(arp[0][i]) > .01; if (loud && quiet) onsets++; quiet = !loud && Math.abs(arp[0][i]) < .001 ? true : quiet && !loud; }
  check('prism arp clocks notes from the project tempo', onsets >= 2, 'onsets ' + onsets);
  return results;
}"""


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--browser", type=Path)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[2] / "website"
    server = ThreadingHTTPServer(("127.0.0.1", 0), partial(Handler, directory=str(root)))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(
                headless=True, **({"executable_path": str(args.browser)} if args.browser else {})
            )
            try:
                page = browser.new_page()
                errors = []
                page.on("pageerror", lambda error: errors.append(str(error)))
                page.goto(f"http://127.0.0.1:{server.server_port}/app/studio.html")
                page.wait_for_selector("#pad-grid .pad")
                results = page.evaluate(SCRIPT)
                results.append(
                    {"name": "no page errors", "passed": not errors, "detail": errors[:3]}
                )
            finally:
                browser.close()
    finally:
        server.shutdown()
        server.server_close()
    print(json.dumps(results, indent=2))
    return 0 if all(item["passed"] for item in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
