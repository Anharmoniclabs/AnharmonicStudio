#!/usr/bin/env python3
"""Inspect the web DAW in a new headless browser; never control desktop windows.

Install playwright in a separate test environment. Reports are local evidence,
not a certification of desktop parity. No microphone permission is requested.
"""

import argparse
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import threading


def main():
    from playwright.sync_api import sync_playwright

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--browser", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    root = Path(__file__).resolve().parent.parent / "website"
    args.output.mkdir(parents=True, exist_ok=True)

    class Handler(SimpleHTTPRequestHandler):
        def log_message(self, *_args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), partial(Handler, directory=str(root)))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    report = {"workspaces": {}, "browser_errors": [], "checks": {}}
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(
                headless=True,
                **({"executable_path": str(args.browser)} if args.browser else {}),
            )
            try:
                page = browser.new_page(viewport={"width": 1920, "height": 1080})
                page.on("pageerror", lambda error: report["browser_errors"].append(str(error)))
                page.add_init_script("""(() => {
                    window.auditOscillators = [];
                    const original = AudioContext.prototype.createOscillator;
                    AudioContext.prototype.createOscillator = function (...args) {
                        const oscillator = original.apply(this, args);
                        window.auditOscillators.push(oscillator);
                        return oscillator;
                    };
                })();""")
                page.goto(f"http://127.0.0.1:{server.server_port}/app/studio.html")
                page.screenshot(path=str(args.output / "web-studio.png"), full_page=True)
                for workspace in (
                    "song",
                    "beats",
                    "notes",
                    "sampler",
                    "instruments",
                    "autotune",
                    "mix",
                ):
                    page.locator(f'[data-workspace="{workspace}"]').click()
                    report["workspaces"][workspace] = page.locator(
                        "#stage button"
                    ).all_text_contents()
                page.locator('[data-workspace="instruments"]').click()
                page.locator(".synth-preview").click()
                page.wait_for_timeout(800)
                report["checks"]["synth_preview_no_browser_error"] = not report["browser_errors"]
                report["checks"]["desktop_saw_patch_uses_sawtooth"] = page.evaluate("""() =>
                    auditOscillators.length === 2 && auditOscillators[0].type === 'sawtooth'
                    && auditOscillators[1].type === 'square'
                """)
                report["checks"]["future_project_rejected"] = page.evaluate("""() => {
                    try { new AnharmonicProject.ProjectStore({format_version: 999}); return false; }
                    catch { return true; }
                }""")
                report["checks"]["zero_clip_gain_preserved"] = page.evaluate("""() => {
                    const store = new AnharmonicProject.ProjectStore({rows: [{clips: [{gain: 0}]}]});
                    return store.project.rows[0].clips[0].gain === 0;
                }""")
            finally:
                browser.close()  # Only the headless browser launched above.
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
    (args.output / "report.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report["checks"], indent=2))
    return 0 if all(report["checks"].values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
