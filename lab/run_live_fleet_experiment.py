#!/usr/bin/env python3
"""Exercise the batch evaluator through real Kubernetes Deployment/Pod objects.

This is an adapter-and-object-path experiment on one local Kind cluster.  It is
not a Flux/Kyverno experiment and does not benchmark controller convergence,
production API capacity, approval services, or inventory services.  Each timed
sweep issues one ``kubectl get deployments,pods`` command, which Kubernetes
serves as one LIST per resource kind (two API LIST operations), then decodes and
normalizes the response before running the in-memory BatchEvaluator.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import os
import platform
import random
import subprocess
import sys
import time
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from batch_evaluator import (
    AuthorizationRecord,
    BatchEvaluator,
    ContainerEvidence,
    EvidenceBundle,
    PodEvidence,
    PreparedEvidence,
    UnitRef,
)


LAB = Path(__file__).resolve().parent
RESULTS = LAB / "results_live_fleet"
NAMESPACE = "govdrift-fleet"
MANAGED_LABEL = "app.kubernetes.io/name=govdrift-fleet"
CHURN_LABEL = "govdrift.io/churn=true"
IMAGE = "localhost:5001/governance-demo:1.0"
SIZES = (10, 50, 100)
SEED = 20260808
SWEEPS = 20
RAW_FIELDS = (
    "n",
    "sweep",
    "phase",
    "fetch_ms",
    "parse_ms",
    "core_ms",
    "total_ms",
    "units_per_second",
    "deployments",
    "active_pods",
    "active_containers",
    "response_bytes",
    "exact_vectors",
    "expected_vectors",
    "epistemic_vectors",
    "api_command_count",
    "modeled_api_list_operations",
    "api_error",
    "error_detail",
    "policy_fanout_units",
    "policy_fanout_core_ms",
    "policy_fanout_exact_vectors",
)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class CommandError(RuntimeError):
    pass


@dataclass(slots=True)
class CommandRunner:
    api_errors: list[dict[str, Any]]

    def run(self, *args: str, timeout: float = 180) -> str:
        proc = subprocess.run(args, text=True, capture_output=True, timeout=timeout)
        if proc.returncode:
            error = {
                "utc": utc_now(),
                "command": list(args),
                "returncode": proc.returncode,
                "stderr": proc.stderr[-2000:],
            }
            self.api_errors.append(error)
            raise CommandError(
                f"command failed ({proc.returncode}): {' '.join(args)}: {proc.stderr.strip()}"
            )
        return proc.stdout


@dataclass(frozen=True, slots=True)
class NormalizedFleet:
    units: tuple[UnitRef, ...]
    observed: dict[UnitRef, dict[str, Any]]
    revisions: dict[UnitRef, str | None]
    pods: tuple[PodEvidence, ...]
    active_pods: int
    active_containers: int


def deployment_object(index: int) -> dict[str, Any]:
    name = f"fleet-{index:04d}"
    churn = "true" if index % 5 == 0 else "false"
    labels = {
        "app.kubernetes.io/name": "govdrift-fleet",
        "govdrift.io/unit": name,
        "govdrift.io/revision": "fleet-r1",
        "govdrift.io/churn": churn,
    }
    pod_labels = {
        "app.kubernetes.io/name": "govdrift-fleet",
        "govdrift.io/unit": name,
        "govdrift.io/revision": "fleet-r1",
    }
    return {
        "apiVersion": "apps/v1",
        "kind": "Deployment",
        "metadata": {"name": name, "namespace": NAMESPACE, "labels": labels},
        "spec": {
            "replicas": 1,
            "strategy": {
                "type": "RollingUpdate",
                "rollingUpdate": {"maxSurge": 1, "maxUnavailable": 0},
            },
            "selector": {"matchLabels": {"govdrift.io/unit": name}},
            "template": {
                "metadata": {"labels": pod_labels},
                "spec": {
                    "terminationGracePeriodSeconds": 1,
                    "containers": [
                        {
                            "name": "main",
                            "image": IMAGE,
                            "imagePullPolicy": "IfNotPresent",
                            "resources": {
                                "requests": {"cpu": "2m", "memory": "8Mi"},
                                "limits": {"cpu": "50m", "memory": "32Mi"},
                            },
                        },
                        {
                            "name": "lineage-sidecar",
                            "image": IMAGE,
                            "imagePullPolicy": "IfNotPresent",
                            "command": ["/bin/sh", "-c", "sleep 86400"],
                            "resources": {
                                "requests": {"cpu": "1m", "memory": "4Mi"},
                                "limits": {"cpu": "20m", "memory": "16Mi"},
                            },
                        },
                    ],
                },
            },
        },
    }


def write_manifest(n: int, output: Path) -> tuple[Path, dict[str, dict[str, Any]]]:
    items = [deployment_object(index) for index in range(n)]
    path = output / f"manifest-n{n:04d}.json"
    path.write_text(json.dumps({"apiVersion": "v1", "kind": "List", "items": items}, indent=2) + "\n")
    desired = {item["metadata"]["name"]: project_deployment(item) for item in items}
    return path, desired


def project_deployment(obj: dict[str, Any]) -> dict[str, Any]:
    metadata = obj.get("metadata", {})
    labels = metadata.get("labels", {})
    containers = obj["spec"]["template"]["spec"].get("containers", [])
    return {
        "identity_labels": {
            key: labels.get(key)
            for key in ("app.kubernetes.io/name", "govdrift.io/unit", "govdrift.io/revision")
        },
        "containers": tuple(
            (
                row.get("name"),
                row.get("image"),
                tuple(row.get("command", [])),
                tuple(row.get("args", [])),
                row.get("resources", {}),
            )
            for row in containers
        ),
    }


def image_digest(image_id: str | None) -> str | None:
    if not image_id:
        return None
    if "@" in image_id:
        return image_id.split("@", 1)[1]
    marker = image_id.find("sha256:")
    return image_id[marker:] if marker >= 0 else image_id


def normalize_snapshot(document: dict[str, Any], expected_namespace: str = NAMESPACE) -> NormalizedFleet:
    deployments: dict[tuple[str, str], dict[str, Any]] = {}
    pods_raw: list[dict[str, Any]] = []
    for item in document.get("items", []):
        kind = item.get("kind")
        namespace = item.get("metadata", {}).get("namespace")
        if namespace != expected_namespace:
            continue
        if kind == "Deployment":
            key = (namespace, item["metadata"]["name"])
            deployments[key] = item
        elif kind == "Pod":
            pods_raw.append(item)

    units_by_name: dict[tuple[str, str], UnitRef] = {}
    observed: dict[UnitRef, dict[str, Any]] = {}
    revisions: dict[UnitRef, str | None] = {}
    for (namespace, name), item in deployments.items():
        uid = item.get("metadata", {}).get("uid")
        if not uid:
            raise ValueError(f"deployment {namespace}/{name} has no UID")
        unit = UnitRef(namespace, name, str(uid))
        units_by_name[(namespace, name)] = unit
        observed[unit] = project_deployment(item)
        revisions[unit] = item.get("metadata", {}).get("labels", {}).get("govdrift.io/revision")

    pods: list[PodEvidence] = []
    active_pods = active_containers = 0
    for item in pods_raw:
        metadata = item.get("metadata", {})
        namespace = metadata.get("namespace")
        deployment_name = metadata.get("labels", {}).get("govdrift.io/unit")
        unit = units_by_name.get((namespace, deployment_name))
        if unit is None:
            continue
        terminating = bool(metadata.get("deletionTimestamp"))
        status = item.get("status", {})
        status_by_name: dict[str, dict[str, Any]] = {}
        for field in ("initContainerStatuses", "containerStatuses", "ephemeralContainerStatuses"):
            for row in status.get(field, []) or []:
                status_by_name[row.get("name")] = row
        specifications: list[dict[str, Any]] = []
        spec = item.get("spec", {})
        for field in ("initContainers", "containers", "ephemeralContainers"):
            specifications.extend(spec.get(field, []) or [])
        containers = tuple(
            ContainerEvidence(
                str(row.get("name")),
                image_digest(status_by_name.get(row.get("name"), {}).get("imageID")),
            )
            for row in specifications
        )
        pods.append(
            PodEvidence(
                unit=unit,
                pod_uid=str(metadata.get("uid", "missing-pod-uid")),
                containers=containers,
                terminating=terminating,
            )
        )
        if not terminating:
            active_pods += 1
            active_containers += len(containers)

    return NormalizedFleet(
        units=tuple(sorted(units_by_name.values())),
        observed=observed,
        revisions=revisions,
        pods=tuple(pods),
        active_pods=active_pods,
        active_containers=active_containers,
    )


def freeze_approvals(fleet: NormalizedFleet) -> tuple[AuthorizationRecord, ...]:
    subjects: dict[UnitRef, set[str]] = {unit: set() for unit in fleet.units}
    for pod in fleet.pods:
        if pod.terminating:
            continue
        for container in pod.containers:
            if container.image_digest is None:
                raise ValueError(f"cannot freeze approval: missing digest in {pod.pod_uid}")
            subjects[pod.unit].add(container.image_digest)
    records = []
    for unit in fleet.units:
        revision = fleet.revisions.get(unit)
        if not revision or not subjects[unit]:
            raise ValueError(f"cannot freeze approval for {unit}")
        records.append(
            AuthorizationRecord(
                approval_id=f"APR-LIVE-{unit.uid}",
                scope=unit,
                revisions=frozenset({revision}),
                subjects=frozenset(subjects[unit]),
            )
        )
    return tuple(records)


def build_evidence_bundle(
    fleet: NormalizedFleet,
    desired_by_name: dict[str, dict[str, Any]],
    approvals: tuple[AuthorizationRecord, ...],
    namespace_uid: str,
) -> EvidenceBundle:
    desired: dict[UnitRef, dict[str, Any]] = {}
    policy: dict[UnitRef, bool | None] = {}
    approved_environment: dict[UnitRef, dict[str, Any]] = {}
    observed_environment: dict[UnitRef, dict[str, Any]] = {}
    for unit in fleet.units:
        if unit.name not in desired_by_name:
            raise ValueError(f"desired projection missing for {unit.name}")
        desired[unit] = desired_by_name[unit.name]
        policy[unit] = True
        environment = {
            "cluster": "kind-govdrift-lab",
            "namespace_uid": namespace_uid,
            "namespace": unit.namespace,
        }
        approved_environment[unit] = environment
        observed_environment[unit] = {**environment, "capture": "live-object-path"}
    return EvidenceBundle(
        desired=desired,
        observed=fleet.observed,
        policy_compliance=policy,
        current_revision=fleet.revisions,
        approvals=approvals,
        pods=fleet.pods,
        approved_environment=approved_environment,
        observed_environment=observed_environment,
    )


def fetch_command(namespace: str = NAMESPACE) -> tuple[str, ...]:
    return (
        "kubectl",
        "-n",
        namespace,
        "get",
        "deployments,pods",
        "-l",
        MANAGED_LABEL,
        "-o",
        "json",
        "--request-timeout=30s",
    )


def capture(runner: CommandRunner) -> tuple[str, float]:
    started = time.perf_counter_ns()
    payload = runner.run(*fetch_command(), timeout=45)
    return payload, (time.perf_counter_ns() - started) / 1_000_000


def wait_ready(runner: CommandRunner, expected_deployments: int, timeout: float) -> dict[str, Any]:
    started = time.monotonic()
    polls = 0
    last = ""
    while time.monotonic() - started < timeout:
        polls += 1
        last = runner.run(*fetch_command(), timeout=45)
        document = json.loads(last)
        deployments = [item for item in document.get("items", []) if item.get("kind") == "Deployment"]
        pods = [
            item for item in document.get("items", [])
            if item.get("kind") == "Pod" and not item.get("metadata", {}).get("deletionTimestamp")
        ]
        ready_deployments = 0
        for item in deployments:
            desired = int(item.get("spec", {}).get("replicas", 0))
            status = item.get("status", {})
            generation = int(item.get("metadata", {}).get("generation", 0))
            if (
                int(status.get("observedGeneration", 0)) >= generation
                and int(status.get("updatedReplicas", 0)) == desired
                and int(status.get("readyReplicas", 0)) == desired
                and int(status.get("availableReplicas", 0)) == desired
            ):
                ready_deployments += 1
        containers_ready = all(
            status.get("ready") and status.get("imageID")
            for pod in pods
            for status in (pod.get("status", {}).get("containerStatuses", []) or [])
        )
        declared = sum(len(pod.get("spec", {}).get("containers", [])) for pod in pods)
        materialized = sum(len(pod.get("status", {}).get("containerStatuses", []) or []) for pod in pods)
        if (
            len(deployments) == expected_deployments
            and ready_deployments == expected_deployments
            and pods
            and containers_ready
            and materialized == declared
        ):
            return {
                "seconds": time.monotonic() - started,
                "polls": polls,
                "deployments": len(deployments),
                "pods": len(pods),
                "containers": materialized,
            }
        time.sleep(1)
    raise TimeoutError(
        f"fleet did not settle within {timeout}s (expected deployments={expected_deployments})"
    )


def docker_stats() -> dict[str, Any]:
    proc = subprocess.run(
        [
            "docker",
            "stats",
            "--no-stream",
            "--format",
            "{{json .}}",
            "govdrift-lab-control-plane",
        ],
        text=True,
        capture_output=True,
        timeout=20,
    )
    if proc.returncode:
        return {"available": False, "error": proc.stderr.strip(), "utc": utc_now()}
    row = json.loads(proc.stdout)
    parse_percent = lambda value: float(str(value).rstrip("%"))
    return {
        "available": True,
        "utc": utc_now(),
        "cpu_percent": parse_percent(row.get("CPUPerc", "0%")),
        "memory_percent": parse_percent(row.get("MemPerc", "0%")),
        "memory_usage": row.get("MemUsage"),
        "pids": row.get("PIDs"),
    }


def enforce_resource_stop(resource_checks: list[dict[str, Any]], stage: str) -> None:
    first = docker_stats()
    first["stage"] = stage
    resource_checks.append(first)
    if not first.get("available"):
        return
    if float(first["memory_percent"]) >= 85.0:
        raise RuntimeError(f"stop rule: node memory reached {first['memory_percent']:.1f}%")
    if float(first["cpu_percent"]) >= 90.0:
        time.sleep(2)
        second = docker_stats()
        second["stage"] = stage + "-cpu-confirmation"
        resource_checks.append(second)
        if second.get("available") and float(second["cpu_percent"]) >= 90.0:
            raise RuntimeError("stop rule: node CPU remained at or above 90%")


def platform_snapshot(runner: CommandRunner) -> dict[str, Any]:
    server = json.loads(runner.run("kubectl", "version", "-o", "json", timeout=30))
    node = json.loads(
        runner.run("kubectl", "get", "node", "govdrift-lab-control-plane", "-o", "json", timeout=30)
    )
    return {
        "host_os": platform.platform(),
        "host_machine": platform.machine(),
        "logical_cpus": os.cpu_count(),
        "python": sys.version.split()[0],
        "kubernetes_client": server["clientVersion"]["gitVersion"],
        "kubernetes_server": server["serverVersion"]["gitVersion"],
        "node_capacity": node.get("status", {}).get("capacity", {}),
        "node_allocatable": node.get("status", {}).get("allocatable", {}),
        "container_image": IMAGE,
        "initial_node_stats": docker_stats(),
    }


def namespace_exists() -> bool:
    proc = subprocess.run(
        ["kubectl", "get", "namespace", NAMESPACE, "-o", "name"],
        text=True,
        capture_output=True,
        timeout=20,
    )
    return proc.returncode == 0


def phase_for_sweep(sweep: int) -> str:
    if sweep < 8:
        return "baseline"
    if sweep < 14:
        return "post-scale"
    return "post-restart"


def run_sweep(
    *,
    n: int,
    sweep: int,
    desired: dict[str, dict[str, Any]],
    approvals: tuple[AuthorizationRecord, ...],
    namespace_uid: str,
    fanout_units: tuple[UnitRef, ...],
    runner: CommandRunner,
    evaluator: BatchEvaluator,
) -> dict[str, Any]:
    total_started = time.perf_counter_ns()
    payload, fetch_ms = capture(runner)
    parse_started = time.perf_counter_ns()
    document = json.loads(payload)
    fleet = normalize_snapshot(document)
    if len(fleet.units) != n:
        raise RuntimeError(f"expected {n} deployments, captured {len(fleet.units)}")
    bundle = build_evidence_bundle(fleet, desired, approvals, namespace_uid)
    parse_ms = (time.perf_counter_ns() - parse_started) / 1_000_000
    core_started = time.perf_counter_ns()
    verdicts = evaluator.evaluate(fleet.units, bundle)
    core_ms = (time.perf_counter_ns() - core_started) / 1_000_000
    total_ms = (time.perf_counter_ns() - total_started) / 1_000_000
    exact = sum(item.verdict == "consistent" for item in verdicts.values())
    epistemic = sum(bool(item.undecidable_components) for item in verdicts.values())

    # Policy-like fan-out is synthetic but operates on the just-captured real
    # UnitRefs and evidence. Structural approval/pod indices are reused.
    fanout_started = time.perf_counter_ns()
    policy = dict(bundle.policy_compliance)
    for unit in fanout_units:
        policy[unit] = False
    modified = replace(bundle, policy_compliance=policy)
    baseline_prepared = PreparedEvidence.build(bundle)
    modified_prepared = PreparedEvidence(
        bundle=modified,
        approvals_by_scope=baseline_prepared.approvals_by_scope,
        pods_by_scope=baseline_prepared.pods_by_scope,
    )
    fanout = evaluator.evaluate_prepared(fanout_units, modified_prepared)
    fanout_ms = (time.perf_counter_ns() - fanout_started) / 1_000_000
    fanout_exact = sum(item.drift_set == ("policy",) for item in fanout.values())
    return {
        "n": n,
        "sweep": sweep + 1,
        "phase": phase_for_sweep(sweep),
        "fetch_ms": fetch_ms,
        "parse_ms": parse_ms,
        "core_ms": core_ms,
        "total_ms": total_ms,
        "units_per_second": n / (total_ms / 1000),
        "deployments": len(fleet.units),
        "active_pods": fleet.active_pods,
        "active_containers": fleet.active_containers,
        "response_bytes": len(payload.encode("utf-8")),
        "exact_vectors": exact,
        "expected_vectors": n,
        "epistemic_vectors": epistemic,
        "api_command_count": 1,
        "modeled_api_list_operations": 2,
        "api_error": False,
        "error_detail": "",
        "policy_fanout_units": len(fanout_units),
        "policy_fanout_core_ms": fanout_ms,
        "policy_fanout_exact_vectors": fanout_exact,
    }


def write_outputs(output: Path, payload: dict[str, Any]) -> None:
    rows = payload["rows"]
    with (output / "live_fleet_raw.csv").open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=RAW_FIELDS, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)
    (output / "live_fleet_raw.json").write_text(json.dumps(payload, indent=2) + "\n")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", type=Path, default=RESULTS)
    parser.add_argument("--sweeps", type=int, default=SWEEPS)
    parser.add_argument("--readiness-timeout", type=float, default=240)
    parser.add_argument("--size-timeout", type=float, default=600)
    args = parser.parse_args()
    if args.sweeps != 20:
        raise SystemExit("the frozen live protocol requires exactly 20 sweeps per size")
    output = args.output_dir
    output.mkdir(parents=True, exist_ok=True)
    if namespace_exists():
        raise SystemExit(f"refusing to reuse existing namespace {NAMESPACE}")

    runner = CommandRunner([])
    evaluator = BatchEvaluator()
    rows: list[dict[str, Any]] = []
    actions: list[dict[str, Any]] = []
    resource_checks: list[dict[str, Any]] = []
    completed_sizes: list[int] = []
    stop_reason: str | None = None
    fatal_error: str | None = None
    cleanup: dict[str, Any] = {"attempted": False, "namespace": NAMESPACE}
    started = utc_now()
    platform_data = platform_snapshot(runner)
    source_hashes = {
        path.name: hashlib.sha256(path.read_bytes()).hexdigest()
        for path in (Path(__file__), LAB / "batch_evaluator.py")
    }

    try:
        namespace_manifest = output / "namespace.json"
        namespace_manifest.write_text(
            json.dumps(
                {
                    "apiVersion": "v1",
                    "kind": "Namespace",
                    "metadata": {
                        "name": NAMESPACE,
                        "labels": {"app.kubernetes.io/name": "govdrift-fleet"},
                    },
                },
                indent=2,
            )
            + "\n"
        )
        runner.run("kubectl", "apply", "-f", str(namespace_manifest), timeout=60)
        namespace_obj = json.loads(
            runner.run("kubectl", "get", "namespace", NAMESPACE, "-o", "json", timeout=30)
        )
        namespace_uid = str(namespace_obj["metadata"]["uid"])

        for n in SIZES:
            size_started = time.monotonic()
            manifest, desired = write_manifest(n, output)
            runner.run("kubectl", "apply", "-f", str(manifest), timeout=240)
            ready = wait_ready(runner, n, args.readiness_timeout)
            actions.append({"n": n, "action": "apply-and-ready", "utc": utc_now(), **ready})
            enforce_resource_stop(resource_checks, f"n={n}-ready")

            baseline_payload, _ = capture(runner)
            fleet = normalize_snapshot(json.loads(baseline_payload))
            approvals = freeze_approvals(fleet)
            rng = random.Random(SEED + n)
            fanout_count = max(1, math.ceil(n * 0.20))
            fanout_units = tuple(sorted(rng.sample(list(fleet.units), fanout_count)))

            for sweep in range(args.sweeps):
                if time.monotonic() - size_started > args.size_timeout:
                    raise RuntimeError(f"stop rule: n={n} exceeded {args.size_timeout}s")
                if sweep == 8:
                    action_started = time.monotonic()
                    runner.run(
                        "kubectl", "-n", NAMESPACE, "scale", "deployment",
                        "-l", CHURN_LABEL, "--replicas=2", timeout=120,
                    )
                    ready = wait_ready(runner, n, args.readiness_timeout)
                    actions.append(
                        {
                            "n": n,
                            "action": "scale-churn-cohort-1-to-2",
                            "cohort_fraction": 0.20,
                            "seconds_including_wait": time.monotonic() - action_started,
                            "utc": utc_now(),
                            **ready,
                        }
                    )
                    enforce_resource_stop(resource_checks, f"n={n}-post-scale")
                if sweep == 14:
                    action_started = time.monotonic()
                    runner.run(
                        "kubectl", "-n", NAMESPACE, "rollout", "restart", "deployment",
                        "-l", CHURN_LABEL, timeout=120,
                    )
                    ready = wait_ready(runner, n, args.readiness_timeout)
                    actions.append(
                        {
                            "n": n,
                            "action": "restart-churn-cohort",
                            "cohort_fraction": 0.20,
                            "seconds_including_wait": time.monotonic() - action_started,
                            "utc": utc_now(),
                            **ready,
                        }
                    )
                    enforce_resource_stop(resource_checks, f"n={n}-post-restart")

                row = run_sweep(
                    n=n,
                    sweep=sweep,
                    desired=desired,
                    approvals=approvals,
                    namespace_uid=namespace_uid,
                    fanout_units=fanout_units,
                    runner=runner,
                    evaluator=evaluator,
                )
                rows.append(row)
                if row["exact_vectors"] != n or row["epistemic_vectors"] != 0:
                    raise RuntimeError(f"stop rule: non-exact live vector at n={n}, sweep={sweep+1}")
                if row["policy_fanout_exact_vectors"] != row["policy_fanout_units"]:
                    raise RuntimeError(f"stop rule: non-exact policy fan-out at n={n}")
            completed_sizes.append(n)
            print(f"completed n={n}: {args.sweeps} sweeps", flush=True)
    except (CommandError, TimeoutError, RuntimeError, ValueError, json.JSONDecodeError) as exc:
        fatal_error = str(exc)
        stop_reason = str(exc)
    finally:
        cleanup_started = utc_now()
        cleanup["attempted"] = True
        cleanup["started_utc"] = cleanup_started
        proc = subprocess.run(
            [
                "kubectl", "delete", "namespace", NAMESPACE,
                "--wait=true", "--timeout=180s", "--ignore-not-found=true",
            ],
            text=True,
            capture_output=True,
            timeout=200,
        )
        cleanup.update(
            {
                "returncode": proc.returncode,
                "stdout": proc.stdout.strip(),
                "stderr": proc.stderr.strip(),
                "completed_utc": utc_now(),
                "namespace_absent_after": not namespace_exists(),
            }
        )
        (output / "cleanup.json").write_text(json.dumps(cleanup, indent=2) + "\n")

    payload = {
        "schema_version": 1,
        "experiment": "live Kind Kubernetes object-path plus in-memory BatchEvaluator",
        "measurement_scope": (
            "Each sweep uses one kubectl command that performs one Deployment LIST and one "
            "Pod LIST, then JSON decoding, adapter normalization, locally synthesized scoped "
            "approval/inventory evidence, and in-memory semantic evaluation. This is not a "
            "Flux/Kyverno full-stack, approval-service, inventory-service, or production test."
        ),
        "requested_sizes": list(SIZES),
        "completed_sizes": completed_sizes,
        "sweeps_per_completed_size": args.sweeps,
        "seed": SEED,
        "started_utc": started,
        "completed_utc": utc_now(),
        "status": "completed" if completed_sizes == list(SIZES) and fatal_error is None else "stopped",
        "stop_reason": stop_reason,
        "fatal_error": fatal_error,
        "stop_rules": {
            "node_memory_percent": 85.0,
            "node_cpu_percent": 90.0,
            "cpu_confirmation_samples": 2,
            "api_errors_allowed": 0,
            "readiness_timeout_seconds": args.readiness_timeout,
            "per_size_timeout_seconds": args.size_timeout,
            "semantic_errors_allowed": 0,
        },
        "churn_schedule": {
            "sweeps_1_8": "settled baseline",
            "before_sweep_9": "scale deterministic 20% cohort from one to two replicas",
            "sweeps_9_14": "settled post-scale",
            "before_sweep_15": "rollout restart the same cohort and wait for settlement",
            "sweeps_15_20": "settled post-restart",
            "concurrency_boundary": "mutations occur between phases; timed sweeps begin after settlement",
        },
        "policy_fanout": (
            "A seeded 20% subset of real captured UnitRefs receives an in-memory policy=false "
            "delta on every sweep; it does not mutate a live policy engine."
        ),
        "limitations": [
            "One local single-node Kind cluster and one host execution.",
            "Deployments and Pods are real; approvals, policy results, and environment inventory are locally synthesized after capture.",
            "Flux and Kyverno do not participate in the exclusive namespace.",
            "The two resource LIST operations are issued by one kubectl command; kubectl process startup is included in fetch time.",
            "Churn is phase-separated and settled before measurement, not concurrent with a sweep.",
            "No API saturation, multi-cluster behavior, field prevalence, or production effectiveness is estimated.",
        ],
        "platform": platform_data,
        "source_sha256": source_hashes,
        "actions": actions,
        "resource_checks": resource_checks,
        "api_errors": runner.api_errors,
        "cleanup": cleanup,
        "rows": rows,
    }
    write_outputs(output, payload)
    print(
        f"wrote {len(rows)} live sweeps; completed sizes={completed_sizes}; "
        f"cleanup_absent={cleanup.get('namespace_absent_after')}",
        flush=True,
    )
    if fatal_error:
        raise SystemExit(fatal_error)
    if not cleanup.get("namespace_absent_after"):
        raise SystemExit("exclusive namespace cleanup could not be verified")


if __name__ == "__main__":
    main()
