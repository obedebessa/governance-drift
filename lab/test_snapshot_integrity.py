#!/usr/bin/env python3
"""Contract tests for the tamper-evident snapshot recorder."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from snapshot_integrity import seal_snapshot, verify_snapshot


def metadata(identifier: str, predecessors: list[str]) -> dict:
    return {
        "id": identifier,
        "approved_at": 1.0,
        "activated_at": 2.0,
        "state": "activated",
        "scope": {"subjects": ["payments"], "environments": ["payments"]},
        "supersedes": predecessors,
    }


class SnapshotIntegrityContract(unittest.TestCase):
    def make_snapshot(self, root: Path, identifier: str, predecessors: list[str]) -> Path:
        directory = root / identifier
        directory.mkdir()
        (directory / "manifest.json").write_text('{"image":"approved"}\n')
        (directory / "metadata.json").write_text(
            json.dumps(metadata(identifier, predecessors)) + "\n"
        )
        seal_snapshot(directory)
        return directory

    def test_sealed_snapshot_verifies(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            directory = self.make_snapshot(Path(temporary), "G1", [])
            verify_snapshot(directory)

    def test_successor_binds_predecessor_hash(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            first = self.make_snapshot(root, "G1", [])
            second = self.make_snapshot(root, "G2", ["G1"])
            verify_snapshot(first)
            verify_snapshot(second)

    def test_content_tampering_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            directory = self.make_snapshot(Path(temporary), "G1", [])
            (directory / "manifest.json").write_text('{"image":"tampered"}\n')
            with self.assertRaisesRegex(ValueError, "content hash mismatch"):
                verify_snapshot(directory)


if __name__ == "__main__":
    unittest.main()
