#!/usr/bin/env python
"""Install the pinned CC0 orchestral recordings as lossless FLAC assets."""

from concurrent.futures import ThreadPoolExecutor
import hashlib
import io
import json
from pathlib import Path
import tempfile
from urllib.parse import quote
from urllib.request import urlopen

import soundfile as sf

ROOT = Path(__file__).resolve().parent.parent / "assets/orchestra"


def main():
    manifest = json.loads((ROOT / "manifest.json").read_text())
    revision = manifest["revision"]
    zones = [
        zone for instrument in manifest["instruments"].values() for zone in instrument["zones"]
    ]

    def install(zone):
        destination = ROOT / zone["file"]
        if destination.is_file():
            sf.info(destination)  # Reject an incomplete previous installation.
            return
        url = (
            "https://raw.githubusercontent.com/sgossner/VSCO-2-CE/"
            + revision
            + "/"
            + quote(zone["upstream_path"])
        )
        with urlopen(url, timeout=45) as response:
            data = response.read(zone["bytes"] + 1)
        digest = hashlib.sha1(f"blob {len(data)}\0".encode() + data).hexdigest()
        if len(data) != zone["bytes"] or digest != zone["blob_sha1"]:
            raise ValueError(f"Upstream integrity mismatch: {zone['upstream_path']}")
        audio, sr = sf.read(io.BytesIO(data), dtype="float32", always_2d=True)
        destination.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            dir=destination.parent, suffix=".flac", delete=False
        ) as temporary:
            temporary_path = Path(temporary.name)
        try:
            sf.write(temporary_path, audio, sr, subtype="PCM_24", format="FLAC")
            temporary_path.replace(destination)
        finally:
            temporary_path.unlink(missing_ok=True)

    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(install, zones))
    print(f"Installed {len(zones)} recordings across {len(manifest['instruments'])} articulations")


if __name__ == "__main__":
    main()
