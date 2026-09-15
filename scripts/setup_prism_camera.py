#!/usr/bin/env python3
"""Download the optional, pinned local hand model; never open a camera."""

import hashlib
from pathlib import Path
import sys
import urllib.request

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from mpclab.prism_camera import MODEL_URL, model_path

SHA256 = "fbc2a30080c3c557093b5ddfc334698132eb341044ccee322ccf8bcf3607cde1"


def main():
    path = model_path()
    if path.is_file() and hashlib.sha256(path.read_bytes()).hexdigest() == SHA256:
        print(f"Hand model ready: {path}")
        return
    with urllib.request.urlopen(MODEL_URL, timeout=30) as response:
        data = response.read(16 * 1024 * 1024 + 1)
    if len(data) > 16 * 1024 * 1024 or hashlib.sha256(data).hexdigest() != SHA256:
        raise RuntimeError("Hand model download failed checksum validation")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".download")
    temporary.write_bytes(data)
    temporary.replace(path)
    print(f"Hand model installed: {path}")


if __name__ == "__main__":
    main()
