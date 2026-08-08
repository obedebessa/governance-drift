#!/usr/bin/env python3
"""Auditable positive replication of Governance Drift scenarios S1--S9.

The campaign owns only the ``govdrift-trace`` namespace, the
``govdrift-trace-git`` Docker container, a fresh temporary runtime, and a new
campaign directory below ``lab/results_trace``.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import os
import shutil
import statistics
import subprocess
import sys
import tempfile
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from trace_evaluator import canonical_bytes, evaluate, normalize_image_id, sha256_file


LAB = Path(__file__).resolve().parent
RESULTS_ROOT = LAB / "results_trace"
IMAGE_LOCK = LAB / "image-lock.json"
NAMESPACE = "govdrift-trace"
DEPLOYMENT = "payments-trace"
POLICY = "governance-baseline-trace"
GIT_CONTAINER = "govdrift-trace-git"
CADENCES = (1.0, 5.0, 10.0)
EXPECTED = {
    "S1": ["configuration"],
    "S2": ["authorization"],
    "S3": ["policy"],
    "S4": ["authorization"],
    "S5": ["environment"],
    "S6": ["authorization", "intent"],
    "S7": ["environment"],
    "S8": ["authorization"],
    "S9": ["evidence"],
}
SCENARIO_LABELS = {
    "S1": "manual in-cluster configuration change",
    "S2": "expired temporary exception with persistent effect",
    "S3": "policy supersession",
    "S4": "admission-time artifact substitution",
    "S5": "IAM authorization-surface expansion",
    "S6": "unapproved Git rollback followed by Flux convergence",
    "S7": "out-of-band load-balancer change",
    "S8": "live authorization identity mismatch",
    "S9": "missing live continuing-authorization status",
}


COMMAND_LEDGER: list[dict[str, Any]] = []


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def sha256_value(value: Any) -> str:
    return hashlib.sha256(canonical_bytes(value)).hexdigest()


def atomic_json(path: Path, value: Any, *, mode: int | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        json.dump(value, handle, indent=2, sort_keys=True, ensure_ascii=False)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)
    if mode is not None:
        path.chmod(mode)


def atomic_text(path: Path, value: str, *, mode: int | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        handle.write(value)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)
    if mode is not None:
        path.chmod(mode)


def run(
    *args: str,
    cwd: Path | None = None,
    input_text: str | None = None,
    check: bool = True,
) -> str:
    started_mono = time.monotonic()
    started_utc = utc_now()
    completed = subprocess.run(
        args,
        cwd=cwd,
        input=input_text,
        text=True,
        capture_output=True,
    )
    completed_mono = time.monotonic()
    row = {
        "sequence": len(COMMAND_LEDGER) + 1,
        "argv": list(args),
        "cwd": str(cwd.resolve()) if cwd else None,
        "started_mono": started_mono,
        "started_utc": started_utc,
        "completed_mono": completed_mono,
        "completed_utc": utc_now(),
        "duration_seconds": completed_mono - started_mono,
        "returncode": completed.returncode,
        "stdout_sha256": hashlib.sha256(completed.stdout.encode()).hexdigest(),
        "stderr_sha256": hashlib.sha256(completed.stderr.encode()).hexdigest(),
        "stdout_tail": completed.stdout[-4096:],
        "stderr_tail": completed.stderr[-4096:],
    }
    COMMAND_LEDGER.append(row)
    if check and completed.returncode:
        raise RuntimeError(
            f"command failed ({completed.returncode}): {' '.join(args)}\n"
            f"stdout={completed.stdout}\nstderr={completed.stderr}"
        )
    return completed.stdout


def kubectl(*args: str, check: bool = True) -> str:
    return run("kubectl", *args, check=check)


def kubectl_json(*args: str) -> dict[str, Any]:
    return json.loads(kubectl(*args, "-o", "json"))


def wait_until(
    predicate: Callable[[], bool], *, timeout: float, description: str, interval: float = 0.25
) -> None:
    deadline = time.monotonic() + timeout
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        try:
            if predicate():
                return
        except Exception as exc:
            last_error = exc
        time.sleep(interval)
    suffix = f"; last error: {last_error}" if last_error else ""
    raise TimeoutError(f"timed out waiting for {description}{suffix}")


def image_refs() -> dict[str, str]:
    lock = json.loads(IMAGE_LOCK.read_text(encoding="utf-8"))["images"]
    refs = {
        "python": lock["python:3.12-slim"],
        "baseline": lock["nginx:1.27-alpine"],
        "alternate": lock["nginx:1.26-alpine"],
    }
    if any("@sha256:" not in ref for ref in refs.values()):
        raise ValueError("trace campaign requires digest-locked image references")
    return refs


def deployment_object(image: str, version: str) -> dict[str, Any]:
    return {
        "apiVersion": "apps/v1",
        "kind": "Deployment",
        "metadata": {
            "name": DEPLOYMENT,
            "namespace": NAMESPACE,
            "labels": {"app": DEPLOYMENT, "env": "prod", "team-owner": "platform"},
        },
        "spec": {
            "replicas": 1,
            "selector": {"matchLabels": {"app": DEPLOYMENT}},
            "template": {
                "metadata": {"labels": {"app": DEPLOYMENT}},
                "spec": {
                    "containers": [{
                        "name": DEPLOYMENT,
                        "image": image,
                        "imagePullPolicy": "IfNotPresent",
                        "env": [{"name": "APP_VERSION", "value": version}],
                        "resources": {
                            "limits": {"cpu": "100m", "memory": "64Mi"},
                            "requests": {"cpu": "25m", "memory": "16Mi"},
                        },
                    }]
                },
            },
        },
    }


def policy_object(version: str) -> dict[str, Any]:
    return {
        "apiVersion": "kyverno.io/v1",
        "kind": "Policy",
        "metadata": {
            "name": POLICY,
            "namespace": NAMESPACE,
            "labels": {"policy-version": version},
        },
        "spec": {
            "validationFailureAction": "Audit",
            "background": True,
            "rules": [{
                "name": f"require-prod-env-{version}",
                "match": {"any": [{"resources": {"kinds": ["Deployment"], "names": [DEPLOYMENT]}}]},
                "validate": {
                    "message": f"env label required by {version}",
                    "pattern": {"metadata": {"labels": {"env": "prod"}}},
                },
            }],
        },
    }


def mutation_policy_object(alternate: str) -> dict[str, Any]:
    return {
        "apiVersion": "kyverno.io/v1",
        "kind": "Policy",
        "metadata": {"name": "artifact-substituter", "namespace": NAMESPACE},
        "spec": {
            "background": False,
            "rules": [{
                "name": "substitute-materialized-artifact",
                "match": {"any": [{"resources": {"kinds": ["Pod"], "selector": {"matchLabels": {"app": DEPLOYMENT}}}}]},
                "mutate": {
                    "patchStrategicMerge": {
                        "spec": {"containers": [{"(name)": DEPLOYMENT, "image": alternate}]}
                    }
                },
            }],
        },
    }


def load_image_into_kind(reference: str) -> None:
    run("docker", "pull", reference)
    nodes = [line for line in run("kind", "get", "nodes", "--name", "govdrift-lab").splitlines() if line]
    if not nodes:
        raise RuntimeError("kind cluster govdrift-lab has no nodes")
    for node in nodes:
        started = time.monotonic()
        save = subprocess.Popen(["docker", "save", reference], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        assert save.stdout is not None
        imported = subprocess.run(
            ["docker", "exec", "-i", node, "ctr", "--namespace=k8s.io", "images", "import", "-"],
            stdin=save.stdout,
            text=False,
            capture_output=True,
        )
        save.stdout.close()
        save_stderr = save.stderr.read() if save.stderr else b""
        save_code = save.wait()
        COMMAND_LEDGER.append({
            "sequence": len(COMMAND_LEDGER) + 1,
            "argv": ["docker", "save", reference, "|", "docker", "exec", "-i", node, "ctr", "--namespace=k8s.io", "images", "import", "-"],
            "cwd": None,
            "started_mono": started,
            "started_utc": None,
            "completed_mono": time.monotonic(),
            "completed_utc": utc_now(),
            "duration_seconds": time.monotonic() - started,
            "returncode": imported.returncode or save_code,
            "stdout_sha256": hashlib.sha256(imported.stdout).hexdigest(),
            "stderr_sha256": hashlib.sha256(save_stderr + imported.stderr).hexdigest(),
            "stdout_tail": imported.stdout.decode(errors="replace")[-4096:],
            "stderr_tail": (save_stderr + imported.stderr).decode(errors="replace")[-4096:],
        })
        if save_code or imported.returncode:
            raise RuntimeError(f"failed to import {reference} into {node}")


def create_repository(runtime: Path, refs: dict[str, str]) -> dict[str, str]:
    bare = runtime / "git/remote.git"
    work = runtime / "work"
    bare.parent.mkdir(parents=True)
    run("git", "init", "--bare", str(bare))
    run("git", "clone", str(bare), str(work))
    run("git", "config", "user.name", "Governance Drift Trace Lab", cwd=work)
    run("git", "config", "user.email", "trace@example.invalid", cwd=work)
    workload = work / "workload"
    workload.mkdir()
    atomic_text(workload / "kustomization.yaml", "resources:\n  - deployment.json\n")
    predecessor = deployment_object(refs["alternate"], "v1-predecessor")
    baseline = deployment_object(refs["baseline"], "v2-approved")
    atomic_json(workload / "deployment.json", predecessor)
    run("git", "add", "workload", cwd=work)
    run("git", "commit", "-m", "trace predecessor: unapproved rollback target", cwd=work)
    predecessor_revision = run("git", "rev-parse", "HEAD", cwd=work).strip()
    atomic_json(workload / "deployment.json", baseline)
    run("git", "add", "workload/deployment.json", cwd=work)
    run("git", "commit", "-m", "trace baseline: approved state", cwd=work)
    baseline_revision = run("git", "rev-parse", "HEAD", cwd=work).strip()
    run("git", "push", "origin", "HEAD:master", cwd=work)
    atomic_json(runtime / "deployment-predecessor.json", predecessor, mode=0o444)
    atomic_json(runtime / "deployment-baseline.json", baseline, mode=0o444)
    return {"predecessor": predecessor_revision, "baseline": baseline_revision}


def start_git_server(runtime: Path, python_image: str, campaign_id: str) -> None:
    existing = run(
        "docker", "ps", "-a", "--filter", f"name=^/{GIT_CONTAINER}$", "--format", "{{.Names}}"
    ).strip()
    if existing:
        raise RuntimeError(f"refusing to replace pre-existing container {GIT_CONTAINER}")
    run(
        "docker", "run", "-d", "--restart=no", "--network", "kind", "--name", GIT_CONTAINER,
        "--label", f"govdrift.campaign={campaign_id}",
        "-v", f"{runtime / 'git'}:/git:ro",
        "-v", f"{LAB / 'git-http'}:/srv/cgi-bin:ro",
        python_image,
        "sh", "-c",
        "apt-get update -qq && apt-get install -y -qq git >/dev/null && "
        "cd /srv && exec python3 -m http.server --cgi 8000",
    )


def write_cluster_inputs(runtime: Path, refs: dict[str, str]) -> None:
    atomic_json(runtime / "policy-baseline.json", policy_object("pi-7"), mode=0o444)
    atomic_json(runtime / "policy-superseded.json", policy_object("pi-8"), mode=0o444)
    atomic_json(runtime / "policy-mutation.json", mutation_policy_object(refs["alternate"]), mode=0o444)
    flux = {
        "apiVersion": "v1",
        "kind": "List",
        "items": [
            {
                "apiVersion": "source.toolkit.fluxcd.io/v1",
                "kind": "GitRepository",
                "metadata": {"name": "trace-source", "namespace": NAMESPACE},
                "spec": {
                    "interval": "1h",
                    "url": f"http://{GIT_CONTAINER}:8000/cgi-bin/git/remote.git",
                    "ref": {"branch": "master"},
                },
            },
            {
                "apiVersion": "kustomize.toolkit.fluxcd.io/v1",
                "kind": "Kustomization",
                "metadata": {"name": "trace-workload", "namespace": NAMESPACE},
                "spec": {
                    "interval": "1h",
                    "retryInterval": "10s",
                    "timeout": "2m",
                    "sourceRef": {"kind": "GitRepository", "name": "trace-source"},
                    "path": "./workload",
                    "prune": True,
                    "wait": True,
                    "targetNamespace": NAMESPACE,
                },
            },
        ],
    }
    atomic_json(runtime / "flux.json", flux, mode=0o444)


def git_source_revision() -> str:
    obj = kubectl_json("-n", NAMESPACE, "get", "gitrepository", "trace-source")
    return str(obj.get("status", {}).get("artifact", {}).get("revision", ""))


def kustomization_revision() -> str:
    obj = kubectl_json("-n", NAMESPACE, "get", "kustomization", "trace-workload")
    return str(obj.get("status", {}).get("lastAppliedRevision", ""))


def reconcile(revision: str) -> None:
    token = str(uuid.uuid4())
    kubectl(
        "-n", NAMESPACE, "annotate", "gitrepository", "trace-source",
        f"reconcile.fluxcd.io/requestedAt={token}", "--overwrite",
    )
    wait_until(
        lambda: revision in git_source_revision(),
        timeout=180,
        description=f"Flux source revision {revision[:12]}",
        interval=1.0,
    )
    kubectl(
        "-n", NAMESPACE, "annotate", "kustomization", "trace-workload",
        f"reconcile.fluxcd.io/requestedAt={token}", "--overwrite",
    )
    wait_until(
        lambda: revision in kustomization_revision(),
        timeout=180,
        description=f"Flux applied revision {revision[:12]}",
        interval=1.0,
    )


def wait_rollout() -> None:
    kubectl("-n", NAMESPACE, "rollout", "status", f"deployment/{DEPLOYMENT}", "--timeout=180s")


def active_image_ids() -> list[str]:
    pods = kubectl_json("-n", NAMESPACE, "get", "pods", "-l", f"app={DEPLOYMENT}")
    values = []
    for pod in pods.get("items", []):
        if pod.get("metadata", {}).get("deletionTimestamp"):
            continue
        for status in pod.get("status", {}).get("containerStatuses", []):
            if status.get("imageID"):
                values.append(normalize_image_id(status["imageID"]))
    return sorted(set(values))


def setup(runtime: Path, results: Path, campaign_id: str) -> dict[str, Any]:
    context = run("kubectl", "config", "current-context").strip()
    if context != "kind-govdrift-lab":
        raise RuntimeError(f"expected context kind-govdrift-lab, observed {context}")
    if kubectl("get", "namespace", NAMESPACE, "--ignore-not-found", "-o", "name").strip():
        raise RuntimeError(f"refusing to touch pre-existing namespace {NAMESPACE}")

    refs = image_refs()
    revisions = create_repository(runtime, refs)
    write_cluster_inputs(runtime, refs)
    start_git_server(runtime, refs["python"], campaign_id)
    load_image_into_kind(refs["baseline"])
    load_image_into_kind(refs["alternate"])

    namespace = {
        "apiVersion": "v1",
        "kind": "Namespace",
        "metadata": {
            "name": NAMESPACE,
            "labels": {
                "app.kubernetes.io/managed-by": "govdrift-trace",
                "govdrift.io/campaign": campaign_id,
            },
        },
    }
    atomic_json(runtime / "namespace.json", namespace, mode=0o444)
    kubectl("apply", "-f", str(runtime / "namespace.json"))
    kubectl("apply", "-f", str(runtime / "policy-baseline.json"))
    kubectl("apply", "-f", str(runtime / "flux.json"))
    reconcile(revisions["baseline"])
    wait_rollout()

    ids = active_image_ids()
    if len(ids) != 1:
        raise RuntimeError(f"baseline requires one materialized image digest, observed {ids}")
    approval = {
        "id": "APR-TRACE-BASE",
        "kind": "deployment-authorization",
        "mode": "continuing",
        "valid_at_execution": True,
        "subject": f"deployment/{DEPLOYMENT}",
        "unit_ref": {
            "cluster": "kind-govdrift-lab",
            "namespace": NAMESPACE,
            "kind": "Deployment",
            "name": DEPLOYMENT,
        },
        "revisions": [revisions["baseline"]],
        "subjects": ids,
        "revoked": False,
        "revocation_effect": "prospective",
    }
    inventory = {
        "load_balancer": {"listeners": [443], "tls_policy": "TLS-1-3-2025"},
        "iam": {"actions": ["logs:PutLogEvents"], "wildcard": False},
        "region": "us-east-1",
    }
    basis = {
        "schema": "govdrift-trace-basis/v1",
        "basis_id": f"{campaign_id}:baseline",
        "captured_utc": utc_now(),
        "policy_version": "pi-7",
        "approval": approval,
        "inventory": inventory,
        "deployment_ref": refs["baseline"],
        "baseline_revision": revisions["baseline"],
    }
    (runtime / "approvals").mkdir()
    (runtime / "proofs").mkdir()
    atomic_json(runtime / "basis.json", basis, mode=0o444)
    atomic_json(runtime / "approval-baseline.json", approval, mode=0o444)
    atomic_json(runtime / "approvals/APR-TRACE-BASE.json", approval)
    atomic_json(runtime / "cloud-inventory-baseline.json", inventory, mode=0o444)
    atomic_json(runtime / "cloud-inventory.json", inventory)

    observed = evaluate(runtime=runtime, namespace=NAMESPACE, deployment=DEPLOYMENT, policy=POLICY)
    if observed["verdict"] != "consistent":
        raise RuntimeError(f"setup baseline is not consistent: {observed}")
    atomic_json(results / "setup_evaluation.json", observed)
    return {"refs": refs, "revisions": revisions, "approval": approval, "inventory": inventory}


def clear_json_directory(path: Path) -> None:
    for item in sorted(path.glob("*.json")):
        item.chmod(0o644)
        item.unlink()


def reset_baseline(runtime: Path, state: dict[str, Any]) -> dict[str, Any]:
    kubectl("-n", NAMESPACE, "delete", "policy", "artifact-substituter", "--ignore-not-found")
    work = runtime / "work"
    baseline = state["revisions"]["baseline"]
    run("git", "checkout", "--detach", baseline, cwd=work)
    run("git", "push", "--force", "origin", f"{baseline}:master", cwd=work)
    clear_json_directory(runtime / "approvals")
    clear_json_directory(runtime / "proofs")
    atomic_json(runtime / "approvals/APR-TRACE-BASE.json", state["approval"])
    atomic_json(runtime / "cloud-inventory.json", state["inventory"])
    kubectl("apply", "-f", str(runtime / "policy-baseline.json"))
    reconcile(baseline)
    kubectl("apply", "-f", str(runtime / "deployment-baseline.json"))
    kubectl("-n", NAMESPACE, "rollout", "restart", f"deployment/{DEPLOYMENT}")
    wait_rollout()

    def consistent() -> bool:
        result = evaluate(runtime=runtime, namespace=NAMESPACE, deployment=DEPLOYMENT, policy=POLICY)
        return result["verdict"] == "consistent"

    wait_until(consistent, timeout=120, description="consistent reset baseline", interval=0.5)
    return evaluate(runtime=runtime, namespace=NAMESPACE, deployment=DEPLOYMENT, policy=POLICY)


def causal_window(action: Callable[[], None]) -> dict[str, Any]:
    started_mono = time.monotonic()
    started_utc = utc_now()
    action()
    completed_mono = time.monotonic()
    return {
        "cause_started_mono": started_mono,
        "cause_started_utc": started_utc,
        "effect_observed_mono": completed_mono,
        "effect_observed_utc": utc_now(),
    }


def inject_s1(runtime: Path, state: dict[str, Any]) -> dict[str, Any]:
    del runtime, state
    patch = '[{"op":"replace","path":"/spec/template/spec/containers/0/env/0/value","value":"v2-tampered"}]'
    return causal_window(lambda: (
        kubectl("-n", NAMESPACE, "patch", "deployment", DEPLOYMENT, "--type=json", "-p", patch),
        wait_rollout(),
    ))


def inject_s2(runtime: Path, state: dict[str, Any]) -> dict[str, Any]:
    preparation_started_mono = time.monotonic()
    exception = {
        "id": "EXC-TRACE-S2",
        "kind": "temporary-exception",
        "mode": "temporary-exception",
        "valid_at_execution": True,
        "subject": f"deployment/{DEPLOYMENT}",
        "unit_ref": state["approval"]["unit_ref"],
        "revisions": [state["revisions"]["baseline"]],
        "subjects": list(state["approval"]["subjects"]),
        "revoked": False,
        "removed": False,
    }
    patch = json.dumps({
        "spec": {"template": {"metadata": {"annotations": {"emergency-debug": exception["id"]}}}}
    }, separators=(",", ":"))
    kubectl("-n", NAMESPACE, "patch", "deployment", DEPLOYMENT, "--type=merge", "-p", patch)
    wait_rollout()
    expires_seconds = 6.0
    expires_utc_epoch = time.time() + expires_seconds
    expires_mono = time.monotonic() + expires_seconds
    exception["expires_utc"] = expires_utc_epoch
    atomic_json(runtime / "proofs/EXC-TRACE-S2.json", exception, mode=0o444)
    atomic_json(runtime / "approvals/EXC-TRACE-S2.json", exception)
    preparation_completed_mono = time.monotonic()
    before_cause = evaluate(runtime=runtime, namespace=NAMESPACE, deployment=DEPLOYMENT, policy=POLICY)
    if before_cause["verdict"] != "consistent":
        raise RuntimeError(f"S2 preparation must remain authorized: {before_cause}")
    while time.monotonic() < expires_mono:
        time.sleep(min(0.05, expires_mono - time.monotonic()))
    return {
        "preparation_started_mono": preparation_started_mono,
        "preparation_completed_mono": preparation_completed_mono,
        "cause_started_mono": expires_mono,
        "cause_started_utc": datetime.fromtimestamp(expires_utc_epoch, timezone.utc).isoformat(),
        "effect_observed_mono": time.monotonic(),
        "effect_observed_utc": utc_now(),
        "state_immediately_before_cause": before_cause,
        "exception_id": exception["id"],
        "expires_utc_epoch": expires_utc_epoch,
    }


def inject_s3(runtime: Path, state: dict[str, Any]) -> dict[str, Any]:
    del state
    return causal_window(lambda: kubectl("apply", "-f", str(runtime / "policy-superseded.json")))


def inject_s4(runtime: Path, state: dict[str, Any]) -> dict[str, Any]:
    del state
    kubectl("apply", "-f", str(runtime / "policy-mutation.json"))
    # Admission configuration must be accepted before creating the substituted Pod.
    time.sleep(1.0)
    return causal_window(lambda: (
        kubectl("-n", NAMESPACE, "rollout", "restart", f"deployment/{DEPLOYMENT}"),
        wait_rollout(),
    ))


def inject_s5(runtime: Path, state: dict[str, Any]) -> dict[str, Any]:
    del state
    def action() -> None:
        inventory = json.loads((runtime / "cloud-inventory.json").read_text(encoding="utf-8"))
        inventory["iam"] = {"actions": ["*"], "wildcard": True}
        atomic_json(runtime / "cloud-inventory.json", inventory)
    return causal_window(action)


def inject_s6(runtime: Path, state: dict[str, Any]) -> dict[str, Any]:
    predecessor = state["revisions"]["predecessor"]
    def action() -> None:
        run("git", "checkout", "--detach", predecessor, cwd=runtime / "work")
        run("git", "push", "--force", "origin", f"{predecessor}:master", cwd=runtime / "work")
        reconcile(predecessor)
        wait_rollout()
    return causal_window(action)


def inject_s7(runtime: Path, state: dict[str, Any]) -> dict[str, Any]:
    del state
    def action() -> None:
        inventory = json.loads((runtime / "cloud-inventory.json").read_text(encoding="utf-8"))
        inventory["load_balancer"] = {"listeners": [80, 443], "tls_policy": "TLS-1-0-LEGACY"}
        atomic_json(runtime / "cloud-inventory.json", inventory)
    return causal_window(action)


def inject_s8(runtime: Path, state: dict[str, Any]) -> dict[str, Any]:
    del state
    def action() -> None:
        path = runtime / "approvals/APR-TRACE-BASE.json"
        live = json.loads(path.read_text(encoding="utf-8"))
        live["subject"] = "deployment/unrelated-workload"
        atomic_json(path, live)
    return causal_window(action)


def inject_s9(runtime: Path, state: dict[str, Any]) -> dict[str, Any]:
    del state
    return causal_window(lambda: (runtime / "approvals/APR-TRACE-BASE.json").unlink())


INJECTORS: dict[str, Callable[[Path, dict[str, Any]], dict[str, Any]]] = {
    "S1": inject_s1,
    "S2": inject_s2,
    "S3": inject_s3,
    "S4": inject_s4,
    "S5": inject_s5,
    "S6": inject_s6,
    "S7": inject_s7,
    "S8": inject_s8,
    "S9": inject_s9,
}


def read_ndjson(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            rows.append(json.loads(line))
    return rows


def is_exact(row: dict[str, Any], expected: list[str]) -> bool:
    correct_verdict = "undecidable" if expected == ["evidence"] else "drift"
    return row.get("verdict") == correct_verdict and sorted(row.get("class_set", [])) == sorted(expected)


def event_markers(
    rows: list[dict[str, Any]], cause_started_mono: float, expected: list[str]
) -> dict[str, dict[str, Any] | None]:
    post = [row for row in rows if float(row["completed_mono"]) >= cause_started_mono]
    first_alert = next((row for row in post if row.get("verdict") != "consistent"), None)
    exact = next((row for row in post if is_exact(row, expected)), None)
    two_poll_exact = None
    for previous, current in zip(post, post[1:]):
        if is_exact(previous, expected) and is_exact(current, expected):
            two_poll_exact = current
            break

    def marker(row: dict[str, Any] | None) -> dict[str, Any] | None:
        if row is None:
            return None
        return {
            "sequence": row["sequence"],
            "poll_sha256": row["poll_sha256"],
            "actual_start_mono": row["actual_start_mono"],
            "actual_start_utc": row["actual_start_utc"],
            "completed_mono": row["completed_mono"],
            "completed_utc": row["completed_utc"],
            "verdict": row["verdict"],
            "class_set": row["class_set"],
        }
    return {
        "first_alert": marker(first_alert),
        "exact": marker(exact),
        "two_poll_exact": marker(two_poll_exact),
    }


def start_observers(
    campaign_id: str, scenario: str, runtime: Path, results: Path
) -> tuple[list[dict[str, Any]], Path]:
    raw = results / "raw" / scenario
    raw.mkdir(parents=True)
    stop_file = raw / "STOP"
    start_mono = time.monotonic() + 0.5
    processes: list[dict[str, Any]] = []
    for cadence in CADENCES:
        label = f"{int(cadence):02d}s"
        output = raw / f"observer-{label}.ndjson"
        ready = raw / f"observer-{label}.ready.json"
        stderr_path = raw / f"observer-{label}.stderr.log"
        stderr_handle = stderr_path.open("w", encoding="utf-8")
        argv = [
            sys.executable, str(LAB / "trace_observer.py"),
            "--campaign-id", campaign_id,
            "--scenario", scenario,
            "--cadence", str(cadence),
            "--runtime", str(runtime),
            "--namespace", NAMESPACE,
            "--deployment", DEPLOYMENT,
            "--policy", POLICY,
            "--start-mono", str(start_mono),
            "--stop-file", str(stop_file),
            "--ready-file", str(ready),
            "--output", str(output),
        ]
        process = subprocess.Popen(argv, stdout=subprocess.DEVNULL, stderr=stderr_handle, text=True)
        processes.append({
            "cadence": cadence,
            "process": process,
            "output": output,
            "ready": ready,
            "stderr": stderr_path,
            "stderr_handle": stderr_handle,
        })
    wait_until(
        lambda: all(item["ready"].exists() for item in processes),
        timeout=20,
        description=f"{scenario} observer readiness",
    )
    return processes, stop_file


def wait_baseline(processes: list[dict[str, Any]], scenario: str) -> None:
    def ready() -> bool:
        for item in processes:
            if item["process"].poll() is not None:
                raise RuntimeError(f"{scenario} observer exited before baseline")
            rows = read_ndjson(item["output"])
            if not rows or not any(row.get("verdict") == "consistent" for row in rows):
                return False
        return True
    wait_until(ready, timeout=30, description=f"{scenario} baseline poll from every observer")


def wait_all_two_poll_exact(
    processes: list[dict[str, Any]], scenario: str, cause: float, expected: list[str]
) -> None:
    def complete() -> bool:
        for item in processes:
            if item["process"].poll() is not None:
                raise RuntimeError(f"{scenario} observer exited before two-poll exact detection")
            markers = event_markers(read_ndjson(item["output"]), cause, expected)
            if markers["two_poll_exact"] is None:
                return False
        return True
    wait_until(
        complete,
        timeout=100,
        description=f"{scenario} two-poll exact detections",
        interval=0.2,
    )


def stop_observers(processes: list[dict[str, Any]], stop_file: Path) -> None:
    atomic_text(stop_file, utc_now() + "\n")
    for item in processes:
        try:
            item["process"].wait(timeout=20)
        except subprocess.TimeoutExpired:
            item["process"].terminate()
            item["process"].wait(timeout=5)
        finally:
            item["stderr_handle"].close()
        if item["process"].returncode:
            raise RuntimeError(
                f"observer cadence {item['cadence']} exited {item['process'].returncode}: "
                f"{item['stderr'].read_text(encoding='utf-8')}"
            )


def summarize_scenario(
    scenario: str,
    injection: dict[str, Any],
    processes: list[dict[str, Any]],
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    trajectories = []
    cause = float(injection["cause_started_mono"])
    effect = float(injection["effect_observed_mono"])
    for item in processes:
        rows = read_ndjson(item["output"])
        ready = json.loads(item["ready"].read_text(encoding="utf-8"))
        markers = event_markers(rows, cause, EXPECTED[scenario])
        if any(value is None for value in markers.values()):
            raise RuntimeError(f"incomplete trajectory {scenario}/{item['cadence']}: {markers}")
        first = markers["first_alert"]
        exact = markers["exact"]
        two_poll_exact = markers["two_poll_exact"]
        assert first and exact and two_poll_exact
        trajectories.append({
            "scenario": scenario,
            "scenario_label": SCENARIO_LABELS[scenario],
            "expected_class_set": EXPECTED[scenario],
            "cadence_seconds": item["cadence"],
            "observer_id": ready["observer_id"],
            "pid": ready["pid"],
            "raw_path": str(item["output"].relative_to(item["output"].parents[2])),
            "polls": len(rows),
            "pre_cause_polls": sum(float(row["completed_mono"]) < cause for row in rows),
            "post_cause_polls": sum(float(row["completed_mono"]) >= cause for row in rows),
            "adapter_error_polls": sum(bool(row.get("adapter_error")) for row in rows),
            "first_alert": first,
            "first_exact": exact,
            "two_poll_exact": two_poll_exact,
            "cause_to_first_alert_seconds": float(first["completed_mono"]) - cause,
            "cause_to_first_exact_seconds": float(exact["completed_mono"]) - cause,
            "cause_to_two_poll_exact_seconds": float(two_poll_exact["completed_mono"]) - cause,
            "effect_to_first_exact_seconds": float(exact["completed_mono"]) - effect,
            "exact_correct": sorted(exact["class_set"]) == sorted(EXPECTED[scenario]),
        })
    summary = {
        "schema": "govdrift-trace-scenario-summary/v2",
        "scenario": scenario,
        "scenario_label": SCENARIO_LABELS[scenario],
        "expected_class_set": EXPECTED[scenario],
        "injection_id": injection["injection_id"],
        "injection_sha256": injection["record_sha256"],
        "cause_started_mono": cause,
        "cause_started_utc": injection["cause_started_utc"],
        "effect_observed_mono": effect,
        "effect_observed_utc": injection["effect_observed_utc"],
        "observer_processes": len(trajectories),
        "distinct_pids": len({row["pid"] for row in trajectories}),
        "distinct_observer_ids": len({row["observer_id"] for row in trajectories}),
        "all_exact": all(row["exact_correct"] for row in trajectories),
        "all_two_poll_exact": all(row["two_poll_exact"] is not None for row in trajectories),
        "trajectories": trajectories,
    }
    return summary, trajectories


def run_scenario(
    campaign_id: str,
    scenario: str,
    runtime: Path,
    results: Path,
    state: dict[str, Any],
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    baseline = reset_baseline(runtime, state)
    processes, stop_file = start_observers(campaign_id, scenario, runtime, results)
    try:
        wait_baseline(processes, scenario)
        before = evaluate(runtime=runtime, namespace=NAMESPACE, deployment=DEPLOYMENT, policy=POLICY)
        command_first = len(COMMAND_LEDGER) + 1
        injection_id = f"{campaign_id}:{scenario}:{uuid.uuid4()}"
        causal = INJECTORS[scenario](runtime, state)
        after = evaluate(runtime=runtime, namespace=NAMESPACE, deployment=DEPLOYMENT, policy=POLICY)
        record = {
            "schema": "govdrift-trace-injection/v1",
            "campaign_id": campaign_id,
            "scenario": scenario,
            "scenario_label": SCENARIO_LABELS[scenario],
            "injection_id": injection_id,
            "expected_class_set": EXPECTED[scenario],
            "command_sequences": list(range(command_first, len(COMMAND_LEDGER) + 1)),
            "before_evaluation": before,
            "after_effect_evaluation": after,
            "before_input_fingerprint_sha256": before["input_fingerprint_sha256"],
            "after_input_fingerprint_sha256": after["input_fingerprint_sha256"],
            "harness_sha256": sha256_file(Path(__file__)),
            "observer_sha256": sha256_file(LAB / "trace_observer.py"),
            "evaluator_sha256": sha256_file(LAB / "trace_evaluator.py"),
            **causal,
        }
        record["record_sha256"] = sha256_value(record)
        atomic_json(results / "injections" / f"{scenario}.json", record)
        wait_all_two_poll_exact(
            processes,
            scenario,
            float(record["cause_started_mono"]),
            EXPECTED[scenario],
        )
    finally:
        stop_observers(processes, stop_file)
    summary, trajectories = summarize_scenario(scenario, record, processes)
    atomic_json(results / "summaries" / f"{scenario}.json", summary)
    return summary, trajectories


def capture_artifacts(runtime: Path, results: Path, state: dict[str, Any]) -> None:
    artifacts = results / "artifacts"
    artifacts.mkdir(parents=True)
    for name in (
        "basis.json", "approval-baseline.json", "cloud-inventory-baseline.json",
        "deployment-baseline.json", "deployment-predecessor.json", "policy-baseline.json",
        "policy-superseded.json", "policy-mutation.json", "namespace.json", "flux.json",
    ):
        shutil.copy2(runtime / name, artifacts / name)
    source_dir = artifacts / "source"
    source_dir.mkdir()
    for source in (Path(__file__), LAB / "trace_observer.py", LAB / "trace_evaluator.py", IMAGE_LOCK):
        shutil.copy2(source, source_dir / source.name)
    run("git", "bundle", "create", str(artifacts / "repository.bundle"), "--all", cwd=runtime / "work")
    atomic_text(
        artifacts / "git-log.txt",
        run("git", "log", "--all", "--decorate", "--stat", "--format=fuller", cwd=runtime / "work"),
    )
    platform = {
        "captured_utc": utc_now(),
        "context": run("kubectl", "config", "current-context").strip(),
        "kubernetes_version": json.loads(run("kubectl", "version", "-o", "json")),
        "nodes": json.loads(kubectl("get", "nodes", "-o", "json")),
        "flux_deployments": json.loads(kubectl("-n", "flux-system", "get", "deployments", "-o", "json")),
        "kyverno_deployments": json.loads(kubectl("-n", "kyverno", "get", "deployments", "-o", "json")),
        "trace_namespace": json.loads(kubectl("get", "namespace", NAMESPACE, "-o", "json")),
        "trace_resources": json.loads(kubectl("-n", NAMESPACE, "get", "all", "-o", "json")),
        "image_references": state["refs"],
        "git_revisions": state["revisions"],
        "host": {
            "python": sys.version,
            "platform": run("uname", "-a").strip(),
            "docker_version": run("docker", "version", "--format", "{{json .}}").strip(),
        },
    }
    atomic_json(results / "platform.json", platform)


def write_trajectory_files(results: Path, trajectories: list[dict[str, Any]]) -> None:
    atomic_json(results / "trajectories.json", trajectories)
    fields = [
        "scenario", "scenario_label", "expected_class_set", "cadence_seconds", "observer_id", "pid",
        "polls", "pre_cause_polls", "post_cause_polls", "adapter_error_polls",
        "cause_to_first_alert_seconds", "cause_to_first_exact_seconds",
        "cause_to_two_poll_exact_seconds", "effect_to_first_exact_seconds", "exact_correct",
    ]
    path = results / "trajectories.csv"
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, lineterminator="\n")
        writer.writeheader()
        for row in trajectories:
            projected = {key: row[key] for key in fields}
            projected["expected_class_set"] = "|".join(row["expected_class_set"])
            writer.writerow(projected)


def nearest_rank(values: list[float], fraction: float) -> float:
    ordered = sorted(values)
    index = math.ceil(fraction * len(ordered)) - 1
    return ordered[max(0, min(len(ordered) - 1, index))]


def write_campaign_summary(
    campaign_id: str,
    results: Path,
    summaries: list[dict[str, Any]],
    trajectories: list[dict[str, Any]],
) -> None:
    exact_latencies = [float(row["cause_to_first_exact_seconds"]) for row in trajectories]
    two_poll_exact_latencies = [
        float(row["cause_to_two_poll_exact_seconds"]) for row in trajectories
    ]
    campaign = {
        "schema": "govdrift-trace-campaign-summary/v2",
        "campaign_id": campaign_id,
        "completed_utc": utc_now(),
        "namespace": NAMESPACE,
        "deployment": DEPLOYMENT,
        "cadences_seconds": list(CADENCES),
        "scenarios": len(summaries),
        "scenario_ids": list(EXPECTED),
        "experimental_unit": "one injected scenario episode",
        "experimental_units_injected_episodes": len(summaries),
        "observational_unit": "one scenario-by-observer-cadence trajectory",
        "observational_units_trajectories": len(trajectories),
        "trajectories": len(trajectories),
        "expected_trajectories": len(EXPECTED) * len(CADENCES),
        "distinct_observer_processes_across_windows": len({(row["scenario"], row["pid"]) for row in trajectories}),
        "exact_trajectories": sum(bool(row["exact_correct"]) for row in trajectories),
        "two_poll_exact_trajectories": sum(
            row["two_poll_exact"] is not None for row in trajectories
        ),
        "scenarios_all_exact": sum(bool(row["all_exact"]) for row in summaries),
        "adapter_error_polls": sum(int(row["adapter_error_polls"]) for row in trajectories),
        "latency_aggregation_scope": "descriptive pooled across 27 correlated observational trajectories",
        "cause_to_first_exact_seconds": {
            "minimum": min(exact_latencies),
            "median": statistics.median(exact_latencies),
            "p95_nearest_rank": nearest_rank(exact_latencies, 0.95),
            "maximum": max(exact_latencies),
        },
        "cause_to_two_poll_exact_seconds": {
            "minimum": min(two_poll_exact_latencies),
            "median": statistics.median(two_poll_exact_latencies),
            "p95_nearest_rank": nearest_rank(two_poll_exact_latencies, 0.95),
            "maximum": max(two_poll_exact_latencies),
        },
        "scenario_summaries": [
            {
                "scenario": row["scenario"],
                "expected_class_set": row["expected_class_set"],
                "all_exact": row["all_exact"],
                "all_two_poll_exact": row["all_two_poll_exact"],
                "distinct_pids": row["distinct_pids"],
            }
            for row in summaries
        ],
        "interpretation": (
            "Positive trace replication with nine experimental units (injected scenario episodes). "
            "The 27 correlated audit trajectories are observational units from three cadence observers "
            "per episode, not 27 statistically independent experimental units."
        ),
    }
    atomic_json(results / "campaign_summary.json", campaign)


def write_readme(results: Path, campaign_id: str) -> None:
    text = f"""# Governance Drift positive trace campaign

