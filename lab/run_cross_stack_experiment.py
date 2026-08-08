#!/usr/bin/env python3
"""Execute the bounded Argo CD + Gatekeeper cross-stack replication.

This is a controlled localhost/Kind experiment, not an equivalence test and
not a production-capacity claim. Kubernetes commands are bound to an isolated
kubeconfig and the ``kind-govdrift-cross`` context. The only cluster deleted
by this program is ``govdrift-cross``.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import traceback
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable


LAB = Path(__file__).resolve().parent
ROOT = LAB.parent
STACK = LAB / "stacks" / "argocd-gatekeeper"
RESULTS = LAB / "results_cross_stack"
CAMPAIGN_SOURCE_FILES = (
    LAB / "run_cross_stack_experiment.py",
    LAB / "test_cross_stack_adapter.py",
    ROOT / "scripts" / "analyze_cross_stack.py",
)
CLUSTER = "govdrift-cross"
CONTEXT = f"kind-{CLUSTER}"
NODE = f"{CLUSTER}-control-plane"
NODE_IMAGE = (
    "kindest/node:v1.36.1@"
    "sha256:3489c7674813ba5d8b1a9977baea8a6e553784dab7b84759d1014dbd78f7ebd5"
)
BASE_IMAGE = "nginx:1.27-alpine"
BASE_IMAGE_ID = "sha256:65645c7bb6a0661892a8b03b89d0743208a18dd2f3f17a54ef4b76fb8e2f2a10"
ALT_IMAGE = "nginx:1.26-alpine"
ALT_IMAGE_ID = "sha256:1eadbb07820339e8bbfed18c771691970baee292ec4ab2558f1453d26153e22d"
GIT_IMAGE = "alpine/git:latest"
GIT_IMAGE_ID = "sha256:729da2347ce652f30476b064198577fe12e1147e58499be9f343039343ef2cee"
BASE_NODE_REF = "govdrift.local/payments:baseline"
ALT_NODE_REF = "govdrift.local/payments:alternate"
LIVE_NODE_REF = "govdrift.local/payments:1.0"
POLL_SECONDS = 0.5
REPETITIONS = 5
INSTALL_TIMEOUT_SECONDS = 15 * 60
SCENARIO_TIMEOUT_SECONDS = {"S1": 90.0, "S3": 120.0, "S4": 90.0}
EXPECTED = {
    "S1": {"configuration"},
    "S3": {"policy"},
    "S4": {"authorization"},
}
SURFACE = {
    "S1": "argocd-native",
    "S3": "gatekeeper-native",
    "S4": "shared-artifact-adapter",
}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    )


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def parse_rfc3339(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


class ExperimentError(RuntimeError):
    pass


class StopRule(ExperimentError):
    pass


def command(
    args: list[str],
    *,
    input_text: str | None = None,
    timeout: float | None = None,
    check: bool = True,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    proc = subprocess.run(
        args,
        input=input_text,
        text=True,
        capture_output=True,
        timeout=timeout,
        env=env,
    )
    if check and proc.returncode:
        raise ExperimentError(
            f"command failed ({proc.returncode}): {' '.join(args)}\n"
            f"stdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
        )
    return proc


def capture_git_provenance() -> dict[str, Any]:
    """Capture the repository state before the campaign mutates its outputs."""
    head = command(
        ["git", "-C", str(ROOT), "rev-parse", "--verify", "HEAD"],
        timeout=15,
    ).stdout.strip()
    if not re.fullmatch(r"[0-9a-f]{40}", head):
        raise ExperimentError(f"invalid Git HEAD captured for campaign: {head!r}")

    branch_proc = command(
        ["git", "-C", str(ROOT), "symbolic-ref", "--quiet", "--short", "HEAD"],
        timeout=15,
        check=False,
    )
    if branch_proc.returncode not in {0, 1}:
        raise ExperimentError(
            "could not determine Git branch for campaign provenance: "
            f"{branch_proc.stderr.strip()}"
        )
    branch = branch_proc.stdout.strip() if branch_proc.returncode == 0 else "DETACHED"
    if not branch:
        raise ExperimentError("Git branch provenance was empty")

    modified = command(
        ["git", "-C", str(ROOT), "diff", "--name-only", "-z", "HEAD", "--"],
        # iCloud-backed workspaces can make an otherwise small index scan take
        # tens of seconds. Provenance is a hard gate, so allow the scan to
        # finish rather than weakening or skipping it.
        timeout=120,
    ).stdout.split("\0")
    untracked = command(
        [
            "git", "-C", str(ROOT), "ls-files", "--others",
            "--exclude-standard", "-z", "--",
        ],
        timeout=120,
    ).stdout.split("\0")
    dirty_files = sorted({path for path in modified + untracked if path})
    return {
        "head": head,
        "branch": branch,
        "detached": branch == "DETACHED",
        "dirty": bool(dirty_files),
        "modified_or_untracked_files": dirty_files,
        "capture_boundary": "campaign initialization before output mutation",
    }


class NdjsonRecorder:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text("")
        self._lock = threading.RLock()
        self._ordinal = 0

    def add(self, record_type: str, **fields: Any) -> dict[str, Any]:
        with self._lock:
            self._ordinal += 1
            row = {
                "ordinal": self._ordinal,
                "record_type": record_type,
                "recorded_utc": utc_now(),
                **fields,
            }
            with self.path.open("a") as handle:
                handle.write(canonical_json(row) + "\n")
            return row


def argo_configuration_state(application: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    """Interpret Argo's native desired/live synchronization status."""
    status = (application.get("status") or {}) if isinstance(application, dict) else {}
    sync = status.get("sync") or {}
    health = status.get("health") or {}
    resources = status.get("resources") or []
    resources = [
        row for row in resources
        if row.get("kind") == "Deployment"
        and row.get("name") == "payments"
        and row.get("namespace") == "payments"
    ]
    resource_statuses = [row.get("status") for row in resources]
    evidence = {
        "application_sync_status": sync.get("status"),
        "deployment_resource_statuses": resource_statuses,
        "application_health_status": health.get("status"),
        "revision": sync.get("revision"),
        "application_conditions": status.get("conditions", []) or [],
    }
    overall = evidence["application_sync_status"]
    if overall == "OutOfSync" or "OutOfSync" in resource_statuses:
        return "inconsistent", evidence
    if overall == "Synced" and all(value in {None, "Synced"} for value in resource_statuses):
        return "consistent", evidence
    return "undecidable", evidence


def crd_is_established(crd: dict[str, Any]) -> bool:
    """Return False for the transient null/missing conditions seen at creation."""
    if not isinstance(crd, dict):
        return False
    status = crd.get("status") or {}
    conditions = status.get("conditions") or []
    return any(
        isinstance(row, dict)
        and row.get("type") == "Established"
        and row.get("status") == "True"
        for row in conditions
    )


def desired_leaf_differences(
    expected: Any,
    actual: Any,
    *,
    path: str = "",
) -> list[dict[str, Any]]:
    """Compare only leaves explicitly present in the pinned desired manifest."""
    differences: list[dict[str, Any]] = []
    if isinstance(expected, dict):
        actual_mapping = actual if isinstance(actual, dict) else {}
        for key in sorted(expected):
            child = f"{path}/{key}"
            differences.extend(
                desired_leaf_differences(
                    expected[key], actual_mapping.get(key), path=child
                )
            )
        return differences
    if isinstance(expected, list):
        actual_sequence = actual if isinstance(actual, list) else []
        for index, value in enumerate(expected):
            child = f"{path}/{index}"
            present = actual_sequence[index] if index < len(actual_sequence) else None
            differences.extend(desired_leaf_differences(value, present, path=child))
        return differences
    if expected != actual:
        differences.append({"path": path or "/", "expected": expected, "actual": actual})
    return differences


def gatekeeper_message_uid(message: Any) -> str | None:
    """Extract the evaluated object's UID emitted by the controlled Rego rule."""
    if not isinstance(message, str):
        return None
    match = re.match(r"^govdrift-resource-uid=([a-z0-9-]+); ", message)
    return match.group(1) if match else None


