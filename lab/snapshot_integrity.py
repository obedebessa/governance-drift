#!/usr/bin/env python3
"""Create and verify the laboratory's tamper-evident snapshot hash chain."""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path


def content_digest(directory: Path) -> str:
    digest = hashlib.sha256()
    files = sorted(
        path for path in directory.rglob("*")
        if path.is_file() and path.name != "metadata.json"
    )
    for path in files:
        relative = path.relative_to(directory).as_posix().encode()
        payload = path.read_bytes()
        digest.update(len(relative).to_bytes(8, "big"))
        digest.update(relative)
        digest.update(len(payload).to_bytes(8, "big"))
        digest.update(payload)
    return digest.hexdigest()


def record_digest(metadata: dict) -> str:
    payload = {key: value for key, value in metadata.items() if key != "record_sha256"}
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(canonical).hexdigest()


def seal_snapshot(directory: Path) -> dict:
    metadata_path = directory / "metadata.json"
    metadata = json.loads(metadata_path.read_text())
    predecessors = metadata.get("supersedes", [])
    if len(predecessors) > 1:
        raise ValueError("laboratory recorder supports one direct predecessor per activation")
    previous_hash = None
    if predecessors:
        previous_path = directory.parent / predecessors[0] / "metadata.json"
        previous = json.loads(previous_path.read_text())
        previous_hash = previous.get("record_sha256")
        if not previous_hash:
            raise ValueError("predecessor snapshot is not sealed")
    metadata["content_sha256"] = content_digest(directory)
    metadata["previous_record_sha256"] = previous_hash
    metadata["record_sha256"] = record_digest(metadata)
    metadata_path.write_text(json.dumps(metadata, indent=2, sort_keys=True) + "\n")
    return metadata


def verify_snapshot(directory: Path, metadata: dict | None = None) -> None:
    row = metadata or json.loads((directory / "metadata.json").read_text())
    if row.get("content_sha256") != content_digest(directory):
        raise ValueError(f"snapshot content hash mismatch: {row.get('id', directory.name)}")
    if row.get("record_sha256") != record_digest(row):
        raise ValueError(f"snapshot record hash mismatch: {row.get('id', directory.name)}")
    predecessors = row.get("supersedes", [])
    expected_previous = None
    if predecessors:
        previous = json.loads(
            (directory.parent / predecessors[0] / "metadata.json").read_text()
        )
        expected_previous = previous.get("record_sha256")
    if row.get("previous_record_sha256") != expected_previous:
        raise ValueError(f"snapshot chain mismatch: {row.get('id', directory.name)}")


def main() -> int:
    if len(sys.argv) != 3 or sys.argv[1] not in {"seal", "verify"}:
        raise SystemExit("usage: snapshot_integrity.py {seal|verify} SNAPSHOT_DIR")
    directory = Path(sys.argv[2])
    if sys.argv[1] == "seal":
        seal_snapshot(directory)
    else:
        verify_snapshot(directory)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
