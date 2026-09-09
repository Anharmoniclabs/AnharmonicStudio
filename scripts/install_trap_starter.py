#!/usr/bin/env python3
"""Curate the official MusicRadar 808 pack into Anharmonic Studio's 16-pad starter."""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
import zipfile
from pathlib import Path

import soundfile as sf

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from mpclab.library import Library
from mpclab.starter import MANIFEST_NAME, make_trap_project


SOURCE_URL = "https://cdn.mos.musicradar.com/audio/samples/musicradar-808-samples.zip"
SOURCE_PAGE = "https://www.musicradar.com/news/sampleradar-378-free-808-drum-samples"
LICENSE = (
    "Royalty-free for use in music; raw samples may not be redistributed. "
    "See the MusicRadar source page for the publisher's full wording."
)
DEFAULT_ARCHIVE = (
    Path.home() / "Music" / "MPC-Lab-Sample-Packs" / "archives" / "musicradar-808-samples.zip"
)
DEFAULT_EXTRACTED = (
    Path.home() / "Music" / "MPC-Lab-Sample-Packs" / "extracted" / "musicradar-808-samples"
)

TOKEN_PATTERNS = {
    "snare": re.compile(r"(^|[^a-z])(snare|sd)([^a-z]|$)"),
    "clap": re.compile(r"(^|[^a-z])(clap|cp)([^a-z]|$)"),
    "closed_hat": re.compile(r"closed.?hat|closed.?hh|(^|[^a-z])chh?([^a-z]|$)"),
    "open_hat": re.compile(r"open.?hat|open.?hh|(^|[^a-z])ohh?([^a-z]|$)"),
    "rim": re.compile(r"(^|[^a-z])(rim|rimshot|rs)([^a-z]|$)"),
    "perc": re.compile(r"perc|clave|cowbell|conga|tom|maraca|shaker"),
    "fx": re.compile(r"fx|crash|cymbal|ride|noise"),
    "kick": re.compile(r"(^|[^a-z])(kick|bd|bass.?drum)([^a-z]|$)"),
}


def _safe_extract(archive: Path, destination: Path) -> None:
    destination.mkdir(parents=True, exist_ok=True)
    root = destination.resolve()
    with zipfile.ZipFile(archive) as pack:
        for member in pack.infolist():
            target = (destination / member.filename).resolve()
            if root != target and root not in target.parents:
                raise ValueError(f"unsafe archive path: {member.filename}")
        pack.extractall(destination)


def _duration(path: Path) -> float:
    try:
        info = sf.info(str(path))
        return info.frames / max(1, info.samplerate)
    except Exception:
        return 0.0


def _group(files: list[Path]) -> dict[str, list[Path]]:
    groups = {key: [] for key in TOKEN_PATTERNS}
    for path in files:
        text = " ".join(path.parts[-4:]).lower().replace("_", " ").replace("-", " ")
        for role, pattern in TOKEN_PATTERNS.items():
            if pattern.search(text):
                groups[role].append(path)
                break
    for role in groups:
        groups[role].sort(key=lambda p: (p.name.lower(), str(p)))
    return groups


def _pick(candidates: list[Path], used: set[Path], *, longest: bool = False) -> Path | None:
    available = [p for p in candidates if p not in used]
    if not available:
        return None
    # Long bass-drum tails become playable 808 basses; short bass drums stay
    # punchy enough to layer as kicks.  Other groups use deterministic variety.
    by_length = sorted(available, key=lambda p: (_duration(p), p.name.lower()))
    chosen = by_length[-1] if longest else by_length[len(by_length) // 3]
    used.add(chosen)
    return chosen


def curate(files: list[Path]) -> dict[str, Path]:
    groups = _group(files)
    used: set[Path] = set()
    result: dict[str, Path] = {}

    bass = groups["kick"]
    for role, longest in (("808", True), ("808_alt", True), ("kick", False), ("kick_alt", False)):
        selected = _pick(bass, used, longest=longest)
        if selected:
            result[role] = selected

    for role, source in (
        ("snare", "snare"),
        ("snare_alt", "snare"),
        ("clap", "clap"),
        ("clap_alt", "clap"),
        ("closed_hat", "closed_hat"),
        ("closed_hat_alt", "closed_hat"),
        ("open_hat", "open_hat"),
        ("open_hat_alt", "open_hat"),
        ("rim", "rim"),
        ("perc", "perc"),
        ("perc_alt", "perc"),
        ("fx", "fx"),
    ):
        selected = _pick(groups[source], used)
        if selected:
            result[role] = selected

    # Some packs call both hats simply HH. Fall back to any hat-looking files.
    hats = [p for p in files if re.search(r"hat|hh", p.name.lower())]
    for role in ("closed_hat", "closed_hat_alt", "open_hat", "open_hat_alt"):
        if role not in result:
            selected = _pick(hats, used, longest=role.startswith("open"))
            if selected:
                result[role] = selected
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("archive", nargs="?", type=Path, default=DEFAULT_ARCHIVE)
    parser.add_argument("--library", type=Path, default=ROOT / "library")
    parser.add_argument("--extract-to", type=Path, default=DEFAULT_EXTRACTED)
    args = parser.parse_args()

    if not args.archive.is_file():
        parser.error(f"archive not found: {args.archive}")
    _safe_extract(args.archive, args.extract_to)
    files = sorted(
        p
        for p in args.extract_to.rglob("*")
        if (
            p.is_file()
            and p.suffix.lower() in {".wav", ".aif", ".aiff"}
            and "__MACOSX" not in p.parts
            and not p.name.startswith("._")
        )
    )
    if not files:
        parser.error("the archive contains no WAV/AIFF samples")

    # A trap pad kit needs clean one-shots. MusicRadar ships loops in the same
    # archive, and choosing by duration across both groups used to mistake a
    # full kick loop for an 808 tail. Prefer the publisher's Hits folder.
    hits = [p for p in files if any(part.lower() == "hits" for part in p.parts)]
    selected = curate(hits or files)
    missing = [role for role in ("808", "kick", "snare", "closed_hat") if role not in selected]
    if missing:
        parser.error("could not identify required sounds: " + ", ".join(missing))

    library = Library(args.library)
    clips: dict[str, str] = {}
    originals: dict[str, str] = {}
    imported: list[str] = []
    try:
        for role, path in selected.items():
            clip = library.import_file(
                path, name=f"Trap Kit - {role.replace('_', ' ').upper()}", kind="kit"
            )
            imported.append(clip.id)
            clips[role] = clip.id
            originals[role] = str(path.relative_to(args.extract_to))
            print(f"{role:16} {path.name}")
    except Exception:
        # A failed transcode must not leave half a kit mixed into the browser.
        for clip_id in imported:
            library.delete(clip_id)
        raise

    manifest = {
        "title": "MusicRadar 808 Fire Kit",
        "source_page": SOURCE_PAGE,
        "download_url": SOURCE_URL,
        "license": LICENSE,
        "installed_at": time.time(),
        "clips": clips,
        "original_files": originals,
    }
    (args.library / MANIFEST_NAME).write_text(json.dumps(manifest, indent=2))
    template = ROOT / "projects" / "Fire Trap Starter.json"
    template.parent.mkdir(exist_ok=True)
    if not template.exists():
        make_trap_project(clips).save(template)
        print(f"Created ready-to-load project: {template}")
    print(f"Installed {len(clips)} sounds into {args.library}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
