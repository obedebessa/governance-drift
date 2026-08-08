#!/usr/bin/env python3
"""Contract tests for the Argo CD + Gatekeeper replication adapters."""

from __future__ import annotations

import copy
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from analyze_cross_stack import (  # noqa: E402
    recompute_scenario_timing,
    validate_cleanup_proof,
)
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
    @staticmethod
    def timing_fixture() -> tuple[dict, list[dict]]:
        marker = {
            "record_type": "injection_onset_marker",
            "scenario": "S3",
            "repetition": 1,
            "schedule_index": 1,
            "injection_id": "S3:r1:schedule1",
            "timing_reference_id": "operational-onset:S3:r1:schedule1",
            "reference_kind": "operational-onset",
            "injection_utc": "2026-08-08T12:00:00+00:00",
            "onset_utc": "2026-08-08T12:00:00.100000+00:00",
            "injection_monotonic_seconds": 100.0,
            "onset_monotonic_seconds": 100.1,
            "actuation_seconds": 0.1,
        }
        components_undecidable = {
            "configuration": "consistent",
            "policy": "undecidable",
            "authorization": "consistent",
            "intent": "not_evaluated",
            "environment": "not_evaluated",
        }
        components_policy = {**components_undecidable, "policy": "inconsistent"}
        polls = [
            {
                "record_type": "scenario_poll",
                "scenario": "S3",
                "repetition": 1,
                "schedule_index": 1,
                "injection_id": marker["injection_id"],
                "timing_reference_id": marker["timing_reference_id"],
                "poll_index": 1,
                "elapsed_since_onset_seconds": 0.0,
                "evaluation_started_since_onset_seconds": 0.0,
                "evaluation_completed_since_onset_seconds": 0.2,
                "evaluation_duration_seconds": 0.2,
                "components": components_undecidable,
                "observed_set": [],
                "undecidable_components": ["policy"],
                "exact_set": False,
                "classification_at_completion": {
                    "components": components_undecidable,
                    "observed_set": [],
                    "undecidable_components": ["policy"],
                    "exact_set": False,
                },
            },
            {
                "record_type": "scenario_poll",
                "scenario": "S3",
                "repetition": 1,
                "schedule_index": 1,
                "injection_id": marker["injection_id"],
                "timing_reference_id": marker["timing_reference_id"],
                "poll_index": 2,
                "elapsed_since_onset_seconds": 1.0,
                "evaluation_started_since_onset_seconds": 1.0,
                "evaluation_completed_since_onset_seconds": 1.3,
                "evaluation_duration_seconds": 0.3,
                "components": components_policy,
                "observed_set": ["policy"],
                "undecidable_components": [],
                "exact_set": True,
                "classification_at_completion": {
                    "components": components_policy,
                    "observed_set": ["policy"],
                    "undecidable_components": [],
                    "exact_set": True,
                },
            },
        ]
        return marker, polls

    @staticmethod
    def cleanup_fixture() -> dict:
        verify_command = ["kind", "get", "clusters"]
        delete_command = [
            "kind", "delete", "cluster", "--name", "govdrift-cross"
        ]
        return {
            "cluster": "govdrift-cross",
            "target_scope": "only govdrift-cross",
            "delete_attempted": True,
            "delete_returncode": 0,
            "verified_absent": True,
            "cleanup_proof": {
                "verify_before": {
                    "command": verify_command,
                    "returncode": 0,
                    "stdout": "govdrift-cross\nother-cluster\n",
                    "stderr": "",
                    "clusters": ["govdrift-cross", "other-cluster"],
                },
                "delete": {
                    "command": delete_command,
                    "attempted": True,
                    "returncode": 0,
                    "stdout": "Deleted nodes: [govdrift-cross-control-plane]\n",
                    "stderr": "",
                },
                "verify_after": {
                    "command": verify_command,
                    "returncode": 0,
                    "stdout": "other-cluster\n",
                    "stderr": "",
                    "clusters": ["other-cluster"],
                },
            },
        }

    def test_raw_timing_recomputes_epistemic_substantive_and_exact(self) -> None:
        marker, polls = self.timing_fixture()
        timing = recompute_scenario_timing(marker, polls, {"policy"})
        self.assertEqual(
            timing["operational_onset_to_first_honest_seconds"], 0.2
        )
        self.assertEqual(timing["first_honest_verdict_kind"], "epistemic-only")
        self.assertEqual(timing["first_epistemic_alert_seconds"], 0.2)
        self.assertEqual(timing["first_substantive_alert_seconds"], 1.3)
        self.assertEqual(timing["exact_set_latency_seconds"], 1.3)

    def test_raw_timing_rejects_missing_or_tampered_fields(self) -> None:
        marker, polls = self.timing_fixture()
        missing = copy.deepcopy(polls)
        del missing[0]["evaluation_completed_since_onset_seconds"]
        with self.assertRaises(SystemExit):
            recompute_scenario_timing(marker, missing, {"policy"})

        bad_duration = copy.deepcopy(polls)
        bad_duration[0]["evaluation_duration_seconds"] = 0.3
        with self.assertRaises(SystemExit):
            recompute_scenario_timing(marker, bad_duration, {"policy"})

        bad_classification = copy.deepcopy(polls)
        bad_classification[0]["classification_at_completion"]["exact_set"] = True
        with self.assertRaises(SystemExit):
            recompute_scenario_timing(marker, bad_classification, {"policy"})

    def test_cleanup_proof_rejects_missing_or_tampered_evidence(self) -> None:
        valid = self.cleanup_fixture()
        validate_cleanup_proof(valid)

        wrong_target = copy.deepcopy(valid)
        wrong_target["cleanup_proof"]["delete"]["command"][-1] = "other-cluster"
        with self.assertRaises(SystemExit):
            validate_cleanup_proof(wrong_target)

        missing_stdout = copy.deepcopy(valid)
        del missing_stdout["cleanup_proof"]["verify_after"]["stdout"]
        with self.assertRaises(SystemExit):
            validate_cleanup_proof(missing_stdout)

        still_present = copy.deepcopy(valid)
        still_present["cleanup_proof"]["verify_after"]["stdout"] = (
            "govdrift-cross\nother-cluster\n"
        )
        still_present["cleanup_proof"]["verify_after"]["clusters"] = [
            "govdrift-cross",
            "other-cluster",
        ]
        with self.assertRaises(SystemExit):
            validate_cleanup_proof(still_present)

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

        synthetic_provenance = {
            "head": "0" * 40,
            "branch": "UNIT-TEST",
            "detached": False,
            "dirty": False,
            "modified_or_untracked_files": [],
            "capture_boundary": "campaign initialization before output mutation",
        }
        with tempfile.TemporaryDirectory() as temporary, patch(
            "run_cross_stack_experiment.capture_git_provenance",
            return_value=synthetic_provenance,
        ):
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