Campaign `{campaign_id}` executes S1--S9 in the dedicated `{NAMESPACE}` namespace. Each
scenario is observed by three separate OS processes at 1, 5, and 10 second cadences.
Every poll is an fsync'd NDJSON record whose SHA-256 field chains to the prior poll.
Injection records contain a unique ID, source hashes, command-ledger indices, input
fingerprints, and monotonic plus UTC cause/effect timestamps. A trajectory reaches
`two_poll_exact` on its second consecutive exact expected classification.

The experimental unit reported in `campaign_summary.json` is one injected scenario
episode (n=9). A scenario-by-observer-cadence trajectory is a correlated observational
unit (9 x 3 = 27). The three processes in a scenario are independently scheduled and
have distinct PIDs/UUIDs, but they observe the same injected cause and share the host,
API server, namespace, and evidence store; they are repeated measurements, not
independent statistical replicates. Pooled latency summaries across the 27 trajectories
are explicitly descriptive.

## Verification

Run `python3 scripts/verify_trace_results.py lab/results_trace/{campaign_id}` from
the repository root.
The verifier recomputes every poll chain, injection hash, event marker, image-lock
constraint, denominator, and `manifest.sha256` entry.

The reportable derived summaries use the explicit v2 `two_poll_exact*` vocabulary.
This names the second consecutive exact poll without asserting long-horizon stability.

