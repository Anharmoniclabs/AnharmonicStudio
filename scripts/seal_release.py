#!/usr/bin/env python
"""Encrypt paid build outputs with a public certificate before artifact upload."""

import argparse
from pathlib import Path
import subprocess
import tarfile
import tempfile


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--certificate", type=Path, default=Path("packaging/release-recipient.pem"))
    args = parser.parse_args()
    if not (args.directory / "SHA256SUMS").is_file():
        parser.error("Only a validated candidate with checksums may be sealed")
    cert = args.certificate.read_text()
    if "PRIVATE KEY" in cert or "BEGIN CERTIFICATE" not in cert:
        parser.error("Expected only the public release certificate")
    if args.output.exists():
        parser.error("Refusing to overwrite an existing encrypted candidate")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="seal-release-") as temporary:
        archive = Path(temporary) / "candidate.tar.gz"
        with tarfile.open(archive, "w:gz") as stream:
            stream.add(args.directory, arcname=args.directory.name)
        subprocess.run(
            [
                "openssl",
                "cms",
                "-encrypt",
                "-binary",
                "-aes256",
                "-in",
                str(archive),
                "-out",
                str(args.output),
                "-outform",
                "DER",
                str(args.certificate),
            ],
            check=True,
        )
    print(f"Encrypted candidate: {args.output}")


if __name__ == "__main__":
    main()
