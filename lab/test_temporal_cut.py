#!/usr/bin/env python3

from __future__ import annotations

import unittest

from temporal_cut import select_admissible_cut


class TemporalCutContract(unittest.TestCase):
    def setUp(self) -> None:
        self.records = {
            "policy": [
                {"id": "p7", "unit_ref": "u1", "capture_start": 99.0, "capture_end": 99.2}
            ],
            "approval": [
                {"id": "a4", "unit_ref": "u1", "capture_start": 99.1, "capture_end": 99.3}
            ],
        }
        self.contracts = {
            source: {
                "watermark": 101.0,
                "watermark_basis": "capture_start",
                "freshness_seconds": 2.0,
                "clock_error_bound_seconds": 0.05,
            }
            for source in self.records
        }

    def select(self):
        return select_admissible_cut(
            self.records,
            self.contracts,
            required_sources={"policy", "approval"},
            unit_ref="u1",
            theta=100.0,
            max_spread_seconds=1.0,
        )

    def test_coherent_cut_is_admitted(self) -> None:
        admitted, reason, selected = self.select()
        self.assertTrue(admitted, reason)
        self.assertEqual({source: row["id"] for source, row in selected.items()}, {
            "approval": "a4", "policy": "p7"
        })

    def test_missing_watermark_is_rejected(self) -> None:
        del self.contracts["policy"]["watermark"]
        admitted, reason, _ = self.select()
        self.assertFalse(admitted)
        self.assertIn("missing watermark", reason)

    def test_unclosed_prefix_is_rejected(self) -> None:
        self.contracts["policy"]["watermark"] = 99.9
        admitted, reason, _ = self.select()
        self.assertFalse(admitted)
        self.assertIn("has not closed", reason)

    def test_capture_upper_bound_watermark_is_rejected(self) -> None:
        self.contracts["policy"]["watermark_basis"] = "capture_end"
        admitted, reason, _ = self.select()
        self.assertFalse(admitted)
        self.assertIn("watermark basis", reason)

    def test_straddling_capture_is_rejected(self) -> None:
        self.records["policy"].append({
            "id": "p8", "unit_ref": "u1", "capture_start": 99.9, "capture_end": 100.1
        })
        admitted, reason, _ = self.select()
        self.assertFalse(admitted)
        self.assertIn("straddles", reason)

    def test_stale_latest_record_is_rejected(self) -> None:
        self.contracts["policy"]["freshness_seconds"] = 0.5
        admitted, reason, _ = self.select()
        self.assertFalse(admitted)
        self.assertIn("stale", reason)

    def test_ambiguous_latest_record_is_rejected(self) -> None:
        self.records["policy"].append({
            "id": "p7-copy", "unit_ref": "u1", "capture_start": 99.05, "capture_end": 99.2
        })
        admitted, reason, _ = self.select()
        self.assertFalse(admitted)
        self.assertIn("ambiguous", reason)

    def test_excessive_cross_source_spread_is_rejected(self) -> None:
        self.records["approval"][0].update(capture_start=95.0, capture_end=95.1)
        self.contracts["approval"]["freshness_seconds"] = 10.0
        admitted, reason, _ = self.select()
        self.assertFalse(admitted)
        self.assertIn("spread", reason)


if __name__ == "__main__":
    unittest.main()
