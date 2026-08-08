#!/usr/bin/env python3
"""Dependency-free in-memory batch evaluator for scaling experiments.

This module deliberately does not contact Kubernetes, Git, a policy engine, or
an approval service.  It models the semantic work performed *after* batched
evidence has arrived.  The companion experiment therefore reports decision
and indexing throughput, while external fetch counts are an explicit call-plan
model rather than measured API traffic.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from typing import Any, Literal


Status = Literal["consistent", "inconsistent", "undecidable"]
COMPONENTS = ("configuration", "policy", "authorization", "intent", "environment")
PRIORITY = ("configuration", "policy", "intent", "authorization", "environment", "evidence")


@dataclass(frozen=True, order=True, slots=True)
class UnitRef:
    """Stable workload identity; name alone is intentionally insufficient."""

    namespace: str
    name: str
    uid: str


@dataclass(frozen=True, slots=True)
class ContainerEvidence:
    name: str
    image_digest: str | None


@dataclass(frozen=True, slots=True)
class PodEvidence:
    unit: UnitRef
    pod_uid: str
    containers: tuple[ContainerEvidence, ...]
    terminating: bool = False


@dataclass(frozen=True, slots=True)
class AuthorizationRecord:
    """Approval proof and live-status projection, scoped to one exact UID."""

    approval_id: str
    scope: UnitRef
    revisions: frozenset[str]
    subjects: frozenset[str]
    mode: str = "continuing"
    proof_available: bool = True
    live_status_available: bool = True
    valid_at_execution: bool = True
    revoked: bool = False
    revocation_effect: str = "prospective"
    expires_at: int | None = None


@dataclass(frozen=True, slots=True)
class EvidenceBundle:
    """One batched, already-decoded evidence snapshot for an estate."""

    desired: Mapping[UnitRef, Mapping[str, Any]]
    observed: Mapping[UnitRef, Mapping[str, Any]]
    policy_compliance: Mapping[UnitRef, bool | None]
    current_revision: Mapping[UnitRef, str | None]
    approvals: tuple[AuthorizationRecord, ...]
    pods: tuple[PodEvidence, ...]
    approved_environment: Mapping[UnitRef, Mapping[str, Any]]
    observed_environment: Mapping[UnitRef, Mapping[str, Any]]


@dataclass(frozen=True, slots=True)
class Verdict:
    verdict: str
    class_set: tuple[str, ...]
    drift_set: tuple[str, ...]
    undecidable_components: tuple[str, ...]
    components: Mapping[str, Status]

    def to_dict(self) -> dict[str, Any]:
        return {
            "verdict": self.verdict,
            "class_set": list(self.class_set),
            "drift_set": list(self.drift_set),
            "undecidable_components": list(self.undecidable_components),
            "components": dict(self.components),
        }


@dataclass(frozen=True, slots=True)
class ExternalCallPlan:
    """Architectural call-count model; no calls are made by this module."""

    batch_stream_fetches: tuple[str, ...] = (
        "desired-state batch",
        "deployment batch",
        "policy-result batch",
        "approval-and-basis batch",
        "pod-lineage batch",
        "environment-inventory batch",
    )
    per_unit_stream_fetches: int = 6

    def batch_calls(self, _units: int) -> int:
        return len(self.batch_stream_fetches)

    def naive_per_unit_calls(self, units: int) -> int:
        return self.per_unit_stream_fetches * units


@dataclass(frozen=True, slots=True)
class PreparedEvidence:
    bundle: EvidenceBundle
    approvals_by_scope: Mapping[UnitRef, tuple[AuthorizationRecord, ...]]
    pods_by_scope: Mapping[UnitRef, tuple[PodEvidence, ...]]

    @classmethod
    def build(cls, bundle: EvidenceBundle) -> "PreparedEvidence":
        approvals: dict[UnitRef, list[AuthorizationRecord]] = defaultdict(list)
        for record in bundle.approvals:
            approvals[record.scope].append(record)
        pods: dict[UnitRef, list[PodEvidence]] = defaultdict(list)
        for pod in bundle.pods:
            pods[pod.unit].append(pod)
        return cls(
            bundle=bundle,
            approvals_by_scope={key: tuple(value) for key, value in approvals.items()},
            pods_by_scope={key: tuple(value) for key, value in pods.items()},
        )


def _violated_recorded_equalities(
    approved: Mapping[str, Any], observed: Mapping[str, Any], prefix: str = ""
) -> list[str]:
    violations: list[str] = []
    for key, expected in approved.items():
        path = f"{prefix}.{key}" if prefix else key
        if key not in observed:
            violations.append(path)
        elif isinstance(expected, Mapping) and isinstance(observed[key], Mapping):
            violations.extend(_violated_recorded_equalities(expected, observed[key], path))
        elif observed[key] != expected:
            violations.append(path)
    return violations


def _authorization_state(record: AuthorizationRecord, now: int) -> bool | None:
    """True=applicable, False=known inapplicable, None=not decidable."""

    if not record.proof_available:
        return None
    if record.mode == "one-shot":
        if record.revocation_effect == "retroactive" and not record.live_status_available:
            return None
        if record.revoked and record.revocation_effect == "retroactive":
            return False
        return bool(record.valid_at_execution)
    if record.mode in {"continuing", "temporary-exception"}:
        if not record.live_status_available:
            return None
        if record.revoked:
            return False
        return record.expires_at is None or now < record.expires_at
    raise ValueError(f"unknown authorization mode: {record.mode}")


def _intent_state(record: AuthorizationRecord) -> bool | None:
    """True=execution-valid lineage, False=invalid lineage, None=unknown."""

    if not record.proof_available:
        return None
    if record.mode not in {"one-shot", "continuing", "temporary-exception"}:
        raise ValueError(f"unknown authorization mode: {record.mode}")
    if not record.valid_at_execution:
        return False
    if record.revocation_effect != "retroactive":
        return True
    if not record.live_status_available:
        return None
    return not record.revoked


def _combine(left: Status, right: Status) -> Status:
    if "inconsistent" in {left, right}:
        return "inconsistent"
    if "undecidable" in {left, right}:
        return "undecidable"
    return "consistent"


class BatchEvaluator:
    """Evaluate any number of exact UnitRefs from one evidence snapshot."""

    def __init__(self, *, now: int = 2_000_000_000) -> None:
        self.now = now

    def evaluate(self, units: Sequence[UnitRef], bundle: EvidenceBundle) -> dict[UnitRef, Verdict]:
        """Build indices and execute a complete in-memory sweep."""

        return self.evaluate_prepared(units, PreparedEvidence.build(bundle))

    def evaluate_prepared(
        self, units: Sequence[UnitRef], prepared: PreparedEvidence
    ) -> dict[UnitRef, Verdict]:
        return {unit: self.evaluate_unit(unit, prepared) for unit in units}

    def evaluate_unit(self, unit: UnitRef, prepared: PreparedEvidence) -> Verdict:
        evidence = prepared.bundle
        components: dict[str, Status] = {name: "consistent" for name in COMPONENTS}

        if unit not in evidence.desired or unit not in evidence.observed:
            components["configuration"] = "undecidable"
        elif evidence.desired[unit] != evidence.observed[unit]:
            components["configuration"] = "inconsistent"

        policy = evidence.policy_compliance.get(unit)
        if policy is None:
            components["policy"] = "undecidable"
        elif not policy:
            components["policy"] = "inconsistent"

        records = prepared.approvals_by_scope.get(unit, ())
        known_applicable: list[AuthorizationRecord] = []
        unknown_authorization = False
        intent_applicable: list[AuthorizationRecord] = []
        unknown_intent = False
        for record in records:
            state = _authorization_state(record, self.now)
            if state is True:
                known_applicable.append(record)
            elif state is None:
                unknown_authorization = True
            intent_state = _intent_state(record)
            if intent_state is True:
                intent_applicable.append(record)
            elif intent_state is None:
                unknown_intent = True

        if not records:
            authorization_status: Status = "undecidable"
        elif known_applicable:
            authorization_status = "consistent"
        elif unknown_authorization:
            authorization_status = "undecidable"
        else:
            authorization_status = "inconsistent"

        revision = evidence.current_revision.get(unit)
        if revision is None or not records:
            components["intent"] = "undecidable"
        elif any(revision in record.revisions for record in intent_applicable):
            components["intent"] = "consistent"
        elif unknown_intent:
            components["intent"] = "undecidable"
        else:
            components["intent"] = "inconsistent"

        active_pods = [pod for pod in prepared.pods_by_scope.get(unit, ()) if not pod.terminating]
        digests: list[str] = []
        lineage_missing = not active_pods
        for pod in active_pods:
            if not pod.containers:
                lineage_missing = True
            for container in pod.containers:
                if container.image_digest is None:
                    lineage_missing = True
                else:
                    digests.append(container.image_digest)
        if not records:
            lineage_status = "undecidable"
        elif lineage_missing or not digests:
            lineage_status: Status = "undecidable"
        elif all(
            any(digest in record.subjects for record in known_applicable)
            for digest in digests
        ):
            lineage_status = "consistent"
        elif unknown_authorization:
            lineage_status = "undecidable"
        else:
            lineage_status = "inconsistent"
        components["authorization"] = _combine(authorization_status, lineage_status)

        approved_environment = evidence.approved_environment.get(unit)
        observed_environment = evidence.observed_environment.get(unit)
        if approved_environment is None or observed_environment is None:
            components["environment"] = "undecidable"
        elif _violated_recorded_equalities(approved_environment, observed_environment):
            components["environment"] = "inconsistent"

        drift_set = tuple(name for name in COMPONENTS if components[name] == "inconsistent")
        undecidable = tuple(name for name in COMPONENTS if components[name] == "undecidable")
        classes = tuple((*drift_set, *(("evidence",) if undecidable else ())))
        first = next((name for name in PRIORITY if name in classes), None)
        verdict = "drift" if drift_set else ("undecidable" if undecidable else "consistent")
        # Priority is deliberately derivable from class_set; retaining the
        # calculation here exercises the same total ordering as the live path.
        if first is not None and first not in classes:  # pragma: no cover - defensive invariant
            raise AssertionError("priority projection escaped the class set")
        return Verdict(verdict, classes, drift_set, undecidable, components)


def build_synthetic_evidence(
    units_count: int, *, seed: int = 20260808
) -> tuple[tuple[UnitRef, ...], EvidenceBundle]:
    """Create deterministic, conforming evidence with two pods/two containers per unit."""

    if units_count < 1:
        raise ValueError("units_count must be positive")
    units: list[UnitRef] = []
    desired: dict[UnitRef, Mapping[str, Any]] = {}
    observed: dict[UnitRef, Mapping[str, Any]] = {}
    policy: dict[UnitRef, bool | None] = {}
    revisions: dict[UnitRef, str | None] = {}
    approvals: list[AuthorizationRecord] = []
    pods: list[PodEvidence] = []
    approved_environment: dict[UnitRef, Mapping[str, Any]] = {}
    observed_environment: dict[UnitRef, Mapping[str, Any]] = {}

    for index in range(units_count):
        unit = UnitRef(
            namespace=f"team-{index % 17:02d}",
            name=f"service-{index:04d}",
            uid=f"uid-{seed:x}-{index:06d}",
        )
        units.append(unit)
        main_digest = f"sha256:{seed:08x}{index:056x}"
        sidecar_digest = f"sha256:{seed ^ 0xA5A5A5A5:08x}{index:056x}"
        revision = f"rev-{seed:x}-{index:06d}"
        projection = {
            "labels": {"app": unit.name, "owner": unit.namespace},
            "containers": (
                ("main", main_digest, "100m", "64Mi"),
                ("telemetry", sidecar_digest, "25m", "32Mi"),
            ),
        }
        desired[unit] = projection
        observed[unit] = projection
        policy[unit] = True
        revisions[unit] = revision
        approvals.append(
            AuthorizationRecord(
                approval_id=f"APR-{index:06d}",
                scope=unit,
                revisions=frozenset({revision}),
                subjects=frozenset({main_digest, sidecar_digest}),
            )
        )
        for pod_index in range(2):
            pods.append(
                PodEvidence(
                    unit=unit,
                    pod_uid=f"pod-{index:06d}-{pod_index}",
                    containers=(
                        ContainerEvidence("main", main_digest),
                        ContainerEvidence("telemetry", sidecar_digest),
                    ),
                )
            )
        environment = {
            "region": f"region-{index % 3}",
            "iam": {"scope": "least-privilege"},
            "exposure": "private",
        }
        approved_environment[unit] = environment
        # Extra, unrecorded inventory must not create a false violation.
        observed_environment[unit] = {**environment, "inventory_generation": index}

    bundle = EvidenceBundle(
        desired=desired,
        observed=observed,
        policy_compliance=policy,
        current_revision=revisions,
        approvals=tuple(approvals),
        pods=tuple(pods),
        approved_environment=approved_environment,
        observed_environment=observed_environment,
    )
    return tuple(units), bundle


def with_policy_failure(bundle: EvidenceBundle, units: Sequence[UnitRef]) -> EvidenceBundle:
    policy = dict(bundle.policy_compliance)
    for unit in units:
        policy[unit] = False
    return replace(bundle, policy_compliance=policy)


def with_unauthorized_digest(bundle: EvidenceBundle, units: Sequence[UnitRef]) -> EvidenceBundle:
    """Change the last container of the last active pod for each selected unit."""

    selected = set(units)
    last_pod = {
        unit: max(
            (pod.pod_uid for pod in bundle.pods if pod.unit == unit and not pod.terminating),
            default="",
        )
        for unit in selected
    }
    pods: list[PodEvidence] = []
    for pod in bundle.pods:
        if pod.unit in selected and pod.pod_uid == last_pod[pod.unit] and pod.containers:
            containers = list(pod.containers)
            containers[-1] = replace(
                containers[-1], image_digest=f"sha256:unauthorized-{pod.unit.uid}"
            )
            pod = replace(pod, containers=tuple(containers))
        pods.append(pod)
    return replace(bundle, pods=tuple(pods))