def gatekeeper_policy_state(
    constraint: dict[str, Any],
    *,
    deployment_uid: str | None,
    not_before_utc: str | None,
) -> tuple[str, dict[str, Any]]:
    """Interpret a fresh Gatekeeper audit with engine-emitted UID linkage."""
    status = constraint.get("status", {}) if isinstance(constraint, dict) else {}
    violations = status.get("violations", []) or []
    audit_timestamp = status.get("auditTimestamp")
    audit_time = parse_rfc3339(audit_timestamp)
    boundary = parse_rfc3339(not_before_utc)
    fresh = bool(audit_time and (boundary is None or audit_time >= boundary))
    matches = [
        row for row in violations
        if row.get("kind") == "Deployment"
        and row.get("name") == "payments"
        and row.get("namespace") == "payments"
    ]
    structural_uids = sorted({str(row["uid"]) for row in matches if row.get("uid")})
    message_uids = sorted(
        {
            uid
            for row in matches
            if (uid := gatekeeper_message_uid(row.get("message"))) is not None
        }
    )
    subject_linked = bool(
        deployment_uid
        and len(message_uids) == 1
        and message_uids[0] == deployment_uid
    )
    evidence = {
        "audit_timestamp": audit_timestamp,
        "audit_fresh_for_injection": fresh,
        "total_violations": status.get("totalViolations", len(violations)),
        "matching_violation_count": len(matches),
        "violation_identity": [
            {
                "group": row.get("group"),
                "version": row.get("version"),
                "kind": row.get("kind"),
                "namespace": row.get("namespace"),
                "name": row.get("name"),
                "message": row.get("message"),
                "embedded_resource_uid": gatekeeper_message_uid(row.get("message")),
                "enforcement_action": row.get("enforcementAction"),
            }
            for row in matches
        ],
        "gatekeeper_structural_uid_field": bool(structural_uids),
        "gatekeeper_structural_uids": structural_uids,
        "gatekeeper_emitted_resource_uid": bool(message_uids),
        "gatekeeper_message_uids": message_uids,
        "joined_resource_uid": deployment_uid if subject_linked else None,
        "uid_join_source": (
            "gatekeeper-policy-message-embedded-object-uid"
            if subject_linked else None
        ),
    }
    if not fresh:
        return "undecidable", evidence
    if matches and subject_linked:
        return "inconsistent", evidence
    if matches:
        return "undecidable", evidence
    return "consistent", evidence


def artifact_authorization_state(
    pods: dict[str, Any],
    *,
    approved_digests: set[str],
) -> tuple[str, dict[str, Any]]:
    """Shared T3 adapter: compare ready runtime image IDs to approved digests."""
    running: list[dict[str, Any]] = []
    for pod in pods.get("items", []) if isinstance(pods, dict) else []:
        if pod.get("metadata", {}).get("deletionTimestamp"):
            continue
        for status in pod.get("status", {}).get("containerStatuses", []) or []:
            if status.get("name") != "payments" or not status.get("ready"):
                continue
            running.append(
                {
                    "pod": pod.get("metadata", {}).get("name"),
                    "pod_uid": pod.get("metadata", {}).get("uid"),
                    "image": status.get("image"),
                    "image_id": status.get("imageID"),
                }
            )
    image_ids = sorted({row["image_id"] for row in running if row.get("image_id")})
    evidence = {
        "ready_containers": running,
        "running_image_ids": image_ids,
        "approved_image_ids": sorted(approved_digests),
        "adapter_origin": "shared-artifact-lineage-adapter",
        "independent_argocd_gatekeeper_authorization_validation": False,
    }
    if not image_ids or not approved_digests:
        return "undecidable", evidence
    if any(image_id not in approved_digests for image_id in image_ids):
        return "inconsistent", evidence
    return "consistent", evidence


def classify_evidence(
    application: dict[str, Any],
    constraint: dict[str, Any],
    deployment: dict[str, Any],
    pods: dict[str, Any],
    *,
    approved_digests: set[str],
    policy_not_before_utc: str | None,
) -> dict[str, Any]:
    deployment_uid = deployment.get("metadata", {}).get("uid")
    configuration, argo = argo_configuration_state(application)
    policy, gatekeeper = gatekeeper_policy_state(
        constraint,
        deployment_uid=deployment_uid,
        not_before_utc=policy_not_before_utc,
    )
    authorization, artifact = artifact_authorization_state(
        pods,
        approved_digests=approved_digests,
    )
    components = {
        "configuration": configuration,
        "policy": policy,
        "authorization": authorization,
        "intent": "not_evaluated",
        "environment": "not_evaluated",
    }
    observed_set = sorted(
        name for name, value in components.items() if value == "inconsistent"
    )
    undecidable = sorted(
        name for name, value in components.items() if value == "undecidable"
    )
    return {
        "components": components,
        "observed_set": observed_set,
        "undecidable_components": undecidable,
        "deployment_uid": deployment_uid,
        "evidence": {
            "argo": argo,
            "gatekeeper": gatekeeper,
            "artifact": artifact,
        },
        "scope": {
            "configuration": "argocd-native",
            "policy": "gatekeeper-native",
            "authorization": "shared-adapter-not-independently-replicated",
            "intent": "not-evaluated",
            "environment": "not-evaluated",
        },
    }


class ResourceMonitor(threading.Thread):
    """Apply the 80% sustained stop rule with three five-second samples."""

    def __init__(self, recorder: NdjsonRecorder, *, interval: float = 5.0) -> None:
        super().__init__(daemon=True)
        self.recorder = recorder
        self.interval = interval
        self.threshold = 80.0
        self.required_consecutive = 3
        self.stop_event = threading.Event()
        self.condition = threading.Condition()
        self.samples = 0
        self.stop_reason: dict[str, Any] | None = None
        self.streaks: dict[str, int] = {}
        info = command(
            ["docker", "info", "--format", "{{.NCPU}}"],
            check=False,
            timeout=10,
        )
        try:
            self.docker_cpus = max(1.0, float(info.stdout.strip()))
        except ValueError:
            self.docker_cpus = 1.0

    @staticmethod
    def _host_cpu() -> float | None:
        proc = command(["top", "-l", "1", "-n", "0"], check=False, timeout=10)
        match = re.search(r"CPU usage:.*?([0-9.]+)% idle", proc.stdout)
        return round(100.0 - float(match.group(1)), 3) if match else None

    @staticmethod
    def _host_memory() -> float | None:
        proc = command(["memory_pressure", "-Q"], check=False, timeout=10)
        match = re.search(r"memory free percentage:\s*([0-9]+)%", proc.stdout)
        return round(100.0 - float(match.group(1)), 3) if match else None

    def _node_metrics(self) -> tuple[float | None, float | None]:
        proc = command(
            [
                "docker", "stats", NODE, "--no-stream", "--format",
                "{{.CPUPerc}} {{.MemPerc}}",
            ],
            check=False,
            timeout=15,
        )
        match = re.search(r"([0-9.]+)%\s+([0-9.]+)%", proc.stdout)
        if not match:
            return None, None
        normalized_cpu = float(match.group(1)) / self.docker_cpus
        return round(normalized_cpu, 3), round(float(match.group(2)), 3)

    def sample_once(self) -> dict[str, Any]:
        node_cpu, node_memory = self._node_metrics()
        metrics = {
            "host_cpu_percent": self._host_cpu(),
            "host_memory_pressure_used_percent": self._host_memory(),
            "node_cpu_normalized_percent": node_cpu,
            "node_memory_percent_of_docker_vm": node_memory,
        }
        for name, value in metrics.items():
            self.streaks[name] = (
                self.streaks.get(name, 0) + 1
                if value is not None and value > self.threshold
                else 0
            )
            if self.streaks[name] >= self.required_consecutive and self.stop_reason is None:
                self.stop_reason = {
                    "metric": name,
                    "value": value,
                    "threshold": self.threshold,
                    "consecutive_samples": self.streaks[name],
                    "sample_interval_seconds": self.interval,
                }
        row = self.recorder.add(
            "resource_sample",
            metrics=metrics,
            streaks=dict(self.streaks),
            stop_rule_triggered=self.stop_reason is not None,
        )
        with self.condition:
            self.samples += 1
            self.condition.notify_all()
        return row

    def run(self) -> None:
        while not self.stop_event.is_set():
            started = time.monotonic()
            try:
                self.sample_once()
            except Exception as exc:  # monitoring failure is recorded, not hidden
                self.recorder.add(
                    "resource_monitor_error",
                    error_type=type(exc).__name__,
                    error=str(exc),
                )
            remaining = self.interval - (time.monotonic() - started)
            self.stop_event.wait(max(0.0, remaining))

    def wait_samples(self, count: int, timeout: float) -> None:
        deadline = time.monotonic() + timeout
        with self.condition:
            while self.samples < count:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise ExperimentError("resource monitor did not produce preflight samples")
                self.condition.wait(remaining)

    def close(self) -> None:
        self.stop_event.set()
        self.join(timeout=20)


