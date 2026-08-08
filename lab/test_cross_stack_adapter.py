#!/usr/bin/env python3
"""Contract tests for the Argo CD + Gatekeeper replication adapters."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from run_cross_stack_experiment import (
    CrossStackExperiment,
    ExperimentError,
    argo_configuration_state,
    artifact_authorization_state,
    classify_evidence,
    crd_is_established,
    desired_leaf_differences,
    gatekeeper_policy_state,
)


class CrossStackAdapterTests(unittest.TestCase):
    def test_argo_out_of_sync_is_configuration_inconsistent(self) -> None:
        application = {
            "status": {
                "sync": {"status": "OutOfSync", "revision": "abc123"},
                "health": {"status": "Healthy"},
                "resources": [
                    {
                        "kind": "Deployment",
                        "namespace": "payments",
                        "name": "payments",
                        "status": "OutOfSync",
                    }
                ],
            }
        }
        state, evidence = argo_configuration_state(application)
        self.assertEqual(state, "inconsistent")
        self.assertEqual(evidence["application_sync_status"], "OutOfSync")

    def test_argo_unknown_status_fails_closed(self) -> None:
        state, _ = argo_configuration_state({"status": {"sync": {"status": "Unknown"}}})
        self.assertEqual(state, "undecidable")

    def test_transient_null_collections_remain_undecidable(self) -> None:
        state, evidence = argo_configuration_state(
            {"status": {"sync": {"status": "Unknown"}, "resources": None}}
        )
        self.assertEqual(state, "undecidable")
        self.assertEqual(evidence["deployment_resource_statuses"], [])
        self.assertFalse(crd_is_established({"status": {"conditions": None}}))
        self.assertTrue(
            crd_is_established(
                {"status": {"conditions": [{"type": "Established", "status": "True"}]}}
            )
        )

    def test_desired_leaf_diff_reports_only_explicit_managed_leaves(self) -> None:
        expected = {"spec": {"replicas": 1, "template": {"cpu": "100m"}}}
        actual = {
            "spec": {
                "replicas": 1,
                "template": {"cpu": "999m", "serverDefault": True},
            },
            "status": {"availableReplicas": 1},
        }
        self.assertEqual(
            desired_leaf_differences(expected, actual),
            [{"path": "/spec/template/cpu", "expected": "100m", "actual": "999m"}],
        )

    def test_api_read_failure_is_counted_and_recorded(self) -> None:
        class FailingReadExperiment(CrossStackExperiment):
            def kubectl_json(self, *args: str, check: bool = True) -> dict:  # type: ignore[override]
                raise ExperimentError("synthetic API read failure")

        with tempfile.TemporaryDirectory() as temporary:
            experiment = FailingReadExperiment(output_dir=Path(temporary))
            self.assertEqual(experiment.safe_json("get", "deployment", "payments"), {})
            self.assertEqual(experiment.api_read_errors, 1)
            events = [
                json.loads(line)
                for line in (Path(temporary) / "install_events.ndjson").read_text().splitlines()
            ]
            self.assertEqual(events[0]["record_type"], "api_read_error")

    def test_gatekeeper_violation_uses_engine_emitted_object_uid(self) -> None:
        constraint = {
            "status": {
                "auditTimestamp": "2026-08-08T12:00:02Z",
                "totalViolations": 1,
                "violations": [
                    {
                        "group": "apps",
                        "version": "v1",
                        "kind": "Deployment",
                        "namespace": "payments",
                        "name": "payments",
                        "message": (
                            "govdrift-resource-uid=uid-payments-1; "
                            "label missing"
                        ),
                        "enforcementAction": "dryrun",
                    }
                ],
            }
        }
        state, evidence = gatekeeper_policy_state(
            constraint,
            deployment_uid="uid-payments-1",
            not_before_utc="2026-08-08T12:00:00+00:00",
        )
        self.assertEqual(state, "inconsistent")
        self.assertTrue(evidence["gatekeeper_emitted_resource_uid"])
        self.assertFalse(evidence["gatekeeper_structural_uid_field"])
        self.assertEqual(evidence["joined_resource_uid"], "uid-payments-1")
        self.assertEqual(
            evidence["uid_join_source"],
            "gatekeeper-policy-message-embedded-object-uid",
        )

    def test_gatekeeper_uid_absence_or_mismatch_is_undecidable(self) -> None:
        base = {
            "status": {
                "auditTimestamp": "2026-08-08T12:00:02Z",
                "totalViolations": 1,
                "violations": [
                    {
                        "group": "apps",
                        "version": "v1",
                        "kind": "Deployment",
                        "namespace": "payments",
                        "name": "payments",
                        "message": "label missing",
                        "enforcementAction": "dryrun",
                    }
                ],
            }
        }
        state, _ = gatekeeper_policy_state(
            base,
            deployment_uid="uid-payments-1",
            not_before_utc="2026-08-08T12:00:00Z",
        )
        self.assertEqual(state, "undecidable")
        base["status"]["violations"][0]["message"] = (
            "govdrift-resource-uid=uid-payments-2; label missing"
        )
        state, _ = gatekeeper_policy_state(
            base,
            deployment_uid="uid-payments-1",
            not_before_utc="2026-08-08T12:00:00Z",
        )
        self.assertEqual(state, "undecidable")

    def test_stale_gatekeeper_audit_is_undecidable(self) -> None:
        constraint = {
            "status": {
                "auditTimestamp": "2026-08-08T11:59:59Z",
                "totalViolations": 0,
            }
        }
        state, evidence = gatekeeper_policy_state(
            constraint,
            deployment_uid="uid-payments-1",
            not_before_utc="2026-08-08T12:00:00Z",
        )
        self.assertEqual(state, "undecidable")
        self.assertFalse(evidence["audit_fresh_for_injection"])

    def test_uncovered_digest_is_shared_authorization_signal(self) -> None:
        pods = {
            "items": [
                {
                    "metadata": {"name": "payments-a", "uid": "pod-1"},
                    "status": {
                        "containerStatuses": [
                            {
                                "name": "payments",
                                "ready": True,
                                "image": "govdrift.local/payments:1.0",
                                "imageID": "docker.io/library/nginx@sha256:alternate",
                            }
                        ]
                    },
                }
            ]
        }
        state, evidence = artifact_authorization_state(
            pods,
            approved_digests={"docker.io/library/nginx@sha256:approved"},
        )
        self.assertEqual(state, "inconsistent")
        self.assertFalse(evidence["independent_argocd_gatekeeper_authorization_validation"])

    def test_classifier_does_not_claim_intent_or_environment_replication(self) -> None:
        application = {"status": {"sync": {"status": "Synced"}, "resources": []}}
        constraint = {
            "status": {
                "auditTimestamp": "2026-08-08T12:00:02Z",
                "totalViolations": 0,
            }
        }
        deployment = {"metadata": {"uid": "deployment-uid"}}
        pods = {
            "items": [
                {
                    "metadata": {"name": "payments-a", "uid": "pod-1"},
                    "status": {
                        "containerStatuses": [
                            {
                                "name": "payments",
                                "ready": True,
                                "imageID": "digest-alternate",
                            }
                        ]
                    },
                }
            ]
        }
        result = classify_evidence(
            application,
            constraint,
            deployment,
            pods,
            approved_digests={"digest-approved"},
            policy_not_before_utc="2026-08-08T12:00:00Z",
        )
        self.assertEqual(result["observed_set"], ["authorization"])
        self.assertEqual(result["components"]["intent"], "not_evaluated")
        self.assertEqual(result["components"]["environment"], "not_evaluated")
        self.assertEqual(
            result["scope"]["authorization"],
            "shared-adapter-not-independently-replicated",
        )


if __name__ == "__main__":
    unittest.main()
