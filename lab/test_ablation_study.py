#!/usr/bin/env python3
"""Contract tests for executable ablation semantics."""

from __future__ import annotations

import unittest

import ablation_study as study


class AblationContract(unittest.TestCase):
    def test_core_ladder_has_expected_cumulative_rates(self) -> None:
        rates = []
        for variant in study.VARIANTS:
            rows = [
                study.run_case(scenario, variant, seed)
                for scenario in study.study.SCENARIOS if scenario != "S0"
                for seed in study.study.SEEDS
            ]
            rates.append(sum(row["exact"] for row in rows) / len(rows))
        # B2 deliberately has no intent-history join, so it cannot add the
        # intent component in S12 even though it masks current authorization
        # correctly as evidence. B3 is the first variant that can do both.
        self.assertEqual(rates, [0.75, 10 / 12, 10 / 12, 1.0, 1.0])
        self.assertEqual(rates, sorted(rates))

    def test_full_join_passes_every_semantic_probe(self) -> None:
        rows = [row for row in study.semantic_probes() if row["variant"] == "B4"]
        self.assertEqual(len(rows), 10)
        self.assertTrue(all(row["pass"] for row in rows))

    def test_pending_approval_does_not_replace_active_basis(self) -> None:
        rows = [row for row in study.semantic_probes()
                if row["variant"] == "B4" and row["probe"] == "pending_does_not_replace"]
        self.assertEqual(rows[0]["observed"], "G3")

    def test_missing_live_one_shot_is_not_evidence_failure(self) -> None:
        expected, observed = study.class_probe("B4", "missing_live_one_shot")
        self.assertEqual((expected, observed), ("", ""))


if __name__ == "__main__":
    unittest.main()
