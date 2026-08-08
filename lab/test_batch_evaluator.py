#!/usr/bin/env python3
"""Contract tests for the in-memory batch scaling evaluator."""

from __future__ import annotations

import unittest
from dataclasses import replace

try:
    from batch_evaluator import (
        AuthorizationRecord,
        BatchEvaluator,
        ContainerEvidence,
        EvidenceBundle,
        ExternalCallPlan,
        PodEvidence,
        PreparedEvidence,
        UnitRef,
        build_synthetic_evidence,
        with_policy_failure,
        with_unauthorized_digest,
    )
except ImportError:  # Supports ``python -m unittest lab.test_batch_evaluator``.
    from lab.batch_evaluator import (
        AuthorizationRecord,
        BatchEvaluator,
        ContainerEvidence,
        EvidenceBundle,
        ExternalCallPlan,
        PodEvidence,
        PreparedEvidence,
        UnitRef,
        build_synthetic_evidence,
        with_policy_failure,
        with_unauthorized_digest,
    )


def evidence_for(units: tuple[UnitRef, ...]) -> EvidenceBundle:
    desired = {}
    observed = {}
    policy = {}
    revisions = {}
    approvals = []
    pods = []
    approved_environment = {}
    observed_environment = {}
    for index, unit in enumerate(units):
        revision = f"rev-{index}"
        digest = f"sha256:main-{index}"
        sidecar = f"sha256:sidecar-{index}"
        projection = {"image": "service:1", "owner": unit.namespace}
        desired[unit] = projection
        observed[unit] = projection
        policy[unit] = True
        revisions[unit] = revision
        approvals.append(
            AuthorizationRecord(
                approval_id=f"APR-{index}",
                scope=unit,
                revisions=frozenset({revision}),
                subjects=frozenset({digest, sidecar}),
            )
        )
        pods.append(
            PodEvidence(
                unit,
                f"pod-{index}-a",
                (ContainerEvidence("main", digest), ContainerEvidence("sidecar", sidecar)),
            )
        )
        pods.append(
            PodEvidence(
                unit,
                f"pod-{index}-b",
                (ContainerEvidence("main", digest), ContainerEvidence("sidecar", sidecar)),
            )
        )
        approved_environment[unit] = {"region": "east", "iam": {"scope": "least"}}
        observed_environment[unit] = {
            "region": "east",
            "iam": {"scope": "least"},
            "unrecorded": "ignored",
        }
    return EvidenceBundle(
        desired=desired,
        observed=observed,
        policy_compliance=policy,
        current_revision=revisions,
        approvals=tuple(approvals),
        pods=tuple(pods),
        approved_environment=approved_environment,
        observed_environment=observed_environment,
    )


