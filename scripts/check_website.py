#!/usr/bin/env python3
"""Validate the static Pages tree without uploading installers or private files."""

from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import unquote, urlsplit

ROOT = Path(__file__).resolve().parent.parent / "website"
ALLOWED = {
    ".html",
    ".css",
    ".js",
    ".md",
    ".svg",
    ".png",
    ".jpg",
    ".jpeg",
    ".webp",
    ".ico",
    ".mp4",
    ".vtt",
}


class Page(HTMLParser):
    def __init__(self):
        super().__init__()
        self.ids = set()
        self.references = []
        self.errors = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if "id" in attrs:
            if attrs["id"] in self.ids:
                self.errors.append(f"Duplicate id: {attrs['id']}")
            self.ids.add(attrs["id"])
        for attribute in ("href", "src", "poster"):
            if attrs.get(attribute):
                self.references.append(attrs[attribute])
        if tag == "img" and "alt" not in attrs:
            self.errors.append("Image lacks an alt attribute")


def main():
    page = Page()
    page.feed((ROOT / "index.html").read_text())
    for reference in page.references:
        url = urlsplit(reference)
        if url.scheme or url.netloc:
            if url.scheme != "https":
                page.errors.append(f"Non-HTTPS external reference: {reference}")
            continue
        if not url.path:
            if url.fragment and url.fragment not in page.ids:
                page.errors.append(f"Missing anchor: {reference}")
        else:
            target = (ROOT / unquote(url.path)).resolve()
            if not target.is_relative_to(ROOT) or not target.is_file():
                page.errors.append(f"Missing or escaping local reference: {reference}")
    for path in ROOT.rglob("*"):
        if path.is_symlink():
            page.errors.append(f"Symlinks are not allowed in Pages: {path.relative_to(ROOT)}")
        elif path.is_file() and path.suffix.lower() not in ALLOWED and path != ROOT / ".nojekyll":
            page.errors.append(f"Non-site file in Pages: {path.relative_to(ROOT)}")
    required = {"download", "open-source", "support", "release-status", "download-dialog"}
    if required - page.ids:
        page.errors.append(f"Required sections missing: {required - page.ids}")
    if page.errors:
        raise SystemExit("\n".join(page.errors))
    print(
        f"Pages checks passed: {len(page.ids)} IDs, {len(page.references)} references; site files only."
    )


if __name__ == "__main__":
    main()