## Scope and exact limitations

1. This is one positive replication campaign on one local, single-control-plane Kind
   cluster (linux/arm64), not an external-validity, throughput, or scalability study.
2. Detection times are descriptive wall-clock observations. The runner and observers
   share the host monotonic clock; no distributed-clock claim is made.
3. `two_poll_exact` means two consecutive exact polls. It does not establish
   long-horizon persistence or remediation safety.
4. Flux reconciliation is explicitly requested in setup/reset and S6, so S6 timings
   include forced reconciliation rather than a natural interval distribution.
5. S4 uses a namespaced Kyverno admission mutation to emulate artifact substitution.
   It validates the materialized-image lineage path, not a real registry compromise.
6. S5 and S7 use a file-backed cloud-inventory adapter. They validate environment
   predicates and polling traces, not a live cloud provider API.
7. All workload and container base references used by the campaign are digest locked.
   The Python git-server image is locked, but Debian packages installed inside that
   ephemeral server at startup are not snapshot-pinned; this affects reproducibility
   of the transport helper, not workload identity.
8. The campaign does not test partitions, API saturation, multi-node scheduling,
   adversarial log tampering after capture, or automatic remediation.
9. The immutable proof/basis is enforced by read-only files during each scenario, not
   by an external transparency service or hardware root of trust.
