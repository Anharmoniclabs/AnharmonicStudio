"""Exercise phone orientation, workspace reachability and offline subpath launches.

Emulated browser viewports are evidence of web layout, not physical iPhone certification.
"""

import argparse
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import threading

from playwright.sync_api import sync_playwright


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--browser", type=Path)
    parser.add_argument("--engine", choices=("chromium", "webkit"), default="chromium")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    root = Path(__file__).resolve().parents[2] / "website"

    class Handler(SimpleHTTPRequestHandler):
        def translate_path(self, path):
            if path.startswith("/AnharmonicStudio/"):
                path = path.removeprefix("/AnharmonicStudio")
            return super().translate_path(path)

        def log_message(self, *_args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), partial(Handler, directory=str(root)))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    origin = f"http://127.0.0.1:{server.server_port}"
    report = {"engine": args.engine, "viewports": [], "offline": [], "errors": []}
    try:
        with sync_playwright() as pw:
            browser = getattr(pw, args.engine).launch(
                **({"executable_path": str(args.browser)} if args.browser else {})
            )
            for width, height, touch in [
                (320, 568, True),
                (390, 844, True),
                (430, 932, True),
                (844, 390, True),
                (932, 430, True),
                (1024, 768, True),
                (1440, 900, False),
            ]:
                context = browser.new_context(
                    viewport={"width": width, "height": height}, has_touch=touch
                )
                page = context.new_page()
                page.on("pageerror", lambda error: report["errors"].append(str(error)))
                page.goto(origin + "/app/studio.html")
                page.wait_for_selector("#pad-grid .pad", state="attached")
                phone = width <= 760 or (touch and width <= 1100 and height <= 600)
                assert page.evaluate("document.documentElement.dataset.phone") == str(phone).lower()
                if phone:
                    for tab in [
                        "song",
                        "beats",
                        "notes",
                        "instruments",
                        "vocal",
                        "pads",
                        "sampler",
                        "mix",
                    ]:
                        page.locator(f'[data-mobile-tab="{tab}"]').click()
                        geometry = page.evaluate("""() => {
                          const r = document.querySelector('.main-split').getBoundingClientRect();
                          const tabs = [...document.querySelectorAll('.mobile-tabs button')].map(b => b.getBoundingClientRect());
                          return {height:r.height,bottom:r.bottom,navTop:tabs[0].top,
                            reachable:tabs.every(r => r.left >= -1 && r.right <= innerWidth+1 && r.bottom <= innerHeight+1 && r.height>=44),
                            overflow:document.documentElement.scrollWidth>innerWidth+1};
                        }""")
                        assert geometry["height"] >= 140, (width, height, tab, geometry)
                        assert geometry["bottom"] <= geometry["navTop"] + 5, geometry
                        assert geometry["reachable"] and not geometry["overflow"], geometry
                    page.locator('[data-mobile-tab="pads"]').click()
                    page.screenshot(
                        path=str(args.output / f"{args.engine}-{width}x{height}-pads.png")
                    )
                    # Rotating the same page must preserve the project title.
                    page.locator(".project-name").fill("Rotation check")
                    page.locator(".project-name").blur()
                    page.set_viewport_size({"width": height, "height": width})
                    page.wait_for_function(
                        "document.documentElement.style.getPropertyValue('--studio-height') === Math.round(innerHeight)+'px'"
                    )
                    assert page.locator(".project-name").input_value() == "Rotation check"
                    page.set_viewport_size({"width": width, "height": height})
                    # Both unsupported and rejected orientation requests are harmless.
                    page.evaluate("""() => {
                      document.documentElement.requestFullscreen = () => Promise.reject(new Error('not allowed'));
                      if (screen.orientation) Object.defineProperty(screen.orientation, 'lock', {configurable:true,value:() => Promise.reject(new Error('not allowed'))});
                    }""")
                    page.locator("#transport-more").click()
                    page.locator("#landscape-mode").click()
                    assert not page.locator("#transport-sheet").evaluate("e=>e.open")
                else:
                    assert not page.locator(".mobile-tabs").is_visible()
                    assert page.locator(".studio-nav").is_visible()
                page.goto(origin + "/")
                assert page.locator('.hero-actions a[href="app/studio.html"]').is_visible()
                assert page.evaluate("document.documentElement.scrollWidth <= innerWidth+1"), (
                    width,
                    height,
                    "landing overflow",
                )
                report["viewports"].append(
                    {"width": width, "height": height, "phone_shell": phone, "pass": True}
                )
                context.close()
            # Chromium supplies service workers in this isolated Linux environment.
            if args.engine == "chromium":
                for prefix in ["", "/AnharmonicStudio"]:
                    context = browser.new_context(
                        viewport={"width": 844, "height": 390}, has_touch=True
                    )
                    page = context.new_page()
                    url = origin + prefix + "/app/studio.html?source=pwa&view=pads"
                    page.goto(url)
                    page.wait_for_function(
                        "window.AnharmonicPWA?.offlineReady && navigator.serviceWorker.controller"
                    )
                    await_cache = """async () => {
                      const names = await caches.keys();
                      const cache = await caches.open(names.find(n=>n.startsWith('anharmonic-studio-shell-')));
                      return Boolean(await cache.match(new URL('viewport.js', location.href).href));
                    }"""
                    assert page.evaluate(await_cache)
                    page.locator(".project-name").fill("Offline session")
                    page.locator(".project-name").blur()
                    page.locator("#save-project").click()
                    page.wait_for_function(
                        "document.querySelector('#status').textContent.includes('saved in this browser')"
                    )
                    context.set_offline(True)
                    page.reload()
                    page.wait_for_selector("#pad-grid .pad", state="attached")
                    assert page.locator(".project-name").input_value() == "Offline session"
                    assert page.locator(".mobile-tabs").is_visible()
                    report["offline"].append({"path": prefix + "/app/", "pass": True})
                    context.close()
            browser.close()
        assert not report["errors"], report["errors"]
    finally:
        server.shutdown()
        (args.output / "report.json").write_text(json.dumps(report, indent=2))
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
