#!/usr/bin/env python3
"""Regress held-note cleanup through the real studio keyboard handlers.

Audio is replaced by a voice spy so startup completion can be controlled exactly.
A separate headless browser is used; no user application is controlled.
"""

import argparse
import json
from pathlib import Path
from urllib.parse import urlparse

from playwright.sync_api import sync_playwright


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--browser", type=Path)
    parser.add_argument(
        "--root", type=Path, default=Path(__file__).resolve().parents[2], help="Checkout to test"
    )
    args = parser.parse_args()
    root = args.root.resolve() / "website"
    results = []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(
            headless=True, **({"executable_path": str(args.browser)} if args.browser else {})
        )
        try:
            for workspace in ("notes", "instruments"):
                for scenario in ("blur", "release-during-startup"):
                    context = browser.new_context(viewport={"width": 1600, "height": 1000})

                    def serve(route):
                        path = root / urlparse(route.request.url).path.lstrip("/")
                        if path.is_file() and path.resolve().is_relative_to(root):
                            route.fulfill(path=str(path))
                        else:
                            route.fulfill(status=404, body="")

                    context.route("http://studio.test/**", serve)
                    page = context.new_page()
                    page.goto("http://studio.test/app/studio.html")
                    page.wait_for_selector("#pad-grid .pad")
                    page.evaluate("""() => {
                      window.voiceLog = []; window.activeNotes = new Set();
                      const A = AnharmonicAudio.AudioEngine.prototype;
                      A.resume = async function() {
                        this.context = {};
                        await new Promise(resolve => window.finishStartup = resolve);
                      };
                      A.sync = function() {}; A.meter = function() { return {}; };
                      A.setMetronome = function() {};
                      A.triggerNote = function(pitch) { voiceLog.push(['start', pitch]); activeNotes.add(pitch); };
                      A.releaseNote = function(pitch) { voiceLog.push(['release', pitch]); activeNotes.delete(pitch); };
                      A.releasePad = function(pad) { voiceLog.push(['releasePad', pad]); };
                    }""")
                    page.locator(f'[data-workspace="{workspace}"]').click()
                    page.keyboard.down("q")
                    page.wait_for_function('typeof finishStartup === "function"')
                    if scenario == "blur":
                        page.evaluate("finishStartup()")
                        page.wait_for_function("activeNotes.has(60)")
                        page.evaluate("window.dispatchEvent(new Event('blur'))")
                    else:
                        page.keyboard.up("q")
                        page.evaluate("finishStartup()")
                        # Flush async ensureAudio continuations and browser rendering.
                        page.evaluate(
                            "() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))"
                        )
                    active = page.evaluate("[...activeNotes]")
                    results.append(
                        {
                            "name": f"{workspace}: {scenario}",
                            "passed": not active,
                            "active_notes": active,
                            "events": page.evaluate("voiceLog"),
                        }
                    )
                    context.close()
        finally:
            browser.close()
    print(json.dumps(results, indent=2))
    if not all(result["passed"] for result in results):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