10. The results demonstrate reproducible detector behavior for the declared scenarios;
    they do not estimate real-world prevalence or false-positive rates.
"""
    atomic_text(results / "README.md", text)


def write_manifest(results: Path) -> None:
    rows = []
    for path in sorted(item for item in results.rglob("*") if item.is_file()):
        if path.name == "manifest.sha256":
            continue
        rows.append(f"{sha256_file(path)}  {path.relative_to(results)}")
    atomic_text(results / "manifest.sha256", "\n".join(rows) + "\n")


def cleanup(campaign_id: str, runtime: Path) -> dict[str, Any]:
    errors = []
    namespace_raw = kubectl(
        "get", "namespace", NAMESPACE, "--ignore-not-found", "-o", "json", check=False
    ).strip()
    if namespace_raw:
        namespace_owner = json.loads(namespace_raw).get("metadata", {}).get("labels", {}).get("govdrift.io/campaign")
        if namespace_owner == campaign_id:
            try:
                kubectl("delete", "namespace", NAMESPACE, "--wait=true", "--timeout=180s")
            except Exception as exc:
                errors.append(f"namespace cleanup: {exc}")
        else:
            errors.append(f"namespace cleanup refused: owner is {namespace_owner!r}")
    container_owner = run(
        "docker", "inspect", "--format", "{{index .Config.Labels \"govdrift.campaign\"}}",
        GIT_CONTAINER, check=False,
    ).strip()
    if container_owner:
        if container_owner == campaign_id:
            try:
                run("docker", "rm", "-f", GIT_CONTAINER)
            except Exception as exc:
                errors.append(f"container cleanup: {exc}")
        else:
            errors.append(f"container cleanup refused: owner is {container_owner!r}")
    namespace_absent = not bool(kubectl("get", "namespace", NAMESPACE, "--ignore-not-found", "-o", "name", check=False).strip())
    container_absent = not bool(run(
        "docker", "ps", "-a", "--filter", f"name=^/{GIT_CONTAINER}$", "--format", "{{.Names}}", check=False
    ).strip())
    shutil.rmtree(runtime, ignore_errors=True)
    return {
        "completed_utc": utc_now(),
        "namespace": NAMESPACE,
        "namespace_absent": namespace_absent,
        "container": GIT_CONTAINER,
        "container_absent": container_absent,
        "temporary_runtime_removed": not runtime.exists(),
        "errors": errors,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--results-root", type=Path, default=RESULTS_ROOT)
    args = parser.parse_args()

    campaign_id = f"trace-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}-{uuid.uuid4().hex[:8]}"
    results_root = args.results_root.resolve()
    results_root.mkdir(parents=True, exist_ok=True)
    results = results_root / campaign_id
    results.mkdir()
    runtime = Path(tempfile.mkdtemp(prefix=f"govdrift-{campaign_id}-"))
    atomic_json(results / "campaign.json", {
        "schema": "govdrift-trace-campaign/v1",
        "campaign_id": campaign_id,
        "started_utc": utc_now(),
        "results": str(results),
        "temporary_runtime": str(runtime),
        "expected": EXPECTED,
        "cadences_seconds": list(CADENCES),
    })

    failure: Exception | None = None
    summaries: list[dict[str, Any]] = []
    trajectories: list[dict[str, Any]] = []
    try:
        state = setup(runtime, results, campaign_id)
        for scenario in EXPECTED:
            summary, rows = run_scenario(campaign_id, scenario, runtime, results, state)
            summaries.append(summary)
            trajectories.extend(rows)
        capture_artifacts(runtime, results, state)
        write_trajectory_files(results, trajectories)
        write_campaign_summary(campaign_id, results, summaries, trajectories)
        write_readme(results, campaign_id)
    except Exception as exc:
        failure = exc
        atomic_json(results / "failure.json", {
            "failed_utc": utc_now(),
            "type": type(exc).__name__,
            "message": str(exc),
        })
    finally:
        cleanup_result = cleanup(campaign_id, runtime)
        atomic_json(results / "cleanup.json", cleanup_result)
        atomic_json(results / "command_ledger.json", COMMAND_LEDGER)
        write_manifest(results)

    if failure is not None:
        raise failure
    if cleanup_result["errors"] or not cleanup_result["namespace_absent"] or not cleanup_result["container_absent"]:
        raise RuntimeError(f"campaign completed but cleanup was incomplete: {cleanup_result}")
    print(results)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
