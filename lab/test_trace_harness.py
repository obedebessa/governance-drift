#!/usr/bin/env python3
"""Pure contract tests for the isolated positive trace harness."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


LAB = Path(__file__).resolve().parent
sys.path.insert(0, str(LAB))

import run_trace_experiment as campaign  # noqa: E402
import trace_evaluator  # noqa: E402


class TraceEvaluatorTests(unittest.TestCase):
    def fixture(self, runtime: Path) -> tuple[dict, dict, dict]:
        image = "sha256:" + "a" * 64
        deployment = campaign.deployment_object(
            "nginx@sha256:" + "b" * 64, "v2-approved"
        )
        deployment["metadata"].update({"uid": "deployment-uid", "resourceVersion": "12", "generation": 1})
        policy = campaign.policy_object("pi-7")
        policy["metadata"].update({"uid": "policy-uid", "resourceVersion": "7"})
        approval = {
            "id": "APR-TRACE-BASE",
            "mode": "continuing",
            "subject": f"deployment/{campaign.DEPLOYMENT}",
            "unit_ref": {
                "cluster": "kind-govdrift-lab",
                "namespace": campaign.NAMESPACE,
                "kind": "Deployment",
                "name": campaign.DEPLOYMENT,
            },
            "revisions": ["baseline-revision"],
            "subjects": [image],
            "revoked": False,
        }
        basis = {
            "basis_id": "test-basis",
            "policy_version": "pi-7",
            "approval": approval,
            "inventory": {"region": "us-east-1"},
        }
        (runtime / "work/workload").mkdir(parents=True)
        (runtime / "approvals").mkdir()
        (runtime / "proofs").mkdir()
        (runtime / "basis.json").write_text(json.dumps(basis), encoding="utf-8")
        (runtime / "work/workload/deployment.json").write_text(json.dumps(deployment), encoding="utf-8")
        (runtime / "cloud-inventory.json").write_text(json.dumps(basis["inventory"]), encoding="utf-8")
        pod = {
            "metadata": {"name": "payments-trace-x", "uid": "pod-uid", "resourceVersion": "19"},
            "status": {
                "phase": "Running",
                "containerStatuses": [{"ready": True, "imageID": f"docker.io/library/nginx@{image}"}],
            },
        }
        return deployment, policy, pod

    def fake_command(
        self,
        deployment: dict,
        policy: dict,
        pod: dict,
        *,
        revision: str = "baseline-revision",
    ):
        def command(*args: str, cwd: Path | None = None) -> str:
            del cwd
            if args[0] == "git":
                return f"{revision}\n"
            if args[:3] == ("kubectl", "create", "--dry-run=client"):
                return json.dumps(deployment)
            if "deployment" in args and campaign.DEPLOYMENT in args:
                return json.dumps(deployment)
            if "policyreport" in args:
                return json.dumps({"items": []})
            if "policy" in args and campaign.POLICY in args:
                return json.dumps(policy)
            if "pod" in args:
                return json.dumps({"items": [pod]})
            raise AssertionError(args)
        return command

    def test_missing_continuing_status_stays_epistemic(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            runtime = Path(directory)
            deployment, policy, pod = self.fixture(runtime)
            with patch.object(trace_evaluator, "command", self.fake_command(deployment, policy, pod)):
                result = trace_evaluator.evaluate(
                    runtime=runtime,
                    namespace=campaign.NAMESPACE,
                    deployment=campaign.DEPLOYMENT,
                    policy=campaign.POLICY,
                )
        self.assertEqual(result["verdict"], "undecidable")
        self.assertEqual(result["class_set"], ["evidence"])
        self.assertEqual(result["components"]["authorization"], "undecidable")
        self.assertEqual(result["components"]["intent"], "consistent")
        self.assertEqual(result["drift_set"], [])

    def test_unapproved_revision_survives_missing_current_status_as_s12(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            runtime = Path(directory)
            deployment, policy, pod = self.fixture(runtime)
            command = self.fake_command(
                deployment, policy, pod, revision="unapproved-rollback"
            )
            with patch.object(trace_evaluator, "command", command):
                result = trace_evaluator.evaluate(
                    runtime=runtime,
                    namespace=campaign.NAMESPACE,
                    deployment=campaign.DEPLOYMENT,
                    policy=campaign.POLICY,
                )
        self.assertEqual(result["verdict"], "drift")
        self.assertEqual(result["class_set"], ["intent", "evidence"])
        self.assertEqual(result["components"]["authorization"], "undecidable")
        self.assertEqual(result["components"]["intent"], "inconsistent")

    def test_authenticated_retroactive_revocation_invalidates_intent(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            runtime = Path(directory)
            deployment, policy, pod = self.fixture(runtime)
            basis = json.loads((runtime / "basis.json").read_text())
            basis["approval"].update(revocation_effect="retroactive")
            (runtime / "basis.json").write_text(
                json.dumps(basis), encoding="utf-8"
            )
            approval = {**basis["approval"], "revoked": True}
            (runtime / "approvals/APR-TRACE-BASE.json").write_text(
                json.dumps(approval), encoding="utf-8"
            )
            with patch.object(
                trace_evaluator,
                "command",
                self.fake_command(deployment, policy, pod),
            ):
                result = trace_evaluator.evaluate(
                    runtime=runtime,
                    namespace=campaign.NAMESPACE,
                    deployment=campaign.DEPLOYMENT,
                    policy=campaign.POLICY,
                )
        self.assertEqual(result["verdict"], "drift")
        self.assertEqual(set(result["drift_set"]), {"authorization", "intent"})
        self.assertEqual(result["undecidable_components"], [])

    def test_identity_mismatch_is_authorization_drift(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            runtime = Path(directory)
            deployment, policy, pod = self.fixture(runtime)
            approval = json.loads((runtime / "basis.json").read_text())["approval"]
            approval["subject"] = "deployment/unrelated"
            (runtime / "approvals/APR-TRACE-BASE.json").write_text(json.dumps(approval))
            with patch.object(trace_evaluator, "command", self.fake_command(deployment, policy, pod)):
                result = trace_evaluator.evaluate(
                    runtime=runtime,
                    namespace=campaign.NAMESPACE,
                    deployment=campaign.DEPLOYMENT,
                    policy=campaign.POLICY,
                )
        self.assertEqual(result["verdict"], "drift")
        self.assertEqual(result["class_set"], ["authorization"])


class MarkerTests(unittest.TestCase):
    def test_p95_uses_nearest_rank_not_floor_index(self) -> None:
        values = [float(value) for value in range(1, 28)]
        self.assertEqual(campaign.nearest_rank(values, 0.95), 26.0)

    def test_two_poll_exact_is_second_consecutive_exact_poll(self) -> None:
        rows = [
            {"sequence": 1, "completed_mono": 9.0, "verdict": "consistent", "class_set": []},
            {"sequence": 2, "completed_mono": 11.0, "verdict": "undecidable", "class_set": ["evidence"]},
            {"sequence": 3, "completed_mono": 12.0, "verdict": "drift", "class_set": ["configuration"]},
            {"sequence": 4, "completed_mono": 13.0, "verdict": "consistent", "class_set": []},
            {"sequence": 5, "completed_mono": 14.0, "verdict": "drift", "class_set": ["configuration"]},
            {"sequence": 6, "completed_mono": 15.0, "verdict": "drift", "class_set": ["configuration"]},
        ]
        for row in rows:
            row.update({
                "poll_sha256": str(row["sequence"]),
                "actual_start_mono": row["completed_mono"] - 0.1,
                "actual_start_utc": "2026-08-08T00:00:00+00:00",
                "completed_utc": "2026-08-08T00:00:00+00:00",
            })
        observed = campaign.event_markers(rows, 10.0, ["configuration"])
        self.assertEqual(observed["first_alert"]["sequence"], 2)
        self.assertEqual(observed["exact"]["sequence"], 3)
        self.assertEqual(observed["two_poll_exact"]["sequence"], 6)


if __name__ == "__main__":
    unittest.main()
