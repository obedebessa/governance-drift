#!/usr/bin/env python3
"""Contract tests for digest-locked laboratory images."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from pin_manifests import pin


LAB = Path(__file__).resolve().parent


class ImageLockContract(unittest.TestCase):
    def test_every_locked_reference_uses_sha256(self) -> None:
        document = json.loads((LAB / "image-lock.json").read_text())
        self.assertGreaterEqual(len(document["images"]), 17)
        for tagged, locked in document["images"].items():
            self.assertIn(":", tagged)
            self.assertIn("@sha256:", locked)
            self.assertEqual(len(locked.rsplit("@sha256:", 1)[1]), 64)

    def test_manifest_pinner_replaces_exact_tag(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "install.yaml"
            path.write_text("image: example/controller:v1\n")
            count = pin(path, {"example/controller:v1": "example/controller@sha256:" + "a" * 64})
            self.assertEqual(count, 1)
            self.assertNotIn(":v1", path.read_text())
            self.assertIn("@sha256:", path.read_text())


if __name__ == "__main__":
    unittest.main()
