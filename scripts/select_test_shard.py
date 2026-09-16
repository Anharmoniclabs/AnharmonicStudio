"""Select a deterministic, weighted pytest file shard for CI."""

from __future__ import annotations

import argparse
from pathlib import Path


def test_files(root: Path) -> list[Path]:
    paths = sorted(
        path
        for directory in (root / "tests", root / "automation")
        if directory.is_dir()
        for path in directory.rglob("test_*.py")
        if path.is_file()
    )
    if not paths:
        raise SystemExit("no pytest files found")
    return paths


def shard(paths: list[Path], count: int) -> list[list[Path]]:
    buckets: list[list[Path]] = [[] for _ in range(count)]
    weights = [0] * count
    for path in sorted(paths, key=lambda item: (-item.stat().st_size, item.as_posix())):
        index = min(range(count), key=weights.__getitem__)
        buckets[index].append(path)
        weights[index] += path.stat().st_size
    return buckets


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--shard", type=int, required=True)
    parser.add_argument("--count", type=int, required=True)
    args = parser.parse_args()
    if not 0 <= args.shard < args.count:
        parser.error("shard must be within the shard count")
    root = Path(__file__).resolve().parents[1]
    for path in shard(test_files(root), args.count)[args.shard]:
        print(path.relative_to(root))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
