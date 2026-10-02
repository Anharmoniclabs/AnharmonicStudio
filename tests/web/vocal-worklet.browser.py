#!/usr/bin/env python3
"""Check the live vocal monitor worklet and the vocal render worker in Chromium.

vocal-dsp.test.cjs proves the offline Autotune matches the desktop. This test
proves the browser hosts work: the AudioWorklet tunes a flat voice onto the
scale, passes dry audio untouched, reports tuner readings, and the Web Worker
returns the same render as the verified DSP code. Only a new headless browser
is launched and closed.
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
  const flat = 220 * Math.pow(2, -0.35 / 12), seconds = 3;
  const settings = { ...AnharmonicVocal.DEFAULTS, key: 'A', scale: 'minor', strength: 1, retune_ms: 0, humanize: 0 };
  async function monitor(mode) {
    const context = new OfflineAudioContext(2, 48000 * seconds, 48000);
    await context.audioWorklet.addModule('autotune-worklet.js');
    const node = new AudioWorkletNode(context, 'anharmonic-autotune', { numberOfInputs: 1, numberOfOutputs: 1, outputChannelCount: [2], channelCount: 1, channelCountMode: 'explicit', processorOptions: { settings, mode } });
    const readings = []; node.port.onmessage = event => readings.push(event.data);
    const osc = context.createOscillator(), gain = context.createGain(); osc.frequency.value = flat; gain.gain.value = .4;
    osc.connect(gain).connect(node).connect(context.destination); osc.start();
    const buffer = await context.startRendering();
    await new Promise(resolve => setTimeout(resolve, 50));
    return { left: buffer.getChannelData(0), right: buffer.getChannelData(1), readings };
  }
  // Average frequency from interpolated upward zero crossings over the last two seconds (longer than one crossfade cycle of the shifter).
  const frequency = data => { const start = data.length - 96000; let first = null, last = null, count = 0;
    for (let i = start + 1; i < data.length; i++) if (data[i - 1] < 0 && data[i] >= 0) { const t = i - 1 + data[i - 1] / (data[i - 1] - data[i]); if (first === null) first = t; else count++; last = t; }
    return count * 48000 / (last - first); };
  const cents = (a, b) => 1200 * Math.log2(a / b);
  const tuned = await monitor('tuned'), heard = frequency(tuned.left);
  check('tuned monitoring moves a flat A3 onto pitch', Math.abs(cents(heard, 220)) < 1 && Math.abs(cents(frequency(new Float32Array(48000).map((_, i) => Math.sin(2 * Math.PI * flat * i / 48000))), flat)) < .5, heard.toFixed(2) + ' Hz');
  check('tuned monitoring is stereo and bounded', tuned.left.every((value, index) => value === tuned.right[index] && Math.abs(value) < .5));
  const dry = await monitor('dry'); let difference = 0;
  for (let i = 0; i < dry.left.length; i++) difference = Math.max(difference, Math.abs(dry.left[i] - .4 * Math.sin(2 * Math.PI * flat * i / 48000)));
  check('dry monitoring passes the voice unchanged', difference < 2e-3, String(difference));
  const silent = await monitor('off'), reading = silent.readings.filter(item => item.hz > 0).at(-1);
  check('monitor off is silent but the tuner keeps reading', silent.left.every(value => value === 0) && reading && Math.abs(reading.midi - 56.65) < .05 && reading.target === 57, JSON.stringify(reading));
  // The worker returns the same render as calling the DSP directly.
  const voice = Float32Array.from({ length: 48000 }, (_, i) => .3 * Math.sin(2 * Math.PI * flat * i / 48000) + .1 * Math.sin(4 * Math.PI * flat * i / 48000));
  const direct = AnharmonicVocal.renderAutotune([voice], settings, 48000);
  const worker = new Worker('vocal-worker.js'); let progress = 0;
  const result = await new Promise((resolve, reject) => { worker.onmessage = event => { if (event.data.type === 'progress') progress++; else if (event.data.type === 'done') resolve(event.data); else reject(new Error(event.data.message)); }; worker.postMessage({ id: 1, op: 'render', channels: [voice], settings, sampleRate: 48000 }); });
  worker.terminate();
  let worst = 0; for (let c = 0; c < 2; c++) for (let i = 0; i < voice.length; i++) worst = Math.max(worst, Math.abs(result.channels[c][i] - direct.channels[c][i]));
  check('the render worker matches the verified DSP and reports progress', worst === 0 && progress > 0 && result.key.key === 'A', JSON.stringify({ worst, progress, key: result.key }));
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
                page.wait_for_selector("#pad-grid .pad", state="attached")
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
