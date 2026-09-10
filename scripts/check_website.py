#!/usr/bin/env python3
"""Validate the static Pages tree without uploading installers or private files."""

from html.parser import HTMLParser
from pathlib import Path
import re
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
    pages = {}
    errors = []
    for path in ROOT.rglob("*.html"):
        page = Page()
        page.feed(path.read_text())
        pages[path.resolve()] = page
        errors.extend(f"{path.relative_to(ROOT)}: {error}" for error in page.errors)
    for path, page in pages.items():
        errors.extend(validate_references(path, page, pages))
    for path in ROOT.rglob("*"):
        if path.is_symlink():
            errors.append(f"Symlinks are not allowed in Pages: {path.relative_to(ROOT)}")
        elif path.is_file() and path.suffix.lower() not in ALLOWED and path != ROOT / ".nojekyll":
            errors.append(f"Non-site file in Pages: {path.relative_to(ROOT)}")
    required = {"download", "open-source", "support", "release-status", "download-dialog"}
    landing = pages[(ROOT / "index.html").resolve()]
    if required - landing.ids:
        errors.append(f"Required sections missing: {required - landing.ids}")
    if errors:
        raise SystemExit("\n".join(errors))
    print(f"Pages checks passed: {len(pages)} HTML pages; local references and site files only.")


def validate_references(path, page, pages):
    errors = []
    for reference in page.references:
        url = urlsplit(reference)
        if url.scheme or url.netloc:
            contact = (
                url.scheme == "mailto"
                and not (url.netloc or url.query or url.fragment)
                and re.fullmatch(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}", url.path)
            )
            if url.scheme != "https" and not contact:
                errors.append(f"{path.name}: Non-HTTPS external reference: {reference}")
            continue
        if not url.path:
            if url.fragment and url.fragment not in page.ids:
                errors.append(f"{path.name}: Missing anchor: {reference}")
        else:
            target = (path.parent / unquote(url.path)).resolve()
            if target.is_dir():
                target = target / "index.html"
            if not target.is_relative_to(ROOT) or not target.is_file():
                errors.append(f"{path.name}: Missing or escaping local reference: {reference}")
            elif url.fragment and target in pages and url.fragment not in pages[target].ids:
                errors.append(f"{path.name}: Missing target anchor: {reference}")
    return errors


if __name__ == "__main__":
    main()
