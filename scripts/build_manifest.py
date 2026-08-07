#!/usr/bin/env python3
"""Build MANIFEST.sha256 for release files."""

from __future__ import annotations

import hashlib
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
EXCLUDED_PARTS = {".git", "__pycache__", ".pytest_cache", "runtime", "tmp"}
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
    result = subprocess.run(
        ["git", "ls-files", "-co", "--exclude-standard", "-z"],
        cwd=ROOT,
        check=True,
        capture_output=True,
    )
    relative_paths = sorted(
        Path(item.decode("utf-8")) for item in result.stdout.split(b"\0") if item
    )
    for relative in relative_paths:
        path = ROOT / relative
        if not included(path):
            continue
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        lines.append(f"{digest}  {relative.as_posix()}")
    (ROOT / "MANIFEST.sha256").write_text("\n".join(lines) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
