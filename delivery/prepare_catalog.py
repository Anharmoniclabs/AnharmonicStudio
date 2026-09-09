"""Prepare private delivery files from four decrypted native release directories."""

import argparse
import hashlib
import json
from pathlib import Path
import zipfile

TARGETS = {
    "windows": ("windows-x86_64", "*.exe"),
    "mac-arm": ("macos-arm64", "*.dmg"),
    "mac-intel": ("macos-x86_64", "*.dmg"),
    "linux": ("linux-x86_64", "*.tar.gz"),
}


def digest(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def prepare(root):
    root = root.resolve()
    artifacts = {}
    releases = set()
    commits = set()
    for platform, (directory, pattern) in TARGETS.items():
        candidate = root / directory
        checksums = candidate / "SHA256SUMS"
        verified = set()
        for line in checksums.read_text().splitlines():
            expected, name = line.split("  ", 1)
            path = (candidate / name).resolve()
            if not path.is_relative_to(candidate) or not path.is_file() or digest(path) != expected:
                raise ValueError(f"Release checksum failed: {directory}/{name}")
            verified.add(path)
        info = json.loads((candidate / "build-info.json").read_text())
        releases.add(info["version"])
        commits.add(info["source_commit"])
        installers = list(candidate.glob(pattern))
        sources = list(candidate.glob("*-source.zip"))
        if len(installers) != 1 or len(sources) != 1:
            raise ValueError(f"Expected one installer and matching source: {directory}")
        installer = installers[0]
        if installer not in verified or sources[0] not in verified:
            raise ValueError("Installer or source missing from release checksums")
        materials = root / f"{platform}-source-and-notices.zip"
        with zipfile.ZipFile(materials, "w", zipfile.ZIP_DEFLATED) as archive:
            for path in sorted(verified | {checksums}):
                if path != installer:
                    archive.write(path, path.relative_to(candidate))
        artifacts[platform] = {
            "file": str(installer.relative_to(root)),
            "sha256": digest(installer),
            "materials": {"file": materials.name, "sha256": digest(materials)},
        }
    if len(releases) != 1 or len(commits) != 1:
        raise ValueError("All four installers must have the same release and source commit")
    catalog = {"release": releases.pop(), "source_commit": commits.pop(), "artifacts": artifacts}
    output = root / "catalog.json"
    temporary = root / "catalog.json.tmp"
    temporary.write_text(json.dumps(catalog, indent=2) + "\n")
    temporary.replace(output)
    return output


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    print(prepare(parser.parse_args().directory))
