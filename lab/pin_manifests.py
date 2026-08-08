#!/usr/bin/env python3
"""Replace controller image tags in downloaded manifests with locked digests."""

from __future__ import annotations

import argparse
import json
from pathlib import Path


def pin(path: Path, images: dict[str, str]) -> int:
    text = path.read_text()
    replacements = 0
    for tagged, locked in images.items():
        count = text.count(tagged)
        if count:
            text = text.replace(tagged, locked)
            replacements += count
    path.write_text(text)
    return replacements


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--lock", type=Path, required=True)
    parser.add_argument("manifests", nargs="+", type=Path)
    args = parser.parse_args()
    images = json.loads(args.lock.read_text())["images"]
    total = sum(pin(path, images) for path in args.manifests)
    if total == 0:
        raise SystemExit("no image references matched the lock")
    remaining = [
        tagged for tagged in images
        if any(tagged in path.read_text() for path in args.manifests)
    ]
    if remaining:
        raise SystemExit("unlocked image references remain: " + ", ".join(remaining))
    print(f"pinned {total} manifest image references by digest")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
