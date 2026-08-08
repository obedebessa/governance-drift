#!/usr/bin/env python3
"""Deterministic fault-contract tests for honest evaluator degradation."""

from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import evaluator


DEPLOYMENT = {
    "metadata": {"namespace": "payments", "name": "payments", "uid": "uid-payments",
                 "labels": {"app": "payments", "env": "prod", "team-owner": "payments"}},
    "spec": {"template": {"metadata": {"labels": {"app": "payments"}}, "spec": {
        "containers": [{"name": "payments", "image": "example:1", "imagePullPolicy": "IfNotPresent"}]
    }}},
}
POLICY_PASS = json.dumps({"items": [{
    "metadata": {"namespace": "payments"},
    "scope": {"kind": "Deployment", "name": "payments", "namespace": "payments",
              "uid": "uid-payments"},
    "results": [{"policy": "governance-baseline", "rule": "owner", "result": "pass"}],
}]})
APPROVAL = {"id": "APR-test", "mode": "one-shot", "valid_at_execution": True,
            "revisions": ["rev-ok"], "subjects": ["sha256:approved"]}


def completed(stdout: str = "", returncode: int = 0) -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess([], returncode, stdout=stdout, stderr="")


class EvaluatorContract(unittest.TestCase):
    def setUp(self) -> None:
        self._basis_temp = tempfile.TemporaryDirectory()
        self._basis_path = Path(self._basis_temp.name)
        (self._basis_path / "policy.json").write_text(json.dumps({
            "metadata": {"labels": {"policy-version": "pi-7"}}
        }))
        self._basis_patch = patch.object(
            evaluator, "select_basis_directory", return_value=self._basis_path
        )
        self._basis_patch.start()

    def tearDown(self) -> None:
        self._basis_patch.stop()
        self._basis_temp.cleanup()

    def test_fresh_ordered_evidence_envelope_is_accepted(self) -> None:
        envelope = {
            "subject": "deployment/payments", "sequence": 11,
            "captured_at": 100.0, "delivered_at": 101.0,
        }
        self.assertEqual(
            evaluator.validate_evidence_envelope(
                envelope, now=102.0, expected_subject="deployment/payments",
                last_sequence=10, max_age_seconds=5.0,
                max_transport_delay_seconds=2.0,
            ),
            (True, "accepted"),
        )

    def test_stale_evidence_envelope_is_rejected(self) -> None:
        envelope = {
            "subject": "deployment/payments", "sequence": 11,
            "captured_at": 90.0, "delivered_at": 91.0,
        }
        accepted, reason = evaluator.validate_evidence_envelope(
            envelope, now=102.0, expected_subject="deployment/payments",
            last_sequence=10, max_age_seconds=5.0,
            max_transport_delay_seconds=2.0,
        )
        self.assertFalse(accepted)
        self.assertEqual(reason, "stale evidence")

    def test_excessively_delayed_evidence_envelope_is_rejected(self) -> None:
        envelope = {
            "subject": "deployment/payments", "sequence": 11,
            "captured_at": 100.0, "delivered_at": 104.0,
        }
        accepted, reason = evaluator.validate_evidence_envelope(
            envelope, now=104.0, expected_subject="deployment/payments",
            last_sequence=10, max_age_seconds=10.0,
            max_transport_delay_seconds=2.0,
        )
        self.assertFalse(accepted)
        self.assertEqual(reason, "evidence transport delay exceeded")

    def test_reordered_evidence_envelope_is_rejected(self) -> None:
        envelope = {
            "subject": "deployment/payments", "sequence": 9,
            "captured_at": 100.0, "delivered_at": 101.0,
        }
        accepted, reason = evaluator.validate_evidence_envelope(
            envelope, now=102.0, expected_subject="deployment/payments",
            last_sequence=10, max_age_seconds=5.0,
            max_transport_delay_seconds=2.0,
        )
        self.assertFalse(accepted)
        self.assertEqual(reason, "duplicate or reordered evidence")

    def test_evidence_subject_mismatch_is_rejected(self) -> None:
        envelope = {
            "subject": "deployment/other", "sequence": 11,
            "captured_at": 100.0, "delivered_at": 101.0,
        }
        accepted, reason = evaluator.validate_evidence_envelope(
            envelope, now=102.0, expected_subject="deployment/payments",
            last_sequence=10, max_age_seconds=5.0,
            max_transport_delay_seconds=2.0,
        )
        self.assertFalse(accepted)
        self.assertEqual(reason, "evidence subject mismatch")

    def test_environment_predicates_ignore_unrecorded_fields(self) -> None:
        approved = {"iam": {"scope": "least-privilege"}}
        observed = {"iam": {"scope": "least-privilege"}, "region": "us-east-1"}
        self.assertEqual(evaluator.violated_environment_equalities(approved, observed), [])
        observed["iam"]["scope"] = "admin"
        self.assertEqual(
            evaluator.violated_environment_equalities(approved, observed), ["iam.scope"]
        )

    def test_configuration_and_policy_outage_is_undecidable(self) -> None:
        with patch.object(evaluator, "kubectl_json", side_effect=RuntimeError("api down")), \
             patch.object(evaluator.subprocess, "run", return_value=completed(returncode=1)):
            result = evaluator.evaluate("T1")
        self.assertEqual(result["verdict"], "undecidable")
        self.assertEqual(set(result["undecidable_components"]), {"configuration", "policy"})
        self.assertEqual(result["class_set"], ["evidence"])

    def test_malformed_policy_report_is_undecidable(self) -> None:
        with patch.object(evaluator, "kubectl_json", return_value=DEPLOYMENT), \
             patch.object(evaluator.subprocess, "run", return_value=completed("not-json")):
            result = evaluator.evaluate("T1")
        self.assertEqual(result["components"]["configuration"], "consistent")
        self.assertEqual(result["components"]["policy"], "undecidable")
        self.assertTrue(result["evidence_drift"])

    def test_policy_report_for_wrong_uid_is_ignored(self) -> None:
        wrong = json.dumps({"items": [{
            "metadata": {"namespace": "payments"},
            "scope": {"kind": "Deployment", "name": "payments",
                      "namespace": "payments", "uid": "uid-reused-name"},
            "results": [{"policy": "governance-baseline", "rule": "owner",
                         "result": "pass"}],
        }]})
        with patch.object(evaluator, "kubectl_json", return_value=DEPLOYMENT), \
             patch.object(evaluator.subprocess, "run", return_value=completed(wrong)):
            result = evaluator.evaluate("T1")
        self.assertEqual(result["components"]["policy"], "undecidable")
        self.assertIn("evidence", result["class_set"])

    def test_missing_approval_basis_makes_authorization_and_intent_undecidable(self) -> None:
        with patch.object(evaluator, "kubectl_json", return_value=DEPLOYMENT), \
             patch.object(evaluator.subprocess, "run", return_value=completed(POLICY_PASS)), \
             patch.object(evaluator, "approval_proofs", return_value=[]), \
             patch.object(evaluator, "approvals", return_value=[]):
            result = evaluator.evaluate("T2")
        self.assertEqual(
            set(result["undecidable_components"]), {"authorization", "intent"}
        )
        self.assertEqual(result["class_set"], ["evidence"])

    def test_fresh_uncovered_digest_is_authorization_drift(self) -> None:
        pods = {"items": [{"status": {"containerStatuses": [{
            "ready": True, "imageID": "example@sha256:uncovered"
        }]}}]}
        calls = iter((DEPLOYMENT, DEPLOYMENT, pods))
        with patch.object(evaluator, "kubectl_json", side_effect=lambda *args: next(calls)), \
             patch.object(evaluator.subprocess, "run", return_value=completed(POLICY_PASS)), \
             patch.object(evaluator, "approval_proofs", return_value=[APPROVAL]), \
             patch.object(evaluator, "approvals", return_value=[APPROVAL]), \
             patch.object(evaluator, "command", return_value="rev-ok\n"):
            result = evaluator.evaluate("T3")
        self.assertEqual(result["verdict"], "drift")
        self.assertEqual(result["drift_set"], ["authorization"])
        self.assertFalse(result["evidence_drift"])

    def test_mixed_rollout_checks_every_pod_digest(self) -> None:
        pods = {"items": [
            {"status": {"containerStatuses": [{
                "ready": True, "imageID": "example@sha256:approved"
            }]}},
            {"status": {"containerStatuses": [{
                "ready": True, "imageID": "example@sha256:uncovered"
            }]}},
        ]}
        calls = iter((DEPLOYMENT, DEPLOYMENT, pods))
        with patch.object(evaluator, "kubectl_json", side_effect=lambda *args: next(calls)), \
             patch.object(evaluator.subprocess, "run", return_value=completed(POLICY_PASS)), \
             patch.object(evaluator, "approval_proofs", return_value=[APPROVAL]), \
             patch.object(evaluator, "approvals", return_value=[APPROVAL]), \
             patch.object(evaluator, "command", return_value="rev-ok\n"):
            result = evaluator.evaluate("T3")
        self.assertEqual(result["drift_set"], ["authorization"])
        self.assertIn("sha256:uncovered", result["detail"])

    def test_every_container_digest_is_checked(self) -> None:
        pods = {"items": [{"status": {"containerStatuses": [
            {"name": "app", "ready": True, "imageID": "example@sha256:approved"},
            {"name": "sidecar", "ready": True, "imageID": "helper@sha256:uncovered"},
        ]}}]}
        calls = iter((DEPLOYMENT, DEPLOYMENT, pods))
        with patch.object(evaluator, "kubectl_json", side_effect=lambda *args: next(calls)), \
             patch.object(evaluator.subprocess, "run", return_value=completed(POLICY_PASS)), \
             patch.object(evaluator, "approval_proofs", return_value=[APPROVAL]), \
             patch.object(evaluator, "approvals", return_value=[APPROVAL]), \
             patch.object(evaluator, "command", return_value="rev-ok\n"):
            result = evaluator.evaluate("T3")
        self.assertEqual(result["drift_set"], ["authorization"])
        self.assertIn("sha256:uncovered", result["detail"])

    def test_duplicate_valid_approval_is_idempotent(self) -> None:
        pods = {"items": [{"status": {"containerStatuses": [{
            "ready": True, "imageID": "example@sha256:approved"
        }]}}]}
        calls = iter((DEPLOYMENT, DEPLOYMENT, pods))
        with patch.object(evaluator, "kubectl_json", side_effect=lambda *args: next(calls)), \
             patch.object(evaluator.subprocess, "run", return_value=completed(POLICY_PASS)), \
             patch.object(evaluator, "approval_proofs", return_value=[APPROVAL, APPROVAL]), \
             patch.object(evaluator, "approvals", return_value=[APPROVAL, APPROVAL]), \
             patch.object(evaluator, "command", return_value="rev-ok\n"):
            result = evaluator.evaluate("T3")
        self.assertEqual(result["verdict"], "consistent")
        self.assertEqual(result["class_set"], [])

    def test_authenticated_retroactive_revocation_is_drift_not_missing_evidence(self) -> None:
        revoked = {**APPROVAL, "revoked": True, "revocation_effect": "retroactive"}
        with patch.object(evaluator, "kubectl_json", return_value=DEPLOYMENT), \
             patch.object(evaluator.subprocess, "run", return_value=completed(POLICY_PASS)), \
             patch.object(evaluator, "approval_proofs", return_value=[APPROVAL]), \
             patch.object(evaluator, "approvals", return_value=[revoked]), \
             patch.object(evaluator, "command", return_value="rev-ok\n"):
            result = evaluator.evaluate("T2")
        self.assertEqual(result["verdict"], "drift")
        self.assertEqual(set(result["drift_set"]), {"authorization", "intent"})
        self.assertFalse(result["evidence_drift"])

    def test_missing_live_source_preserves_prospective_one_shot_proof(self) -> None:
        with patch.object(evaluator, "kubectl_json", return_value=DEPLOYMENT), \
             patch.object(evaluator.subprocess, "run", return_value=completed(POLICY_PASS)), \
             patch.object(evaluator, "approval_proofs", return_value=[APPROVAL]), \
             patch.object(evaluator, "approvals", return_value=[]), \
             patch.object(evaluator, "command", return_value="rev-ok\n"):
            result = evaluator.evaluate("T2")
        self.assertEqual(result["verdict"], "consistent")

    def test_missing_live_source_makes_continuing_authorization_undecidable(self) -> None:
        continuing = {**APPROVAL, "mode": "continuing"}
        with patch.object(evaluator, "kubectl_json", return_value=DEPLOYMENT), \
             patch.object(evaluator.subprocess, "run", return_value=completed(POLICY_PASS)), \
             patch.object(evaluator, "approval_proofs", return_value=[continuing]), \
             patch.object(evaluator, "approvals", return_value=[]), \
             patch.object(evaluator, "command", return_value="rev-ok\n"):
            result = evaluator.evaluate("T2")
        self.assertEqual(result["verdict"], "undecidable")
        self.assertEqual(set(result["undecidable_components"]), {"authorization"})
        self.assertEqual(result["components"]["intent"], "consistent")
        self.assertIn("immutable proof retained but live status unavailable", result["detail"])
        self.assertNotIn("approval basis unavailable", result["detail"])

    def test_unapproved_revision_remains_intent_drift_when_live_status_is_missing(self) -> None:
        continuing = {**APPROVAL, "mode": "continuing"}
        with patch.object(evaluator, "kubectl_json", return_value=DEPLOYMENT), \
             patch.object(evaluator.subprocess, "run", return_value=completed(POLICY_PASS)), \
             patch.object(evaluator, "approval_proofs", return_value=[continuing]), \
             patch.object(evaluator, "approvals", return_value=[]), \
             patch.object(evaluator, "command", return_value="rev-unapproved\n"):
            result = evaluator.evaluate("T2")
        self.assertEqual(set(result["class_set"]), {"intent", "evidence"})
        self.assertEqual(result["components"]["intent"], "inconsistent")
        self.assertEqual(result["components"]["authorization"], "undecidable")

    def test_unrelated_expired_exception_does_not_alarm(self) -> None:
        unrelated = {
            "id": "EXC-other", "kind": "emergency-exception",
            "mode": "temporary-exception", "subject": "deployment/ledger",
            "expires_utc": 0, "valid_at_execution": True, "revoked": False,
        }
        with patch.object(evaluator, "kubectl_json", return_value=DEPLOYMENT), \
             patch.object(evaluator.subprocess, "run", return_value=completed(POLICY_PASS)), \
             patch.object(evaluator, "approval_proofs", return_value=[APPROVAL, unrelated]), \
             patch.object(evaluator, "approvals", return_value=[APPROVAL, unrelated]), \
             patch.object(evaluator, "command", return_value="rev-ok\n"):
            result = evaluator.evaluate("T2")
        self.assertEqual(result["verdict"], "consistent")

    def test_missing_environment_inventory_is_undecidable(self) -> None:
        pods = {"items": [{"status": {"containerStatuses": [{
            "ready": True, "imageID": "example@sha256:approved"
        }]}}]}
        calls = iter((DEPLOYMENT, DEPLOYMENT, pods))
        with tempfile.TemporaryDirectory() as temp_dir, \
             patch.object(evaluator, "RUNTIME", Path(temp_dir)), \
             patch.object(evaluator, "kubectl_json", side_effect=lambda *args: next(calls)), \
             patch.object(evaluator.subprocess, "run", return_value=completed(POLICY_PASS)), \
             patch.object(evaluator, "approval_proofs", return_value=[APPROVAL]), \
             patch.object(evaluator, "approvals", return_value=[APPROVAL]), \
             patch.object(evaluator, "command", return_value="rev-ok\n"):
            result = evaluator.evaluate("T4")
        self.assertEqual(result["components"]["environment"], "undecidable")
        self.assertEqual(result["class_set"], ["evidence"])


if __name__ == "__main__":
    unittest.main()
