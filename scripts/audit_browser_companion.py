#!/usr/bin/env python
"""Check the companion with an isolated headless browser and disposable Studio.

Run using requirements-browser.txt, passing --studio-python when Qt is installed
in a separate environment. Never opens audio devices or desktop windows.
"""

from __future__ import annotations
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]


def fixture(root):
    os.environ["QT_QPA_PLATFORM"] = "offscreen"
    sys.path.insert(0, str(ROOT))
    from PySide6.QtWidgets import QApplication
    from PySide6.QtCore import QTimer, QPoint
    from mpclab.application_features import install_application_runtime, attach_application_features
    from mpclab.ui import main_window
    from mpclab.engine import Engine
    from mpclab.companion.ui import start_companion, install_companion_action
    from scripts.render_studio_preview import PreviewSettings

    app = QApplication([])
    app.setQuitOnLastWindowClosed(False)
    install_application_runtime()
    main_window.QSettings = PreviewSettings
    Engine.start = lambda self: None
    (root / "data").mkdir()
    window = main_window.MainWindow(root / "data", restore_session=False)
    attach_application_features(window)
    install_companion_action(window)
    window._ui_timer.stop()
    window._autosave_timer.stop()
    window.resize(1440, 900)
    window.show()
    app.processEvents()
    companion = start_companion(window)
    companion.capture()
    edit = window.proj_name
    position = edit.mapTo(window, QPoint(edit.width() // 2, edit.height() // 2))
    (root / "ready.json").write_text(
        json.dumps(dict(url=companion.url, x=position.x(), y=position.y()))
    )

    def check():
        # Atomic status publication avoids a reader observing partial JSON.
        staged = root / "state.tmp"
        staged.write_text(json.dumps(dict(name=window.project.name)))
        staged.replace(root / "state.json")
        if (root / "stop").exists():
            window._dirty = False
            window.close()
            app.quit()

    timer = QTimer()
    timer.timeout.connect(check)
    timer.start(100)
    QTimer.singleShot(90000, app.quit)
    return app.exec()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixture", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--studio-python", default=sys.executable)
    parser.add_argument("--browser")
    parser.add_argument("--output", type=Path, default=ROOT / "reports/browser-companion")
    args = parser.parse_args()
    if args.fixture:
        return fixture(args.fixture)
    from playwright.sync_api import sync_playwright, expect

    args.output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="companion-smoke-") as temporary:
        root = Path(temporary)
        with (root / "fixture.log").open("w") as log:
            child = subprocess.Popen(
                [args.studio_python, str(Path(__file__).resolve()), "--fixture", str(root)],
                stdout=log,
                stderr=log,
                env=dict(os.environ, OPENBLAS_NUM_THREADS="1"),
            )
            try:
                for _ in range(300):
                    if (root / "ready.json").exists():
                        break
                    if child.poll() is not None:
                        raise RuntimeError((root / "fixture.log").read_text())
                    time.sleep(0.1)
                info = json.loads((root / "ready.json").read_text())
                with sync_playwright() as playwright:
                    browser = playwright.chromium.launch(
                        executable_path=args.browser, headless=True
                    )
                    page = browser.new_page(viewport=dict(width=1600, height=1100))
                    errors = []
                    page.on("pageerror", lambda error: errors.append(str(error)))
                    page.goto(info["url"])
                    expect(page.locator("#status")).to_have_text("Connected · native workstation")
                    canvas = page.locator("canvas").first
                    box = canvas.bounding_box()
                    canvas.click(
                        position=dict(
                            x=info["x"] * box["width"] / 1440, y=info["y"] * box["height"] / 900
                        )
                    )
                    page.keyboard.press("Control+a")
                    page.keyboard.type("Browser Native Session")
                    page.keyboard.press("Enter")
                    for _ in range(100):
                        state = json.loads((root / "state.json").read_text())
                        if state["name"] == "Browser Native Session":
                            break
                        time.sleep(0.1)
                    assert state["name"] == "Browser Native Session", state
                    page.reload()
                    expect(page.locator("#status")).to_have_text("Connected · native workstation")
                    page.screenshot(
                        path=str(args.output / "browser-native-session.png"), full_page=True
                    )
                    assert not errors, errors
                    browser.close()
                    (args.output / "browser-smoke.json").write_text(
                        json.dumps(
                            dict(
                                passed=True, project_name=state["name"], reload=True, errors=errors
                            )
                        )
                    )
            finally:
                (root / "stop").touch()
                try:
                    child.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    # Only the disposable child created above; no name matching.
                    child.terminate()
                    child.wait(timeout=5)
        (args.output / "browser-fixture.log").write_text((root / "fixture.log").read_text())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
