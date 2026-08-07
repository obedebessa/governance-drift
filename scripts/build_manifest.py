#!/usr/bin/env python3
"""Build MANIFEST.sha256 for release files."""

from __future__ import annotations

import hashlib
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
EXCLUDED_PARTS = {".git", "__pycache__", ".pytest_cache", "tmp"}
EXCLUDED_SUFFIXES = {".aux", ".bbl", ".blg", ".log", ".out", ".xdv"}


def included(path: Path) -> bool:
    rel = path.relative_to(ROOT)
    return (
        path.is_file()
        and path.name != "MANIFEST.sha256"
        and not any(part in EXCLUDED_PARTS for part in rel.parts)
        and path.suffix not in EXCLUDED_SUFFIXES
        and path.name != "main.pdf"
        and path.name != "governance-drift-paper.pdf"
    )


def main() -> None:
    lines = []
    for path in sorted(p for p in ROOT.rglob("*") if included(p)):
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        lines.append(f"{digest}  {path.relative_to(ROOT).as_posix()}")
    (ROOT / "MANIFEST.sha256").write_text("\n".join(lines) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
