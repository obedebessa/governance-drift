#!/usr/bin/env python3
"""Namespace-parameterized evaluator for the positive trace campaign.

This module is deliberately separate from ``lab/evaluator.py`` so that the
trace replication never reads or mutates the manuscript laboratory's
``payments`` namespace or ``lab/runtime`` evidence store.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import time
from pathlib import Path
from typing import Any


COMPONENTS = ("configuration", "policy", "authorization", "intent", "environment")
PRIORITY = ("configuration", "policy", "intent", "authorization", "environment", "evidence")


def canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")


def sha256_value(value: Any) -> str:
    return hashlib.sha256(canonical_bytes(value)).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def command(*args: str, cwd: Path | None = None) -> str:
    completed = subprocess.run(args, cwd=cwd, text=True, capture_output=True)
    if completed.returncode:
        raise RuntimeError(
            f"command failed ({completed.returncode}): {' '.join(args)}\n"
            f"stdout={completed.stdout}\nstderr={completed.stderr}"
        )
    return completed.stdout


def kubectl_json(*args: str) -> dict[str, Any]:
    return json.loads(command("kubectl", *args, "-o", "json"))


def load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise TypeError(f"expected JSON object: {path}")
    return value


def projection(obj: dict[str, Any]) -> dict[str, Any]:
    container = obj["spec"]["template"]["spec"]["containers"][0]
    return {
        "deployment_labels": {
            key: value
            for key, value in obj["metadata"].get("labels", {}).items()
            if key in {"app", "env", "team-owner"}
        },
        "pod_labels": {
            key: value
            for key, value in obj["spec"]["template"]["metadata"].get("labels", {}).items()
            if key == "app"
        },
        "container": {
            "name": container["name"],
            "image": container["image"],
            "imagePullPolicy": container.get("imagePullPolicy"),
            "env": container.get("env", []),
            "resources": container.get("resources", {}),
        },
    }


def file_map(directory: Path) -> dict[str, dict[str, Any]]:
    if not directory.exists():
        return {}
    return {
        path.name: {"bytes": path.stat().st_size, "sha256": sha256_file(path)}
        for path in sorted(directory.glob("*.json"))
    }


def load_records(directory: Path) -> list[dict[str, Any]]:
    by_id: dict[str, dict[str, Any]] = {}
    for path in sorted(directory.glob("*.json")):
        row = load_json(path)
        identifier = row.get("id")
        if not isinstance(identifier, str) or not identifier:
            raise ValueError(f"record without id: {path}")
        if identifier in by_id and by_id[identifier] != row:
            raise ValueError(f"conflicting record id: {identifier}")
        by_id[identifier] = row
    return [by_id[key] for key in sorted(by_id)]


def applicable(proof: dict[str, Any], live: dict[str, Any] | None, now: float) -> bool:
    mode = proof.get("mode", "one-shot")
    if mode == "one-shot":
        return bool(proof.get("valid_at_execution", True))
    if live is None:
        raise LookupError(f"live status missing for continuing record {proof.get('id')}")
    if bool(live.get("revoked", False)):
        return False
    if mode in {"continuing", "temporary-exception"}:
        return now < float(live.get("expires_utc", 2**63 - 1))
    raise ValueError(f"unknown authorization mode: {mode}")


def violated_equalities(approved: Any, observed: Any, prefix: str = "") -> list[str]:
    if not isinstance(approved, dict) or not isinstance(observed, dict):
        return [prefix or "$"] if approved != observed else []
    violations: list[str] = []
    for key, expected in approved.items():
        path = f"{prefix}.{key}" if prefix else key
        if key not in observed:
            violations.append(path)
        elif isinstance(expected, dict):
            violations.extend(violated_equalities(expected, observed[key], path))
        elif observed[key] != expected:
            violations.append(path)
    return violations


def normalize_image_id(image_id: str) -> str:
    return image_id.split("@", 1)[-1]


def vector_result(
    components: dict[str, str], details: dict[str, list[str]]
) -> dict[str, Any]:
    drift_set = [name for name in COMPONENTS if components[name] == "inconsistent"]
    undecidable = [name for name in COMPONENTS if components[name] == "undecidable"]
    class_set = [*drift_set, *(["evidence"] if undecidable else [])]
    first = next((name for name in PRIORITY if name in class_set), None)
    verdict = "drift" if drift_set else ("undecidable" if undecidable else "consistent")
    return {
        "verdict": verdict,
        "class": first,
        "class_set": class_set,
        "drift_set": drift_set,
        "undecidable_components": undecidable,
        "evidence_drift": bool(undecidable),
        "components": components,
        "details": details,
        "detail": "; ".join(
            message for name in COMPONENTS for message in details.get(name, [])
        ),
    }


def evaluate(
    *, runtime: Path, namespace: str, deployment: str, policy: str
) -> dict[str, Any]:
    started_mono = time.monotonic()
    components = {name: "consistent" for name in COMPONENTS}
    details: dict[str, list[str]] = {name: [] for name in COMPONENTS}
    inputs: dict[str, Any] = {
        "runtime": str(runtime),
        "namespace": namespace,
        "deployment": deployment,
        "policy": policy,
    }
    basis: dict[str, Any] | None = None
    observed: dict[str, Any] | None = None

    basis_path = runtime / "basis.json"
    try:
        basis = load_json(basis_path)
        inputs["basis"] = {
            "basis_id": basis.get("basis_id"),
            "sha256": sha256_file(basis_path),
        }
    except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
        for component in COMPONENTS:
            components[component] = "undecidable"
            details[component].append(f"admitted basis unavailable: {type(exc).__name__}: {exc}")
        result = vector_result(components, details)
        result.update({
            "schema": "govdrift-trace-evaluation/v1",
            "inputs": inputs,
            "input_fingerprint_sha256": sha256_value(inputs),
            "evaluation_started_mono": started_mono,
            "evaluation_completed_mono": time.monotonic(),
        })
        return result

    desired_path = runtime / "work/workload/deployment.json"
    try:
        desired = json.loads(command(
            "kubectl", "create", "--dry-run=client", "-f", str(desired_path), "-o", "json"
        ))
        observed = kubectl_json("-n", namespace, "get", "deployment", deployment)
        desired_projection = projection(desired)
        observed_projection = projection(observed)
        if desired_projection != observed_projection:
            components["configuration"] = "inconsistent"
            details["configuration"].append("managed deployment projection differs from Git")
        inputs["configuration"] = {
            "desired_file_sha256": sha256_file(desired_path),
            "desired_projection_sha256": sha256_value(desired_projection),
            "observed_projection_sha256": sha256_value(observed_projection),
            "deployment_uid": observed["metadata"].get("uid"),
            "resource_version": observed["metadata"].get("resourceVersion"),
            "generation": observed["metadata"].get("generation"),
        }
    except (OSError, RuntimeError, ValueError, KeyError, IndexError, json.JSONDecodeError) as exc:
        components["configuration"] = "undecidable"
        details["configuration"].append(f"configuration stream unavailable: {type(exc).__name__}: {exc}")

    try:
        live_policy = kubectl_json("-n", namespace, "get", "policy", policy)
        live_version = live_policy.get("metadata", {}).get("labels", {}).get("policy-version")
        approved_version = basis["policy_version"]
        if not live_version:
            raise ValueError("live policy has no policy-version label")
        if live_version != approved_version:
            components["policy"] = "inconsistent"
            details["policy"].append(
                f"policy {live_version} supersedes admitted {approved_version}"
            )
        inputs["policy"] = {
            "uid": live_policy["metadata"].get("uid"),
            "resource_version": live_policy["metadata"].get("resourceVersion"),
            "live_version": live_version,
            "approved_version": approved_version,
        }
        try:
            reports = kubectl_json("-n", namespace, "get", "policyreport")
            report_rows = [
                {
                    "name": item.get("metadata", {}).get("name"),
                    "uid": item.get("metadata", {}).get("uid"),
                    "resource_version": item.get("metadata", {}).get("resourceVersion"),
                    "summary": item.get("summary", {}),
                }
                for item in reports.get("items", [])
            ]
            inputs["policy"]["policy_reports_sha256"] = sha256_value(report_rows)
        except (RuntimeError, ValueError, json.JSONDecodeError):
            inputs["policy"]["policy_reports_sha256"] = None
    except (RuntimeError, ValueError, KeyError, json.JSONDecodeError) as exc:
        components["policy"] = "undecidable"
        details["policy"].append(f"policy stream unavailable: {type(exc).__name__}: {exc}")

    now = time.time()
    proof_records: list[dict[str, Any]] = [basis["approval"]]
    try:
        proof_records.extend(load_records(runtime / "proofs"))
        proof_by_id = {row["id"]: row for row in proof_records}
        live_records = load_records(runtime / "approvals")
        live_by_id = {row["id"]: row for row in live_records}
        inputs["authorization_files"] = {
            "approvals": file_map(runtime / "approvals"),
            "proofs": file_map(runtime / "proofs"),
        }
    except (OSError, ValueError, TypeError, json.JSONDecodeError, KeyError) as exc:
        proof_by_id = {}
        live_records = []
        live_by_id = {}
        components["authorization"] = "undecidable"
        components["intent"] = "undecidable"
        details["authorization"].append(f"authorization records unavailable: {type(exc).__name__}: {exc}")
        details["intent"].append(f"authorization records unavailable: {type(exc).__name__}: {exc}")

    applicable_proofs: list[dict[str, Any]] = []
    live_status_missing = False
    if proof_by_id:
        for identifier, proof in proof_by_id.items():
            live = live_by_id.get(identifier)
            try:
                if applicable(proof, live, now):
                    applicable_proofs.append(proof)
            except LookupError as exc:
                live_status_missing = True
                details["authorization"].append(str(exc))
                details["intent"].append(str(exc))
            if live is not None:
                identity_fields = ("subject", "subjects", "unit_ref")
                mismatched = [field for field in identity_fields if live.get(field) != proof.get(field)]
                if mismatched:
                    components["authorization"] = "inconsistent"
                    details["authorization"].append(
                        f"live authorization {identifier} mismatches immutable proof fields: "
                        + ", ".join(mismatched)
                    )
        if live_status_missing:
            components["authorization"] = "undecidable"
            components["intent"] = "undecidable"
        elif not applicable_proofs and components["authorization"] == "consistent":
            components["authorization"] = "inconsistent"
            details["authorization"].append("no applicable authorization remains")
    else:
        components["authorization"] = "undecidable"
        components["intent"] = "undecidable"
        details["authorization"].append("immutable authorization proof unavailable")
        details["intent"].append("immutable authorization proof unavailable")

    try:
        revision = command("git", "rev-parse", "HEAD", cwd=runtime / "work").strip()
        inputs["git_revision"] = revision
        if not live_status_missing and applicable_proofs and not any(
            revision in proof.get("revisions", []) for proof in applicable_proofs
        ):
            components["intent"] = "inconsistent"
            details["intent"].append(f"revision {revision[:12]} is not approved")
    except (RuntimeError, OSError) as exc:
        components["intent"] = "undecidable"
        details["intent"].append(f"intent stream unavailable: {type(exc).__name__}: {exc}")

    if observed is not None:
        annotations = observed.get("spec", {}).get("template", {}).get("metadata", {}).get("annotations", {})
        for live in live_records:
            if live.get("mode") != "temporary-exception":
                continue
            if now < float(live.get("expires_utc", 2**63 - 1)) or live.get("removed"):
                continue
            identifier = live.get("id")
            if annotations.get("emergency-debug") == identifier:
                components["authorization"] = "inconsistent"
                details["authorization"].append(
                    f"expired exception {identifier} still covers an active effect"
                )

    try:
        pods = kubectl_json("-n", namespace, "get", "pod", "-l", f"app={deployment}")
        active_pods = []
        image_ids: list[str] = []
        for pod in pods.get("items", []):
            if pod.get("metadata", {}).get("deletionTimestamp"):
                continue
            statuses = [
                *pod.get("status", {}).get("initContainerStatuses", []),
                *pod.get("status", {}).get("containerStatuses", []),
                *pod.get("status", {}).get("ephemeralContainerStatuses", []),
            ]
            if not statuses or any(not status.get("imageID") for status in statuses):
                raise RuntimeError("active pod lacks a complete materialized imageID set")
            if any(not status.get("ready", True) for status in pod.get("status", {}).get("containerStatuses", [])):
                raise RuntimeError("active pod has a non-ready ordinary container")
            ids = [normalize_image_id(str(status["imageID"])) for status in statuses]
            image_ids.extend(ids)
            active_pods.append({
                "name": pod.get("metadata", {}).get("name"),
                "uid": pod.get("metadata", {}).get("uid"),
                "resource_version": pod.get("metadata", {}).get("resourceVersion"),
                "phase": pod.get("status", {}).get("phase"),
                "image_ids": sorted(ids),
            })
        if not active_pods:
            raise RuntimeError("no active pod found")
        approved_subjects = {
            subject
            for proof in applicable_proofs
            for subject in proof.get("subjects", [])
        }
        uncovered = sorted(set(image_ids) - approved_subjects)
        # Missing continuing-status evidence is epistemic: a materialized
        # digest cannot be declared unauthorized merely because the live
        # applicability record is unavailable.  Preserve ``undecidable`` in
        # that case instead of silently converting S9 into a substantive
        # authorization alarm.
        if uncovered and components["authorization"] != "undecidable":
            components["authorization"] = "inconsistent"
            details["authorization"].append(
                "running digest set contains uncovered members: " + ", ".join(uncovered)
            )
        inputs["pods"] = sorted(active_pods, key=lambda row: str(row["name"]))
    except (RuntimeError, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        components["authorization"] = "undecidable"
        details["authorization"].append(f"artifact lineage unavailable: {type(exc).__name__}: {exc}")

    inventory_path = runtime / "cloud-inventory.json"
    try:
        inventory = load_json(inventory_path)
        violations = violated_equalities(basis["inventory"], inventory)
        if violations:
            components["environment"] = "inconsistent"
            details["environment"].append(
                "declared environment predicates violated: " + ", ".join(violations)
            )
        inputs["environment"] = {
            "inventory_sha256": sha256_file(inventory_path),
            "violations": violations,
        }
    except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError) as exc:
        components["environment"] = "undecidable"
        details["environment"].append(f"environment stream unavailable: {type(exc).__name__}: {exc}")

    result = vector_result(components, details)
    result.update({
        "schema": "govdrift-trace-evaluation/v1",
        "inputs": inputs,
        "input_fingerprint_sha256": sha256_value(inputs),
        "evaluation_started_mono": started_mono,
        "evaluation_completed_mono": time.monotonic(),
    })
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--runtime", type=Path, required=True)
    parser.add_argument("--namespace", required=True)
    parser.add_argument("--deployment", required=True)
    parser.add_argument("--policy", required=True)
    args = parser.parse_args()
    print(json.dumps(evaluate(
        runtime=args.runtime.resolve(),
        namespace=args.namespace,
        deployment=args.deployment,
        policy=args.policy,
    ), sort_keys=True, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
