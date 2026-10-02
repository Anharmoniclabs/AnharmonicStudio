#!/usr/bin/env python3
"""Render the web studio's installable-app icons from the vector mark.

Only an isolated headless Chromium is launched. The output PNG files are public
website assets referenced by ``website/app/manifest.webmanifest``; rerun this
after changing the mark or the brand background so the installed icon matches.
"""

import argparse
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MARK = ROOT / "website/assets/mark.svg"
OUTPUT = ROOT / "website/assets/pwa"
BACKGROUND = "#101722"

# Regular icons keep a rounded plate so they read well in browser tabs and
# desktop launchers; the maskable icon fills the whole canvas because the
# platform applies its own mask, keeping the mark inside the 80% safe zone.
ICONS = (
    ("icon-192.png", 192, False),
    ("icon-512.png", 512, False),
    ("icon-maskable-512.png", 512, True),
)


def page_html(mark_svg, size, maskable):
    radius = 0 if maskable else round(size * 0.22)
    inset = round(size * (0.2 if maskable else 0.17))
    return f"""<!doctype html><html><body style="margin:0;background:transparent">
<div id="icon" style="width:{size}px;height:{size}px;border-radius:{radius}px;background:{BACKGROUND};
display:flex;align-items:center;justify-content:center;box-sizing:border-box;padding:{inset}px">
<div style="width:100%;height:100%;display:flex;align-items:center;justify-content:center">{mark_svg}</div>
</div></body></html>"""


def main():
    from playwright.sync_api import sync_playwright

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--browser", type=Path, help="Chromium executable to use")
    parser.add_argument("--output", type=Path, default=OUTPUT)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    mark = MARK.read_text().replace(
        'width="834" height="644"', 'width="100%" height="100%" preserveAspectRatio="xMidYMid meet"'
    )
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(
            headless=True, **({"executable_path": str(args.browser)} if args.browser else {})
        )
        try:
            page = browser.new_page(viewport={"width": 600, "height": 600}, device_scale_factor=1)
            for name, size, maskable in ICONS:
                page.set_content(page_html(mark, size, maskable))
                page.locator("#icon").screenshot(path=str(args.output / name), omit_background=True)
                print(
                    f"wrote {args.output / name} ({size}x{size}{' maskable' if maskable else ''})"
                )
        finally:
            browser.close()


if __name__ == "__main__":
    main()
