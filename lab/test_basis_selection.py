#!/usr/bin/env python3
"""Executable conformance cases for activation-safe basis selection."""

from __future__ import annotations

import unittest

from basis import BasisSelectionError, select_basis


def snapshot(
    identifier: str,
    *,
    state: str = "activated",
    activated_at: float | None = 1.0,
    supersedes: tuple[str, ...] = (),
    subject: str = "payments",
    environment: str = "prod",
) -> dict:
    return {
        "id": identifier,
        "approved_at": 0.0,
        "activated_at": activated_at,
        "state": state,
        "scope": {"subjects": [subject], "environments": [environment]},
        "supersedes": list(supersedes),
    }


class BasisSelectionContract(unittest.TestCase):
    def choose(self, rows: list[dict], now: float = 10.0) -> str:
        return select_basis(
            rows, subject="payments", environment="prod", now=now
        )["id"]

    def test_active_baseline_is_selected(self) -> None:
        self.assertEqual(self.choose([snapshot("G3")]), "G3")

    def test_newer_pending_approval_does_not_replace_active_basis(self) -> None:
        rows = [snapshot("G3"), snapshot("G4", state="pending", activated_at=None)]
        self.assertEqual(self.choose(rows), "G3")

    def test_aborted_approval_does_not_replace_active_basis(self) -> None:
        rows = [snapshot("G3"), snapshot("G4", state="aborted", activated_at=None)]
        self.assertEqual(self.choose(rows), "G3")

    def test_activated_successor_replaces_predecessor(self) -> None:
        rows = [snapshot("G3"), snapshot("G4", activated_at=5.0, supersedes=("G3",))]
        self.assertEqual(self.choose(rows), "G4")

    def test_future_activation_does_not_replace_predecessor(self) -> None:
        rows = [snapshot("G3"), snapshot("G4", activated_at=20.0, supersedes=("G3",))]
        self.assertEqual(self.choose(rows, now=10.0), "G3")

    def test_nonoverlapping_activation_does_not_replace_basis(self) -> None:
        rows = [snapshot("G3"), snapshot("G4", subject="ledger")]
        self.assertEqual(self.choose(rows), "G3")

    def test_parallel_activated_approvals_are_undecidable(self) -> None:
        with self.assertRaisesRegex(BasisSelectionError, "ambiguous"):
            self.choose([snapshot("G3"), snapshot("G4")])

    def test_cycle_is_rejected(self) -> None:
        rows = [
            snapshot("G3", supersedes=("G4",)),
            snapshot("G4", supersedes=("G3",)),
        ]
        with self.assertRaisesRegex(BasisSelectionError, "cycle"):
            self.choose(rows)

    def test_no_activated_basis_is_undecidable(self) -> None:
        with self.assertRaisesRegex(BasisSelectionError, "no activated"):
            self.choose([snapshot("G4", state="pending", activated_at=None)])


if __name__ == "__main__":
    unittest.main()
