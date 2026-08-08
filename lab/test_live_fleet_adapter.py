#!/usr/bin/env python3
"""Unit tests for the live-fleet Kubernetes object adapter."""

from __future__ import annotations

import copy
import unittest
from dataclasses import replace

try:
    from batch_evaluator import BatchEvaluator, PreparedEvidence
    from run_live_fleet_experiment import (
        NAMESPACE,
        build_evidence_bundle,
        deployment_object,
        fetch_command,
        freeze_approvals,
        image_digest,
        normalize_snapshot,
        project_deployment,
    )
except ImportError:  # Supports namespace-package unittest invocation.
    from lab.batch_evaluator import BatchEvaluator, PreparedEvidence
    from lab.run_live_fleet_experiment import (
        NAMESPACE,
        build_evidence_bundle,
        deployment_object,
        fetch_command,
        freeze_approvals,
        image_digest,
        normalize_snapshot,
        project_deployment,
    )


def deployment(index: int = 0, *, namespace: str = NAMESPACE, uid: str = "deploy-uid") -> dict:
    item = deployment_object(index)
    item["metadata"]["namespace"] = namespace
    item["metadata"]["uid"] = uid
    item["status"] = {
        "observedGeneration": 1,
        "updatedReplicas": 1,
        "readyReplicas": 1,
        "availableReplicas": 1,
    }
    item["metadata"]["generation"] = 1
    return item


def pod(
    index: int = 0,
    *,
    namespace: str = NAMESPACE,
    uid: str = "pod-uid",
    include_all_container_kinds: bool = False,
) -> dict:
    name = f"fleet-{index:04d}"
    spec = {
        "containers": [
            {"name": "main"},
            {"name": "lineage-sidecar"},
        ]
    }
    status = {
        "containerStatuses": [
            {"name": "main", "ready": True, "imageID": "image@sha256:main"},
            {"name": "lineage-sidecar", "ready": True, "imageID": "image@sha256:side"},
        ]
    }
    if include_all_container_kinds:
        spec["initContainers"] = [{"name": "init"}]
        spec["ephemeralContainers"] = [{"name": "debug"}]
        status["initContainerStatuses"] = [
            {"name": "init", "ready": True, "imageID": "image@sha256:init"}
        ]
        status["ephemeralContainerStatuses"] = [
            {"name": "debug", "ready": True, "imageID": "image@sha256:debug"}
        ]
    return {
        "apiVersion": "v1",
        "kind": "Pod",
        "metadata": {
            "name": f"{name}-pod",
            "namespace": namespace,
            "uid": uid,
            "labels": {
                "app.kubernetes.io/name": "govdrift-fleet",
                "govdrift.io/unit": name,
            },
        },
        "spec": spec,
        "status": status,
    }


class LiveFleetAdapterContract(unittest.TestCase):
    def test_fetch_is_one_command_for_two_resource_lists(self) -> None:
        command = fetch_command()
        self.assertEqual(command.count("deployments,pods"), 1)
        self.assertIn("--request-timeout=30s", command)

    def test_namespace_and_real_uid_define_unit(self) -> None:
        blue = deployment(namespace="blue", uid="uid-blue")
        green = deployment(namespace="green", uid="uid-green")
        document = {"items": [blue, green, pod(namespace="blue"), pod(namespace="green")]}
        fleet = normalize_snapshot(document, expected_namespace="blue")
        self.assertEqual(len(fleet.units), 1)
        self.assertEqual(fleet.units[0].namespace, "blue")
        self.assertEqual(fleet.units[0].uid, "uid-blue")

    def test_adapter_preserves_regular_init_and_ephemeral_containers(self) -> None:
        document = {"items": [deployment(), pod(include_all_container_kinds=True)]}
        fleet = normalize_snapshot(document)
        self.assertEqual(fleet.active_pods, 1)
        self.assertEqual(fleet.active_containers, 4)
        names = {item.name for item in fleet.pods[0].containers}
        self.assertEqual(names, {"main", "lineage-sidecar", "init", "debug"})
        self.assertEqual(
            {item.image_digest for item in fleet.pods[0].containers},
            {"sha256:main", "sha256:side", "sha256:init", "sha256:debug"},
        )

    def test_replica_and_restart_metadata_are_outside_projection(self) -> None:
        before = deployment()
        after = copy.deepcopy(before)
        after["spec"]["replicas"] = 9
        after["spec"]["template"]["metadata"]["annotations"] = {
            "kubectl.kubernetes.io/restartedAt": "2026-08-08T00:00:00Z"
        }
        self.assertEqual(project_deployment(before), project_deployment(after))

    def test_scoped_approvals_and_all_container_digests_form_consistent_bundle(self) -> None:
        item = deployment()
        fleet = normalize_snapshot({"items": [item, pod()]})
        approvals = freeze_approvals(fleet)
        desired = {item["metadata"]["name"]: project_deployment(item)}
        bundle = build_evidence_bundle(fleet, desired, approvals, "namespace-uid")
        verdict = BatchEvaluator().evaluate(fleet.units, bundle)[fleet.units[0]]
        self.assertEqual(verdict.verdict, "consistent")
        self.assertEqual(approvals[0].scope, fleet.units[0])
        self.assertEqual(approvals[0].subjects, frozenset({"sha256:main", "sha256:side"}))

    def test_recreated_uid_cannot_reuse_frozen_approval(self) -> None:
        old_item = deployment(uid="uid-old")
        old_fleet = normalize_snapshot({"items": [old_item, pod(uid="pod-old")]})
        approvals = freeze_approvals(old_fleet)

        new_item = deployment(uid="uid-new")
        new_fleet = normalize_snapshot({"items": [new_item, pod(uid="pod-new")]})
        desired = {new_item["metadata"]["name"]: project_deployment(new_item)}
        bundle = build_evidence_bundle(new_fleet, desired, approvals, "namespace-uid")
        verdict = BatchEvaluator().evaluate(new_fleet.units, bundle)[new_fleet.units[0]]
        self.assertEqual(verdict.components["authorization"], "undecidable")
        self.assertEqual(verdict.components["intent"], "undecidable")

    def test_seeded_policy_delta_uses_captured_unitref(self) -> None:
        item = deployment()
        fleet = normalize_snapshot({"items": [item, pod()]})
        approvals = freeze_approvals(fleet)
        desired = {item["metadata"]["name"]: project_deployment(item)}
        bundle = build_evidence_bundle(fleet, desired, approvals, "namespace-uid")
        policy = dict(bundle.policy_compliance)
        policy[fleet.units[0]] = False
        prepared = PreparedEvidence.build(replace(bundle, policy_compliance=policy))
        verdict = BatchEvaluator().evaluate_prepared(fleet.units, prepared)[fleet.units[0]]
        self.assertEqual(verdict.drift_set, ("policy",))

    def test_digest_parser_keeps_digest_identity(self) -> None:
        self.assertEqual(image_digest("repo/name@sha256:abc"), "sha256:abc")
        self.assertEqual(image_digest("docker-pullable://repo/name@sha256:def"), "sha256:def")
        self.assertIsNone(image_digest(None))


if __name__ == "__main__":
    unittest.main()