class BatchEvaluatorContract(unittest.TestCase):
    def setUp(self) -> None:
        self.evaluator = BatchEvaluator()

    def test_batch_baseline_is_consistent(self) -> None:
        units, bundle = build_synthetic_evidence(25)
        verdicts = self.evaluator.evaluate(units, bundle)
        self.assertEqual(len(verdicts), 25)
        self.assertTrue(all(item.verdict == "consistent" for item in verdicts.values()))

    def test_namespace_is_part_of_identity(self) -> None:
        units = (UnitRef("blue", "api", "uid-blue"), UnitRef("green", "api", "uid-green"))
        verdicts = self.evaluator.evaluate(units, evidence_for(units))
        self.assertEqual({unit.namespace for unit in verdicts}, {"blue", "green"})
        self.assertTrue(all(item.verdict == "consistent" for item in verdicts.values()))

    def test_recreated_uid_does_not_inherit_old_approval(self) -> None:
        old = UnitRef("blue", "api", "uid-old")
        new = UnitRef("blue", "api", "uid-new")
        old_bundle = evidence_for((old,))
        digest = "sha256:main-0"
        bundle = EvidenceBundle(
            desired={new: old_bundle.desired[old]},
            observed={new: old_bundle.observed[old]},
            policy_compliance={new: True},
            current_revision={new: "rev-0"},
            approvals=old_bundle.approvals,
            pods=(PodEvidence(new, "pod-new", (ContainerEvidence("main", digest),)),),
            approved_environment={new: old_bundle.approved_environment[old]},
            observed_environment={new: old_bundle.observed_environment[old]},
        )
        verdict = self.evaluator.evaluate((new,), bundle)[new]
        self.assertEqual(verdict.components["authorization"], "undecidable")
        self.assertEqual(verdict.components["intent"], "undecidable")
        self.assertIn("evidence", verdict.class_set)

    def test_approval_for_another_scope_cannot_authorize_target(self) -> None:
        target = UnitRef("blue", "api", "uid-target")
        other = UnitRef("blue", "worker", "uid-other")
        bundle = evidence_for((target, other))
        target_approval = next(item for item in bundle.approvals if item.scope == target)
        approvals = tuple(item for item in bundle.approvals if item.scope != target) + (
            replace(target_approval, approval_id="APR-wrong", scope=other),
        )
        verdict = self.evaluator.evaluate((target,), replace(bundle, approvals=approvals))[target]
        self.assertEqual(verdict.components["authorization"], "undecidable")
        self.assertEqual(verdict.components["intent"], "undecidable")

    def test_every_active_pod_and_container_digest_is_checked(self) -> None:
        units, bundle = build_synthetic_evidence(1)
        modified = with_unauthorized_digest(bundle, units)
        verdict = self.evaluator.evaluate(units, modified)[units[0]]
        self.assertEqual(verdict.components["authorization"], "inconsistent")
        self.assertEqual(verdict.drift_set, ("authorization",))

    def test_missing_digest_is_epistemically_undecidable(self) -> None:
        units, bundle = build_synthetic_evidence(1)
        last = bundle.pods[-1]
        containers = tuple((*last.containers[:-1], replace(last.containers[-1], image_digest=None)))
        modified = replace(bundle, pods=tuple((*bundle.pods[:-1], replace(last, containers=containers))))
        verdict = self.evaluator.evaluate(units, modified)[units[0]]
        self.assertEqual(verdict.components["authorization"], "undecidable")
        self.assertIn("evidence", verdict.class_set)

    def test_terminating_pod_is_not_running_lineage(self) -> None:
        units, bundle = build_synthetic_evidence(1)
        terminating = PodEvidence(
            units[0], "pod-terminating", (ContainerEvidence("main", "sha256:bad"),), True
        )
        verdict = self.evaluator.evaluate(units, replace(bundle, pods=(*bundle.pods, terminating)))[units[0]]
        self.assertEqual(verdict.verdict, "consistent")

    def test_one_shot_proof_does_not_require_live_status(self) -> None:
        units, bundle = build_synthetic_evidence(1)
        approval = replace(
            bundle.approvals[0], mode="one-shot", live_status_available=False
        )
        verdict = self.evaluator.evaluate(units, replace(bundle, approvals=(approval,)))[units[0]]
        self.assertEqual(verdict.verdict, "consistent")

    def test_continuing_authority_without_live_status_is_undecidable(self) -> None:
        units, bundle = build_synthetic_evidence(1)
        approval = replace(bundle.approvals[0], live_status_available=False)
        verdict = self.evaluator.evaluate(units, replace(bundle, approvals=(approval,)))[units[0]]
        self.assertEqual(verdict.components["authorization"], "undecidable")
        self.assertEqual(verdict.components["intent"], "undecidable")

    def test_total_and_subset_fanout_have_exact_vectors(self) -> None:
        units, bundle = build_synthetic_evidence(25)
        total = self.evaluator.evaluate(units, with_policy_failure(bundle, units))
        self.assertTrue(all(item.drift_set == ("policy",) for item in total.values()))
        subset = units[::5]
        modified = with_unauthorized_digest(bundle, subset)
        prepared = PreparedEvidence.build(modified)
        affected = self.evaluator.evaluate_prepared(subset, prepared)
        unaffected = self.evaluator.evaluate_prepared(
            tuple(unit for unit in units if unit not in set(subset)), prepared
        )
        self.assertTrue(all(item.drift_set == ("authorization",) for item in affected.values()))
        self.assertTrue(all(item.verdict == "consistent" for item in unaffected.values()))

    def test_external_call_counts_are_explicit_models(self) -> None:
        plan = ExternalCallPlan()
        self.assertEqual(plan.batch_calls(1), 6)
        self.assertEqual(plan.batch_calls(1000), 6)
        self.assertEqual(plan.naive_per_unit_calls(1000), 6000)


if __name__ == "__main__":
    unittest.main()