@dataclass
class Injection:
    scenario: str
    injection_id: str
    timing_reference_id: str
    t_inject: float
    t_onset: float
    injection_utc: str
    onset_utc: str
    policy_not_before_utc: str
    actuation_seconds: float


class CrossStackExperiment:
    def __init__(self, *, output_dir: Path) -> None:
        self.output_dir = output_dir.resolve()
        # This must precede mkdir/unlink/recorder initialization: those actions
        # can themselves change Git's view of an in-repository output path.
        self.protocol_source_state = capture_git_provenance()
        self.output_dir.mkdir(parents=True, exist_ok=True)
        for name in (
            "run_status.json",
            "cleanup.json",
            "platform.json",
            "cross_stack_observations.json",
            "cross_stack_observations.csv",
            "cross_stack_summary.json",
            "cross_stack_profile_summary.csv",
            "table_cross_stack.tex",
        ):
            (self.output_dir / name).unlink(missing_ok=True)
        diagnostics = self.output_dir / "failure_diagnostics"
        if diagnostics.exists():
            shutil.rmtree(diagnostics)
        self.raw = NdjsonRecorder(self.output_dir / "cross_stack_raw.ndjson")
        self.install = NdjsonRecorder(self.output_dir / "install_events.ndjson")
        self.resources = NdjsonRecorder(self.output_dir / "resource_samples.ndjson")
        self.rows: list[dict[str, Any]] = []
        self.approved_digests: set[str] = set()
        self.source_revision: str | None = None
        self.api_read_errors = 0
        self.cluster_created = False
        self.monitor: ResourceMonitor | None = None
        self.kubeconfig: Path | None = None
        self.started_utc = utc_now()
        self.install_deadline = float("inf")

    def check_stop(self) -> None:
        if self.monitor and self.monitor.stop_reason:
            raise StopRule(f"sustained resource threshold exceeded: {self.monitor.stop_reason}")
        if time.monotonic() > self.install_deadline:
            raise StopRule("Argo CD + Gatekeeper installation did not become Ready within 15 minutes")

    def host(self, *args: str, timeout: float | None = 120, check: bool = True) -> str:
        self.check_stop()
        proc = command(list(args), timeout=timeout, check=check)
        self.check_stop()
        return proc.stdout

    def kubectl(
        self,
        *args: str,
        input_text: str | None = None,
        timeout: float | None = 90,
        check: bool = True,
    ) -> str:
        if self.kubeconfig is None:
            raise ExperimentError("isolated kubeconfig is not initialized")
        self.check_stop()
        proc = command(
            [
                "kubectl", "--kubeconfig", str(self.kubeconfig),
                "--context", CONTEXT, *args,
            ],
            input_text=input_text,
            timeout=timeout,
            check=check,
        )
        self.check_stop()
        return proc.stdout

    def kubectl_json(self, *args: str, check: bool = True) -> dict[str, Any]:
        text = self.kubectl(*args, "-o", "json", check=check)
        if not text.strip():
            return {}
        try:
            value = json.loads(text)
        except json.JSONDecodeError as exc:
            raise ExperimentError(f"kubectl returned malformed JSON for {' '.join(args)}") from exc
        return value if isinstance(value, dict) else {}

    def safe_json(self, *args: str) -> dict[str, Any]:
        try:
            return self.kubectl_json(*args)
        except StopRule:
            raise
        except ExperimentError as exc:
            self.api_read_errors += 1
            self.install.add(
                "api_read_error",
                kubectl_args=list(args),
                error_type=type(exc).__name__,
                error=str(exc),
            )
            return {}

    def apply(
        self,
        path: Path,
        *,
        namespace: str | None = None,
        server_side: bool = False,
    ) -> str:
        args = ["apply"]
        if server_side:
            args.extend(["--server-side", "--force-conflicts"])
        if namespace:
            args.extend(["-n", namespace])
        args.extend(["-f", str(path)])
        return self.kubectl(*args, timeout=180)

    def download_and_verify(self) -> dict[str, Any]:
        locks = json.loads((STACK / "upstream-lock.json").read_text())
        upstream = self.output_dir / "upstream"
        upstream.mkdir(parents=True, exist_ok=True)
        verified: dict[str, Any] = {}
        for name, lock in locks.items():
            destination = upstream / f"{name}-{lock['version']}.yaml"
            request = urllib.request.Request(lock["url"], headers={"User-Agent": "govdrift-cross-lab"})
            with urllib.request.urlopen(request, timeout=60) as response:
                payload = response.read()
            actual = hashlib.sha256(payload).hexdigest()
            if actual != lock["sha256"]:
                raise ExperimentError(
                    f"{name} manifest hash mismatch: expected {lock['sha256']}, got {actual}"
                )
            destination.write_bytes(payload)
            verified[name] = {**lock, "path": str(destination), "bytes": len(payload), "verified": True}
            self.install.add("upstream_manifest_verified", component=name, **verified[name])
        return verified

    def write_manifest_inventory(self, verified: dict[str, Any]) -> None:
        rows: list[dict[str, Any]] = []
        for path in sorted(STACK.iterdir()):
            if path.is_file():
                rows.append(
                    {
                        "scope": "local-stack",
                        "name": path.name,
                        "sha256": sha256_file(path),
                        "bytes": path.stat().st_size,
                        "verified": True,
                    }
                )
        for path in sorted(CAMPAIGN_SOURCE_FILES):
            rows.append(
                {
                    "scope": "campaign-source",
                    "name": path.relative_to(ROOT).as_posix(),
                    "sha256": sha256_file(path),
                    "bytes": path.stat().st_size,
                    "verified": True,
                }
            )
        for name, item in sorted(verified.items()):
            path = Path(item["path"])
            rows.append(
                {
                    "scope": "upstream-install",
                    "name": f"{name}-{item['version']}",
                    "sha256": sha256_file(path),
                    "bytes": path.stat().st_size,
                    "verified": sha256_file(path) == item["sha256"],
                }
            )
        with (self.output_dir / "manifest_checksums.csv").open("w", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(rows[0]), lineterminator="\n")
            writer.writeheader()
            writer.writerows(rows)

    def verify_host_images(self) -> dict[str, str]:
        expected = {
            BASE_IMAGE: BASE_IMAGE_ID,
            ALT_IMAGE: ALT_IMAGE_ID,
            GIT_IMAGE: GIT_IMAGE_ID,
        }
        actual: dict[str, str] = {}
        for image, expected_id in expected.items():
            value = self.host(
                "docker", "image", "inspect", image,
                "--format", "{{.Id}}", timeout=20,
            ).strip()
            actual[image] = value
            if value != expected_id:
                raise ExperimentError(
                    f"host image identity changed for {image}: expected {expected_id}, got {value}"
                )
        return actual

    def create_cluster(self, kubeconfig: Path) -> None:
        existing = {
            line.strip()
            for line in self.host("kind", "get", "clusters", check=False).splitlines()
            if line.strip()
        }
        if CLUSTER in existing:
            raise ExperimentError(
                f"refusing to replace pre-existing {CLUSTER}; delete it explicitly before rerun"
            )
        self.kubeconfig = kubeconfig
        remaining = max(1.0, self.install_deadline - time.monotonic())
        self.cluster_created = True
        self.host(
            "kind", "create", "cluster",
            "--name", CLUSTER,
            "--image", NODE_IMAGE,
            "--config", str(STACK / "kind-config.yaml"),
            "--kubeconfig", str(kubeconfig),
            "--wait", "180s",
            timeout=remaining,
        )
        self.install.add("cluster_created", cluster=CLUSTER, node_image=NODE_IMAGE)
        server = self.kubectl_json("version").get("serverVersion", {}).get("gitVersion")
        self.install.add("kubernetes_ready", server_version=server)

    def load_and_alias_images(self) -> None:
        with tempfile.TemporaryDirectory(prefix="govdrift-cross-images-") as temporary:
            for index, image in enumerate((BASE_IMAGE, ALT_IMAGE, GIT_IMAGE), start=1):
                archive = Path(temporary) / f"image-{index}.tar"
                self.host(
                    "docker", "image", "save", "-o", str(archive), image,
                    timeout=180,
                )
                self.check_stop()
                with archive.open("rb") as handle:
                    proc = subprocess.run(
                        [
                            "docker", "exec", "-i", NODE,
                            "ctr", "--namespace=k8s.io", "images", "import",
                            "--platform=linux/arm64", "--snapshotter=overlayfs", "-",
                        ],
                        stdin=handle,
                        capture_output=True,
                        timeout=180,
                    )
                if proc.returncode:
                    raise ExperimentError(
                        f"direct containerd image import failed for {image}: "
                        f"{proc.stderr.decode('utf-8', errors='replace')}"
                    )
                self.install.add(
                    "image_loaded",
                    image=image,
                    method="docker-save-to-single-platform-containerd-import",
                    archive_sha256=sha256_file(archive),
                )
        aliases = (
            ("docker.io/library/nginx:1.27-alpine", BASE_NODE_REF),
            ("docker.io/library/nginx:1.26-alpine", ALT_NODE_REF),
            (BASE_NODE_REF, LIVE_NODE_REF),
        )
        for source, target in aliases:
            self.host(
                "docker", "exec", NODE,
                "ctr", "-n", "k8s.io", "images", "tag", "--force",
                source, target,
                timeout=30,
            )
            self.install.add("node_image_alias", source=source, target=target)

    def set_live_image(self, source: str) -> None:
        if source not in {BASE_NODE_REF, ALT_NODE_REF}:
            raise ExperimentError(f"invalid node image source: {source}")
        self.host(
            "docker", "exec", NODE,
            "ctr", "-n", "k8s.io", "images", "tag", "--force",
            source, LIVE_NODE_REF,
            timeout=30,
        )

    def namespace_ready(self, namespace: str) -> tuple[bool, dict[str, Any]]:
        pods = self.safe_json("-n", namespace, "get", "pods").get("items", [])
        deployments = self.safe_json("-n", namespace, "get", "deployments").get("items", [])
        statefulsets = self.safe_json("-n", namespace, "get", "statefulsets").get("items", [])
        pod_states = []
        pods_ready = bool(pods)
        for pod in pods:
            phase = pod.get("status", {}).get("phase")
            statuses = pod.get("status", {}).get("containerStatuses", []) or []
            ready = phase == "Succeeded" or (
                phase == "Running" and bool(statuses) and all(row.get("ready") for row in statuses)
            )
            pods_ready = pods_ready and ready
            pod_states.append(
                {
                    "name": pod.get("metadata", {}).get("name"),
                    "phase": phase,
                    "ready": ready,
                }
            )
        deployments_ready = all(
            int(row.get("status", {}).get("availableReplicas", 0))
            >= int(row.get("spec", {}).get("replicas", 1))
            for row in deployments
        )
        statefulsets_ready = all(
            int(row.get("status", {}).get("readyReplicas", 0))
            >= int(row.get("spec", {}).get("replicas", 1))
            for row in statefulsets
        )
        detail = {
            "namespace": namespace,
            "pods": pod_states,
            "deployments": len(deployments),
            "statefulsets": len(statefulsets),
            "pods_ready": pods_ready,
            "deployments_ready": deployments_ready,
            "statefulsets_ready": statefulsets_ready,
        }
        return pods_ready and deployments_ready and statefulsets_ready, detail

    def wait_ready(
        self,
        namespace: str,
        label: str,
        *,
        timeout_seconds: float | None = None,
    ) -> None:
        phase_deadline = (
            time.monotonic() + timeout_seconds
            if timeout_seconds is not None
            else float("inf")
        )
        while True:
            self.check_stop()
            if time.monotonic() > phase_deadline:
                raise StopRule(
                    f"{label} did not become Ready within {timeout_seconds:g} seconds"
                )
            ready, detail = self.namespace_ready(namespace)
            self.install.add("readiness_poll", component=label, ready=ready, detail=detail)
            if ready:
                self.install.add("component_ready", component=label, detail=detail)
                return
            time.sleep(min(5.0, max(0.1, phase_deadline - time.monotonic())))

    def wait_crd_established(self, name: str, *, timeout_seconds: float) -> None:
        phase_deadline = time.monotonic() + timeout_seconds
        while True:
            self.check_stop()
            crd = self.kubectl_json(
                "get", "crd", name, "--ignore-not-found"
            )
            established = crd_is_established(crd)
            self.install.add(
                "crd_readiness_poll",
                crd=name,
                exists=bool(crd),
                established=established,
            )
            if established:
                self.install.add("crd_established", crd=name)
                return
            if time.monotonic() > phase_deadline:
                raise StopRule(
                    f"CRD {name} did not become Established within "
                    f"{timeout_seconds:g} seconds"
                )
            time.sleep(min(1.0, max(0.1, phase_deadline - time.monotonic())))

    def wait_git_source(self, *, timeout_seconds: float) -> str:
        """Require a native Git ref read from the actual Argo repo-server pod."""
        phase_deadline = time.monotonic() + timeout_seconds
        expected_ref = "refs/heads/main"
        source = "git://git-server.cross-system.svc.cluster.local/repo.git"
        poll = 0
        while True:
            self.check_stop()
            poll += 1
            output = self.kubectl(
                "-n", "argocd", "exec", "deployment/argocd-repo-server",
                "-c", "argocd-repo-server", "--", "git", "ls-remote", source,
                check=False,
                timeout=30,
            )
            matched = any(
                line.rstrip().endswith(f"\t{expected_ref}")
                for line in output.splitlines()
            )
            self.install.add(
                "git_source_poll",
                poll_index=poll,
                source=source,
                expected_ref=expected_ref,
                matched=matched,
                ls_remote_stdout=output,
            )
            if matched:
                revision = next(
                    line.split("\t", 1)[0]
                    for line in output.splitlines()
                    if line.rstrip().endswith(f"\t{expected_ref}")
                )
                self.install.add(
                    "git_source_ready",
                    source=source,
                    ref=expected_ref,
                    revision=revision,
                    validation_origin="argocd-repo-server-pod",
                )
                return revision
            if time.monotonic() > phase_deadline:
                raise StopRule(
                    f"pinned Git source was not readable by Argo repo-server within "
                    f"{timeout_seconds:g} seconds"
                )
            time.sleep(min(1.0, max(0.1, phase_deadline - time.monotonic())))

    def install_stack(self, verified: dict[str, Any]) -> None:
        self.apply(STACK / "namespaces.yaml")
        self.apply(
            Path(verified["argocd"]["path"]),
            namespace="argocd",
            server_side=True,
        )
        self.apply(Path(verified["gatekeeper"]["path"]), server_side=True)
        self.install.add(
            "install_manifests_applied",
            apply_mode="server-side",
            manifests_unmodified=True,
        )
        self.wait_ready("argocd", "argocd-v3.4.2")
        self.wait_ready("gatekeeper-system", "gatekeeper-v3.22.2")

        audit = self.kubectl_json(
            "-n", "gatekeeper-system", "get", "deployment", "gatekeeper-audit"
        )
        args = list(
            audit.get("spec", {}).get("template", {}).get("spec", {})
            .get("containers", [{}])[0].get("args", [])
        )
        args = [
            value for value in args
            if not value.startswith("--audit-interval=")
            and not value.startswith("--constraint-violations-limit=")
        ]
        args.extend(["--audit-interval=2", "--constraint-violations-limit=100"])
        patch = [{
            "op": "replace",
            "path": "/spec/template/spec/containers/0/args",
            "value": args,
        }]
        self.kubectl(
            "-n", "gatekeeper-system", "patch", "deployment", "gatekeeper-audit",
            "--type=json", "-p", canonical_json(patch),
        )
        self.kubectl(
            "-n", "gatekeeper-system", "rollout", "status", "deployment/gatekeeper-audit",
            "--timeout=180s", timeout=200,
        )
        self.install.add(
            "gatekeeper_audit_configured",
            audit_interval_seconds=2,
            enforcement_action="dryrun",
        )

    def deploy_sources_and_controls(self) -> None:
        seed = self.kubectl(
            "-n", "cross-system", "create", "configmap", "git-seed",
            f"--from-file=workload.yaml={STACK / 'workload.yaml'}",
            "--dry-run=client", "-o", "yaml",
        )
        self.kubectl("apply", "-f", "-", input_text=seed)
        self.apply(STACK / "git-server.yaml")
        self.wait_ready(
            "cross-system",
            "pinned-git-source",
            timeout_seconds=180,
        )
        self.source_revision = self.wait_git_source(timeout_seconds=120)

        self.apply(STACK / "gatekeeper-template.yaml")
        self.wait_crd_established(
            "k8srequiredcrosslabel.constraints.gatekeeper.sh",
            timeout_seconds=120,
        )
        self.apply(STACK / "gatekeeper-constraint-v7.yaml")
        self.apply(STACK / "application.yaml")
        self.install.add("experiment_controls_applied")
        self.install_deadline = float("inf")

    def hard_refresh_argo(self) -> None:
        self.kubectl(
            "-n", "argocd", "annotate", "application", "cross-payments",
            "argocd.argoproj.io/refresh=hard", "--overwrite",
        )

    def collect_evidence(self, *, policy_not_before_utc: str | None) -> dict[str, Any]:
        application = self.safe_json(
            "-n", "argocd", "get", "application", "cross-payments"
        )
        constraint = self.safe_json(
            "get", "k8srequiredcrosslabel", "payments-current-policy"
        )
        deployment = self.safe_json(
            "-n", "payments", "get", "deployment", "payments"
        )
        pods = self.safe_json(
            "-n", "payments", "get", "pods", "-l", "app=payments"
        )
        return classify_evidence(
            application,
            constraint,
            deployment,
            pods,
            approved_digests=set(self.approved_digests),
            policy_not_before_utc=policy_not_before_utc,
        )

    def ready_image_ids(self) -> set[str]:
        pods = self.safe_json(
            "-n", "payments", "get", "pods", "-l", "app=payments"
        )
        values: set[str] = set()
        for pod in pods.get("items", []):
            if pod.get("metadata", {}).get("deletionTimestamp"):
                continue
            for status in pod.get("status", {}).get("containerStatuses", []) or []:
                if status.get("name") == "payments" and status.get("ready") and status.get("imageID"):
                    values.add(status["imageID"])
        return values

    def wait_rollout(self) -> None:
        self.kubectl(
            "-n", "payments", "rollout", "status", "deployment/payments",
            "--timeout=180s", timeout=200,
        )

    def restore_with_argo(self, *, scenario: str, repetition: int) -> dict[str, Any]:
        if not self.source_revision:
            raise ExperimentError("pinned Git revision is unavailable for Argo reset")
        idle_deadline = time.monotonic() + 120
        application: dict[str, Any] = {}
        while time.monotonic() < idle_deadline:
            self.check_stop()
            application = self.safe_json(
                "-n", "argocd", "get", "application", "cross-payments"
            )
            if application and not application.get("operation"):
                break
            time.sleep(0.5)
        else:
            raise ExperimentError("Argo Application did not become idle before reset")

        history = (application.get("status") or {}).get("history") or []
        previous_history_id = max(
            (int(row.get("id", -1)) for row in history if isinstance(row, dict)),
            default=-1,
        )
        operation = {
            "operation": {
                "initiatedBy": {"username": "govdrift-cross-baseline-reset"},
                "sync": {
                    "revision": self.source_revision,
                    "prune": True,
                    "syncOptions": ["CreateNamespace=false"],
                },
            }
        }
        reference_monotonic = time.monotonic()
        reference_id = f"baseline-sync:{scenario}:r{repetition}"
        self.raw.add(
            "baseline_sync_marker",
            scenario=scenario,
            repetition=repetition,
            timing_reference_id=reference_id,
            reference_kind="argo-baseline-sync-start",
            reference_utc=utc_now(),
            reference_monotonic_seconds=round(reference_monotonic, 9),
            source_revision=self.source_revision,
        )
        self.kubectl(
            "-n", "argocd", "patch", "application", "cross-payments",
            "--type=merge", "-p", canonical_json(operation),
        )
        deadline = time.monotonic() + 120
        poll = 0
        while time.monotonic() < deadline:
            self.check_stop()
            poll += 1
            evaluation_started = time.monotonic()
            application = self.safe_json(
                "-n", "argocd", "get", "application", "cross-payments"
            )
            evaluation_completed = time.monotonic()
            status = application.get("status") or {}
            operation_state = status.get("operationState") or {}
            phase = operation_state.get("phase")
            history = status.get("history") or []
            current_history_id = max(
                (int(row.get("id", -1)) for row in history if isinstance(row, dict)),
                default=-1,
            )
            new_history = current_history_id > previous_history_id
            terminal_success = new_history and phase == "Succeeded"
            terminal_failure = new_history and phase in {"Error", "Failed"}
            self.raw.add(
                "baseline_sync_poll",
                scenario=scenario,
                repetition=repetition,
                poll_index=poll,
                timing_reference_id=reference_id,
                evaluation_started_since_reference_seconds=round(
                    evaluation_started - reference_monotonic, 6
                ),
                evaluation_completed_since_reference_seconds=round(
                    evaluation_completed - reference_monotonic, 6
                ),
                evaluation_duration_seconds=round(
                    evaluation_completed - evaluation_started, 6
                ),
                source_revision=self.source_revision,
                previous_history_id=previous_history_id,
                current_history_id=current_history_id,
                new_history=new_history,
                operation_phase=phase,
                operation_message=operation_state.get("message"),
                classification_at_completion={
                    "new_history": new_history,
                    "operation_phase": phase,
                    "terminal_success": terminal_success,
                    "terminal_failure": terminal_failure,
                },
            )
            if terminal_success:
                return {
                    "previous_history_id": previous_history_id,
                    "current_history_id": current_history_id,
                    "phase": phase,
                    "message": operation_state.get("message"),
                    "revision": (
                        (operation_state.get("syncResult") or {}).get("revision")
                    ),
                }
            if terminal_failure:
                raise ExperimentError(
                    f"Argo baseline reset failed: {operation_state.get('message')}"
                )
            time.sleep(POLL_SECONDS)
        raise ExperimentError("Argo baseline reset did not complete within 120 seconds")

    def reset_baseline(self, *, scenario: str, repetition: int) -> tuple[str, dict[str, Any]]:
        self.check_stop()
        self.set_live_image(BASE_NODE_REF)
        desired = self.kubectl_json(
            "create", "--dry-run=client", "-f", str(STACK / "workload.yaml")
        )
        before_live = self.safe_json(
            "-n", "payments", "get", "deployment", "payments",
            "--ignore-not-found",
        )
        before_differences = desired_leaf_differences(desired, before_live)
        argo_reset = self.restore_with_argo(
            scenario=scenario,
            repetition=repetition,
        )
        after_live = self.safe_json(
            "-n", "payments", "get", "deployment", "payments"
        )
        after_differences = desired_leaf_differences(desired, after_live)
        self.raw.add(
            "baseline_restoration",
            scenario=scenario,
            repetition=repetition,
            source_revision=self.source_revision,
            before_desired_leaf_differences=before_differences,
            after_desired_leaf_differences=after_differences,
            argo_operation=argo_reset,
        )
        if after_differences:
            raise ExperimentError(
                f"Argo reset completed but desired manifest leaves still differ: "
                f"{after_differences}"
            )
        policy_boundary = utc_now()
        self.apply(STACK / "gatekeeper-constraint-v7.yaml")
        self.kubectl(
            "-n", "payments", "delete", "pod", "-l", "app=payments",
            "--ignore-not-found=true", "--wait=true", timeout=120,
        )
        self.wait_rollout()
        self.hard_refresh_argo()

        current = self.ready_image_ids()
        if not current:
            raise ExperimentError("baseline rollout has no ready image ID")
        if not self.approved_digests:
            self.approved_digests = set(current)
        if current != self.approved_digests:
            raise ExperimentError(
                f"baseline digest changed: approved={self.approved_digests}, current={current}"
            )

        reference_monotonic = time.monotonic()
        reference_id = f"baseline-observation:{scenario}:r{repetition}"
        self.raw.add(
            "baseline_observation_marker",
            scenario=scenario,
            repetition=repetition,
            timing_reference_id=reference_id,
            reference_kind="baseline-observation-start",
            reference_utc=utc_now(),
            reference_monotonic_seconds=round(reference_monotonic, 9),
        )
        deadline = reference_monotonic + 120
        last: dict[str, Any] = {}
        poll = 0
        while time.monotonic() < deadline:
            self.check_stop()
            poll += 1
            evaluation_started = time.monotonic()
            last = self.collect_evidence(policy_not_before_utc=policy_boundary)
            evaluation_completed = time.monotonic()
            exact_empty = last["observed_set"] == [] and not last["undecidable_components"]
            self.raw.add(
                "baseline_poll",
                scenario=scenario,
                repetition=repetition,
                poll_index=poll,
                timing_reference_id=reference_id,
                evaluation_started_since_reference_seconds=round(
                    evaluation_started - reference_monotonic, 6
                ),
                evaluation_completed_since_reference_seconds=round(
                    evaluation_completed - reference_monotonic, 6
                ),
                evaluation_duration_seconds=round(
                    evaluation_completed - evaluation_started, 6
                ),
                expected_set=[],
                exact_empty=exact_empty,
                classification_at_completion={
                    "components": last["components"],
                    "observed_set": last["observed_set"],
                    "undecidable_components": last["undecidable_components"],
                    "exact_empty": exact_empty,
                },
                **last,
            )
            if exact_empty:
                return policy_boundary, last
            time.sleep(POLL_SECONDS)
        raise ExperimentError(f"baseline did not become fully decidable and empty: {last}")

    def inject(
        self,
        scenario: str,
        *,
        repetition: int,
        schedule_index: int,
    ) -> Injection:
        t_inject = time.monotonic()
        injection_utc = utc_now()
        if scenario == "S1":
            patch = [{
                "op": "replace",
                "path": "/spec/template/spec/containers/0/resources/limits/cpu",
                "value": "999m",
            }]
            self.kubectl(
                "-n", "payments", "patch", "deployment", "payments",
                "--type=json", "-p", canonical_json(patch),
            )
            t_onset = time.monotonic()
            onset_utc = utc_now()
            self.hard_refresh_argo()
            policy_boundary = injection_utc
        elif scenario == "S3":
            self.apply(STACK / "gatekeeper-constraint-v8.yaml")
            t_onset = time.monotonic()
            onset_utc = utc_now()
            policy_boundary = injection_utc
        elif scenario == "S4":
            self.set_live_image(ALT_NODE_REF)
            self.kubectl(
                "-n", "payments", "delete", "pod", "-l", "app=payments",
                "--wait=true", timeout=120,
            )
            self.wait_rollout()
            alternate = self.ready_image_ids()
            if not alternate or alternate == self.approved_digests:
                raise ExperimentError(
                    f"artifact substitution did not materialize: {alternate}"
                )
            t_onset = time.monotonic()
            onset_utc = utc_now()
            self.hard_refresh_argo()
            policy_boundary = injection_utc
        else:
            raise ExperimentError(f"unknown scenario: {scenario}")
        injection_id = f"{scenario}:r{repetition}:schedule{schedule_index}"
        timing_reference_id = f"operational-onset:{injection_id}"
        injection = Injection(
            scenario=scenario,
            injection_id=injection_id,
            timing_reference_id=timing_reference_id,
            t_inject=t_inject,
            t_onset=t_onset,
            injection_utc=injection_utc,
            onset_utc=onset_utc,
            policy_not_before_utc=policy_boundary,
            actuation_seconds=max(0.0, t_onset - t_inject),
        )
        self.raw.add(
            "injection_onset_marker",
            scenario=scenario,
            repetition=repetition,
            schedule_index=schedule_index,
            injection_id=injection_id,
            timing_reference_id=timing_reference_id,
            reference_kind="operational-onset",
            injection_utc=injection_utc,
            onset_utc=onset_utc,
            injection_monotonic_seconds=round(t_inject, 9),
            onset_monotonic_seconds=round(t_onset, 9),
            actuation_seconds=round(injection.actuation_seconds, 6),
        )
        return injection

    def observe(
        self,
        *,
        scenario: str,
        repetition: int,
        schedule_index: int,
        injection: Injection,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        expected = EXPECTED[scenario]
        deadline = injection.t_onset + SCENARIO_TIMEOUT_SECONDS[scenario]
        next_poll = injection.t_onset
        poll_index = 0
        first_honest_time: float | None = None
        first_honest_verdict_kind: str | None = None
        first_epistemic_time: float | None = None
        first_substantive_time: float | None = None
        first_substantive_set: list[str] | None = None
        last: dict[str, Any] = {}
        while time.monotonic() < deadline:
            self.check_stop()
            sleep_for = next_poll - time.monotonic()
            if sleep_for > 0:
                time.sleep(sleep_for)
            started = time.monotonic()
            scheduler_lag = max(0.0, started - next_poll)
            poll_index += 1
            last = self.collect_evidence(
                policy_not_before_utc=injection.policy_not_before_utc
            )
            completed = time.monotonic()
            observed = set(last["observed_set"])
            undecidable = set(last["undecidable_components"])
            if (observed or undecidable) and first_honest_time is None:
                first_honest_time = completed
                if observed and undecidable:
                    first_honest_verdict_kind = "substantive-and-epistemic"
                elif observed:
                    first_honest_verdict_kind = "substantive-only"
                else:
                    first_honest_verdict_kind = "epistemic-only"
            if undecidable and first_epistemic_time is None:
                first_epistemic_time = completed
            if observed and first_substantive_time is None:
                first_substantive_time = completed
                first_substantive_set = sorted(observed)
            exact = observed == expected and not last["undecidable_components"]
            poll_row = self.raw.add(
                "scenario_poll",
                scenario=scenario,
                repetition=repetition,
                schedule_index=schedule_index,
                injection_id=injection.injection_id,
                poll_index=poll_index,
                poll_period_seconds=POLL_SECONDS,
                scheduler_lag_seconds=round(scheduler_lag, 6),
                timing_reference_id=injection.timing_reference_id,
                elapsed_since_onset_seconds=round(started - injection.t_onset, 6),
                evaluation_started_since_onset_seconds=round(
                    started - injection.t_onset, 6
                ),
                evaluation_completed_since_onset_seconds=round(
                    completed - injection.t_onset, 6
                ),
                evaluation_duration_seconds=round(completed - started, 6),
                expected_set=sorted(expected),
                exact_set=exact,
                surface=SURFACE[scenario],
                classification_at_completion={
                    "components": last["components"],
                    "observed_set": last["observed_set"],
                    "undecidable_components": last["undecidable_components"],
                    "exact_set": exact,
                },
                **last,
            )
            if exact:
                exact_time = completed
                if first_honest_time is None:
                    first_honest_time = exact_time
                    first_honest_verdict_kind = "substantive-only"
                if first_substantive_time is None:
                    first_substantive_time = exact_time
                    first_substantive_set = sorted(observed)
                row = {
                    "scenario": scenario,
                    "repetition": repetition,
                    "schedule_index": schedule_index,
                    "injection_id": injection.injection_id,
                    "timing_reference_id": injection.timing_reference_id,
                    "injection_utc": injection.injection_utc,
                    "onset_utc": injection.onset_utc,
                    "surface": SURFACE[scenario],
                    "expected_set": "|".join(sorted(expected)),
                    "first_observed_set": "|".join(first_substantive_set or []),
                    "final_observed_set": "|".join(last["observed_set"]),
                    "baseline_exact_empty": True,
                    "exact_set": True,
                    "polls_to_exact": poll_index,
                    "actuation_seconds": round(injection.actuation_seconds, 6),
                    "operational_onset_to_first_honest_seconds": round(
                        first_honest_time - injection.t_onset, 6
                    ),
                    "first_honest_verdict_kind": first_honest_verdict_kind,
                    "first_epistemic_alert_seconds": (
                        round(first_epistemic_time - injection.t_onset, 6)
                        if first_epistemic_time is not None
                        else None
                    ),
                    "first_substantive_alert_seconds": round(
                        first_substantive_time - injection.t_onset, 6
                    ),
                    "exact_set_latency_seconds": round(exact_time - injection.t_onset, 6),
                    "evidence_latency_seconds": round(completed - injection.t_onset, 6),
                    "deployment_uid": last.get("deployment_uid"),
                    "argocd_gatekeeper_native_validation": scenario in {"S1", "S3"},
                    "shared_adapter_only": scenario == "S4",
                    "intent_evaluated": False,
                    "environment_evaluated": False,
                }
                return row, poll_row
            next_poll += POLL_SECONDS
        raise ExperimentError(
            f"{scenario} repetition {repetition} did not reach exact set "
            f"{sorted(expected)}; last={last}"
        )

    def run_schedule(self) -> None:
        orders = (
            ("S1", "S3", "S4"),
            ("S3", "S4", "S1"),
            ("S4", "S1", "S3"),
            ("S1", "S4", "S3"),
            ("S3", "S1", "S4"),
        )
        schedule_index = 0
        for repetition, order in enumerate(orders, start=1):
            for scenario in order:
                schedule_index += 1
                _, baseline = self.reset_baseline(
                    scenario=scenario,
                    repetition=repetition,
                )
                injection = self.inject(
                    scenario,
                    repetition=repetition,
                    schedule_index=schedule_index,
                )
                row, final_poll = self.observe(
                    scenario=scenario,
                    repetition=repetition,
                    schedule_index=schedule_index,
                    injection=injection,
                )
                row["baseline_deployment_uid"] = baseline.get("deployment_uid")
                row["final_gatekeeper_joined_uid"] = (
                    final_poll.get("evidence", {})
                    .get("gatekeeper", {})
                    .get("joined_resource_uid")
                )
                row["gatekeeper_emitted_resource_uid"] = (
                    final_poll.get("evidence", {})
                    .get("gatekeeper", {})
                    .get("gatekeeper_emitted_resource_uid")
                )
                row["gatekeeper_structural_uid_field"] = (
                    final_poll.get("evidence", {})
                    .get("gatekeeper", {})
                    .get("gatekeeper_structural_uid_field")
                )
                self.rows.append(row)
                print(
                    f"[{schedule_index:02d}/15] {scenario} r{repetition}: "
                    "set="
                    f"{row['final_observed_set']} honest="
                    f"{row['operational_onset_to_first_honest_seconds']:.3f}s",
                    flush=True,
                )

    def platform_snapshot(
        self,
        verified: dict[str, Any],
        host_images: dict[str, str],
    ) -> dict[str, Any]:
        server = self.kubectl_json("version").get("serverVersion", {}).get("gitVersion")
        argo = self.kubectl_json("-n", "argocd", "get", "deployments")
        gatekeeper = self.kubectl_json(
            "-n", "gatekeeper-system", "get", "deployments"
        )

        def images(payload: dict[str, Any]) -> list[str]:
            return sorted({
                container.get("image")
                for item in payload.get("items", [])
                for container in item.get("spec", {}).get("template", {})
                .get("spec", {}).get("containers", [])
                if container.get("image")
            })

        node = json.loads(
            self.host("docker", "inspect", NODE, timeout=30)
        )[0]
        clock = time.get_clock_info("monotonic")
        return {
            "captured_utc": utc_now(),
            "cluster": CLUSTER,
            "context": CONTEXT,
            "isolation": "separate Kind cluster and temporary kubeconfig",
            "host_os": f"macOS {self.host('sw_vers', '-productVersion').strip()}",
            "host_architecture": platform.machine(),
            "host_ram_bytes": int(self.host("sysctl", "-n", "hw.memsize").strip()),
            "python": sys.version.split()[0],
            "docker_server": self.host(
                "docker", "version", "--format", "{{.Server.Version}}"
            ).strip(),
            "kind": self.host("kind", "version").strip(),
            "kind_node_image": NODE_IMAGE,
            "kind_container_limits": {
                "memory_bytes": node.get("HostConfig", {}).get("Memory", 0),
                "nano_cpus": node.get("HostConfig", {}).get("NanoCpus", 0),
                "interpretation": "0 means no explicit per-container Docker limit",
            },
            "kubernetes_server": server,
            "argocd_version": verified["argocd"]["version"],
            "argocd_images": images(argo),
            "gatekeeper_version": verified["gatekeeper"]["version"],
            "gatekeeper_images": images(gatekeeper),
            "gatekeeper_enforcement_action": "dryrun",
            "gatekeeper_audit_interval_seconds": 2,
            "poll_seconds": POLL_SECONDS,
            "repetitions_per_slice": REPETITIONS,
            "host_image_ids": host_images,
            "approved_runtime_digests": sorted(self.approved_digests),
            "protocol_source_state": self.protocol_source_state,
            "clock": {
                "function": "time.monotonic",
                "implementation": clock.implementation,
                "resolution_seconds": clock.resolution,
                "monotonic": clock.monotonic,
                "adjustable": clock.adjustable,
            },
            "validation_scope": {
                "configuration": "replicated with Argo CD native desired/live status",
                "policy": "replicated with Gatekeeper native background audit",
                "authorization": "S4 uses the existing shared digest adapter; not independent cross-stack validation",
                "intent": "not evaluated",
                "environment": "not evaluated",
            },
            "design": (
                "five bounded repetitions of three representative slices; "
                "descriptive replication, not a statistical equivalence test"
            ),
        }

    def write_observations(self) -> None:
        payload = {
            "started_utc": self.started_utc,
            "completed_utc": utc_now(),
            "cluster": CLUSTER,
            "protocol": "bounded descriptive cross-stack replication",
            "rows": self.rows,
        }
        (self.output_dir / "cross_stack_observations.json").write_text(
            json.dumps(payload, indent=2) + "\n"
        )
        with (self.output_dir / "cross_stack_observations.csv").open(
            "w", newline=""
        ) as handle:
            writer = csv.DictWriter(
                handle,
                fieldnames=list(self.rows[0]),
                lineterminator="\n",
            )
            writer.writeheader()
            writer.writerows(self.rows)

    def capture_failure_diagnostics(self, exc: BaseException) -> None:
        """Preserve native component evidence while the isolated context exists."""
        destination = self.output_dir / "failure_diagnostics"
        destination.mkdir(parents=True, exist_ok=True)
        index: dict[str, Any] = {
            "captured_utc": utc_now(),
            "error_type": type(exc).__name__,
            "error": str(exc),
            "traceback": traceback.format_exc(),
            "cluster": CLUSTER,
            "source_transport": "git-protocol",
            "http_head": "not-applicable; source transport is native git://",
            "commands": [],
        }
        if not self.cluster_created or self.kubeconfig is None:
            index["capture_skipped"] = "isolated cluster or kubeconfig was not available"
            (destination / "index.json").write_text(json.dumps(index, indent=2) + "\n")
            return

        commands = (
            (
                "application.json",
                ["-n", "argocd", "get", "application", "cross-payments", "-o", "json"],
            ),
            (
                "argocd-application-controller.log",
                ["-n", "argocd", "logs", "statefulset/argocd-application-controller", "--tail=500"],
            ),
            (
                "argocd-repo-server.log",
                ["-n", "argocd", "logs", "deployment/argocd-repo-server", "--tail=500"],
            ),
            (
                "git-server.log",
                ["-n", "cross-system", "logs", "deployment/git-server", "-c", "git-daemon", "--tail=500"],
            ),
            (
                "constraint-template.json",
                ["get", "constrainttemplate", "k8srequiredcrosslabel", "-o", "json"],
            ),
            (
                "payments-deployment.json",
                ["-n", "payments", "get", "deployment", "payments", "-o", "json"],
            ),
            (
                "source-ls-remote.txt",
                [
                    "-n", "argocd", "exec", "deployment/argocd-repo-server",
                    "-c", "argocd-repo-server", "--", "git", "ls-remote",
                    "git://git-server.cross-system.svc.cluster.local/repo.git",
                ],
            ),
        )
        for filename, kubectl_args in commands:
            try:
                proc = command(
                    [
                        "kubectl", "--kubeconfig", str(self.kubeconfig),
                        "--context", CONTEXT, *kubectl_args,
                    ],
                    check=False,
                    timeout=30,
                )
                (destination / filename).write_text(proc.stdout)
                if proc.stderr:
                    (destination / f"{filename}.stderr").write_text(proc.stderr)
                index["commands"].append(
                    {
                        "artifact": filename,
                        "returncode": proc.returncode,
                        "stderr_artifact": f"{filename}.stderr" if proc.stderr else None,
                    }
                )
            except BaseException as diagnostic_exc:
                index["commands"].append(
                    {
                        "artifact": filename,
                        "diagnostic_error_type": type(diagnostic_exc).__name__,
                        "diagnostic_error": str(diagnostic_exc),
                    }
                )
        (destination / "index.json").write_text(json.dumps(index, indent=2) + "\n")

    def cleanup(self) -> dict[str, Any]:
        started = utc_now()
        verification_command = ["kind", "get", "clusters"]
        delete_command = ["kind", "delete", "cluster", "--name", CLUSTER]

        def verification_evidence(
            proc: subprocess.CompletedProcess[str],
        ) -> dict[str, Any]:
            clusters = sorted({
                row.strip() for row in proc.stdout.splitlines() if row.strip()
            })
            return {
                "command": verification_command,
                "returncode": proc.returncode,
                "stdout": proc.stdout,
                "stderr": proc.stderr,
                "clusters": clusters,
            }

        before_proc = command(
            verification_command,
            check=False,
            timeout=30,
        )
        before = verification_evidence(before_proc)
        result = {
            "cluster": CLUSTER,
            "cleanup_started_utc": started,
            "target_scope": "only govdrift-cross",
            "delete_attempted": self.cluster_created,
            "delete_returncode": None,
            "verified_absent": False,
        }
        delete_evidence: dict[str, Any] = {
            "command": delete_command,
            "attempted": self.cluster_created,
            "returncode": None,
            "stdout": "",
            "stderr": "",
        }
        if self.cluster_created:
            proc = command(
                delete_command,
                check=False,
                timeout=180,
            )
            result["delete_returncode"] = proc.returncode
            delete_evidence.update(
                {
                    "returncode": proc.returncode,
                    "stdout": proc.stdout,
                    "stderr": proc.stderr,
                }
            )
        after_proc = command(
            verification_command,
            check=False,
            timeout=30,
        )
        after = verification_evidence(after_proc)
        result["verified_absent"] = (
            after["returncode"] == 0 and CLUSTER not in set(after["clusters"])
        )
        result["cleanup_proof"] = {
            "verify_before": before,
            "delete": delete_evidence,
            "verify_after": after,
        }
        result["cleanup_completed_utc"] = utc_now()
        (self.output_dir / "cleanup.json").write_text(json.dumps(result, indent=2) + "\n")
        return result

    def execute(self) -> int:
        status: dict[str, Any] = {
            "status": "running",
            "started_utc": self.started_utc,
            "cluster": CLUSTER,
            "stop_rule": {
                "ready_timeout_seconds": INSTALL_TIMEOUT_SECONDS,
                "resource_threshold_percent": 80.0,
                "sustained_samples": 3,
                "sample_interval_seconds": 5.0,
            },
        }
        cleanup: dict[str, Any] = {}
        try:
            verified = self.download_and_verify()
            self.write_manifest_inventory(verified)
            host_images = self.verify_host_images()
            self.monitor = ResourceMonitor(self.resources)
            self.monitor.start()
            self.monitor.wait_samples(3, timeout=30)
            self.check_stop()
            self.install_deadline = time.monotonic() + INSTALL_TIMEOUT_SECONDS

            with tempfile.TemporaryDirectory(prefix="govdrift-cross-kubeconfig-") as temporary:
                kubeconfig = Path(temporary) / "kubeconfig"
                try:
                    self.create_cluster(kubeconfig)
                    self.load_and_alias_images()
                    self.install_stack(verified)
                    self.deploy_sources_and_controls()
                    self.run_schedule()
                    platform_payload = self.platform_snapshot(verified, host_images)
                    (self.output_dir / "platform.json").write_text(
                        json.dumps(platform_payload, indent=2) + "\n"
                    )
                    self.write_observations()
                except BaseException as exc:
                    self.capture_failure_diagnostics(exc)
                    raise

            status.update(
                {
                    "status": "completed",
                    "completed_utc": utc_now(),
                    "observations": len(self.rows),
                    "all_exact": all(row["exact_set"] for row in self.rows),
                    "api_read_errors": self.api_read_errors,
                    "stop_rule_triggered": False,
                }
            )
            return_code = 0
        except BaseException as exc:
            status.update(
                {
                    "status": (
                        "aborted"
                        if isinstance(exc, (StopRule, KeyboardInterrupt))
                        else "failed"
                    ),
                    "completed_utc": utc_now(),
                    "error_type": type(exc).__name__,
                    "error": str(exc),
                    "traceback": traceback.format_exc(),
                    "observations": len(self.rows),
                    "api_read_errors": self.api_read_errors,
                    "stop_rule_triggered": isinstance(exc, StopRule),
                    "resource_stop_reason": self.monitor.stop_reason if self.monitor else None,
                }
            )
            self.install.add(
                "run_failure",
                status=status["status"],
                error_type=type(exc).__name__,
                error=str(exc),
            )
            return_code = 2
        finally:
            cleanup = self.cleanup()
            if self.monitor:
                self.monitor.close()
            if not cleanup.get("verified_absent", False):
                status.update(
                    {
                        "status": "cleanup_failed",
                        "error_type": "CleanupError",
                        "error": "govdrift-cross was not verified absent",
                    }
                )
                return_code = 2
            status["cleanup"] = cleanup
            (self.output_dir / "run_status.json").write_text(
                json.dumps(status, indent=2) + "\n"
            )
        if return_code:
            print(json.dumps(status, indent=2), file=sys.stderr)
        return return_code


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", type=Path, default=RESULTS)
    args = parser.parse_args()
    return CrossStackExperiment(output_dir=args.output_dir).execute()


if __name__ == "__main__":
    raise SystemExit(main())
