#!/usr/bin/env python3
"""Tier-restricted evaluator over the live laboratory components."""

from __future__ import annotations

import argparse
import json
import subprocess
import time
from pathlib import Path


LAB = Path(__file__).resolve().parent
RUNTIME = LAB / "runtime"
TIER_RANK = {"T0n": 0, "T1": 1, "T2": 2, "T3": 3, "T4": 4}
COMPONENTS = ("configuration", "policy", "authorization", "intent", "environment")
PRIORITY = ("configuration", "policy", "intent", "authorization", "environment", "evidence")


def command(*args: str, cwd: Path | None = None, check: bool = True) -> str:
    proc = subprocess.run(args, cwd=cwd, text=True, capture_output=True)
    if check and proc.returncode:
        raise RuntimeError(f"command failed ({proc.returncode}): {' '.join(args)}\n{proc.stderr}")
    return proc.stdout


def kubectl_json(*args: str) -> dict:
    return json.loads(command("kubectl", *args, "-o", "json"))


def projection(obj: dict) -> dict:
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


def approvals() -> list[dict]:
    return [
        json.loads(path.read_text())
        for path in sorted((RUNTIME / "approvals").glob("APR-*.json"))
    ]


def approval_proofs() -> list[dict]:
    gapp = Path((RUNTIME / "gapp_latest").read_text().strip())
    return [
        json.loads(path.read_text())
        for path in sorted((gapp / "approvals").glob("APR-*.json"))
    ]


def applicable(proof: dict, now: int, live: dict | None) -> bool:
    """Apply the paper's one-shot/continuing/temporary authorization modes."""
    mode = proof.get("mode", "one-shot")
    record = live or proof
    revoked = bool(record.get("revoked", False))
    revocation_effect = record.get(
        "revocation_effect", proof.get("revocation_effect", "prospective")
    )
    if revoked and revocation_effect == "retroactive":
        return False
    if mode == "one-shot":
        return bool(proof.get("valid_at_execution", True))
    if live is None:
        raise LookupError("authenticated live authorization status unavailable")
    if revoked:
        return False
    if mode in {"continuing", "temporary-exception"}:
        return now < int(record.get("expires_utc", 2**63 - 1))
    raise ValueError(f"unknown authorization mode: {mode}")


def violated_environment_equalities(approved: dict, observed: dict, prefix: str = "") -> list[str]:
    """Instantiate sigma* as equality predicates over only recorded fields."""
    violations: list[str] = []
    for key, expected in approved.items():
        path = f"{prefix}.{key}" if prefix else key
        if key not in observed:
            violations.append(path)
        elif isinstance(expected, dict) and isinstance(observed[key], dict):
            violations.extend(violated_environment_equalities(expected, observed[key], path))
        elif observed[key] != expected:
            violations.append(path)
    return violations


def validate_evidence_envelope(
    envelope: dict,
    *,
    now: float,
    expected_subject: str,
    last_sequence: int,
    max_age_seconds: float,
    max_transport_delay_seconds: float,
) -> tuple[bool, str]:
    """Validate temporal and ordering metadata at an evidence-adapter boundary.

    The live laboratory's Kubernetes and file-backed readers are synchronous,
    but production adapters must not silently treat a syntactically valid old
    or reordered observation as current evidence.  This dependency-free
    contract is exercised by deterministic fault tests and is intended for
    adapters that wrap their payloads with authenticated transport metadata.
    """
    try:
        subject = str(envelope["subject"])
        sequence = int(envelope["sequence"])
        captured_at = float(envelope["captured_at"])
        delivered_at = float(envelope["delivered_at"])
    except (KeyError, TypeError, ValueError) as exc:
        return False, f"malformed evidence envelope: {exc}"
    if subject != expected_subject:
        return False, "evidence subject mismatch"
    if delivered_at > now:
        return False, "evidence not yet available"
    if captured_at > delivered_at:
        return False, "evidence timestamps are inverted"
    if now - captured_at > max_age_seconds:
        return False, "stale evidence"
    if delivered_at - captured_at > max_transport_delay_seconds:
        return False, "evidence transport delay exceeded"
    if sequence <= last_sequence:
        return False, "duplicate or reordered evidence"
    return True, "accepted"


def vector_result(components: dict[str, str], details: dict[str, list[str]]) -> dict:
    """Return the full substantive vector plus its epistemic mask.

    Evidence drift is not a sixth substantive divergence. It is emitted when
    one or more substantive components are undecidable from the available
    streams. ``class`` remains the priority-ordered operational interface.
    """
    drift_set = [name for name in COMPONENTS if components[name] == "inconsistent"]
    undecidable = [name for name in COMPONENTS if components[name] == "undecidable"]
    class_set = [*drift_set, *(["evidence"] if undecidable else [])]
    first = next((name for name in PRIORITY if name in class_set), None)
    verdict = "drift" if drift_set else ("undecidable" if undecidable else "consistent")
    detail = "; ".join(
        message for name in COMPONENTS for message in details.get(name, [])
    )
    return {
        "verdict": verdict,
        "class": first,
        "class_set": class_set,
        "drift_set": drift_set,
        "undecidable_components": undecidable,
        "evidence_drift": bool(undecidable),
        "components": components,
        "detail": detail,
    }


def evaluate(tier: str) -> dict:
    rank = TIER_RANK[tier]
    components = {name: "not_evaluated" for name in COMPONENTS}
    details: dict[str, list[str]] = {name: [] for name in COMPONENTS}
    try:
        desired = kubectl_json(
            "create", "--dry-run=client", "-f",
            str(RUNTIME / "work/payments/deployment.yaml"),
        )
        observed = kubectl_json("-n", "payments", "get", "deployment", "payments")
        components["configuration"] = "consistent"
        if projection(desired) != projection(observed):
            components["configuration"] = "inconsistent"
            details["configuration"].append("managed deployment projection differs from Git")
    except (RuntimeError, OSError, KeyError, IndexError, json.JSONDecodeError) as exc:
        components["configuration"] = "undecidable"
        details["configuration"].append(f"configuration stream unavailable: {exc}")
    if rank == 0:
        return vector_result(components, details)

    proc = subprocess.run(
        ["kubectl", "get", "policyreport", "-A", "-o", "json"],
        text=True, capture_output=True,
    )
    components["policy"] = "consistent"
    if proc.returncode != 0:
        components["policy"] = "undecidable"
        details["policy"].append("PolicyReport stream unavailable")
    else:
        try:
            target_report_seen = False
            for item in json.loads(proc.stdout).get("items", []):
                scope = item.get("scope", {})
                if scope.get("kind") != "Deployment" or scope.get("name") != "payments":
                    continue
                for row in item.get("results", []):
                    if row.get("policy") != "governance-baseline":
                        continue
                    target_report_seen = True
                    if row.get("result") == "fail":
                        components["policy"] = "inconsistent"
                        details["policy"].append(row.get("rule", "policy failure"))
            if not target_report_seen:
                components["policy"] = "undecidable"
                details["policy"].append("target PolicyReport not yet available")
        except (TypeError, KeyError, json.JSONDecodeError) as exc:
            components["policy"] = "undecidable"
            details["policy"].append(f"PolicyReport stream malformed: {exc}")
    if rank == 1:
        return vector_result(components, details)

    now = int(time.time())
    components["authorization"] = "consistent"
    components["intent"] = "consistent"
    for path in sorted((RUNTIME / "approvals").glob("EXC-*.json")):
        exception = json.loads(path.read_text())
        if now >= int(exception["expires_utc"]) and not exception.get("removed"):
            components["authorization"] = "inconsistent"
            details["authorization"].append(f"expired exception {exception['id']}")
    try:
        proof_records = approval_proofs()
        live_records = approvals()
        live_by_id = {row.get("id"): row for row in live_records}
        current_approvals = [
            proof for proof in proof_records
            if applicable(proof, now, live_by_id.get(proof.get("id")))
        ]
    except (OSError, json.JSONDecodeError, TypeError, ValueError, LookupError) as exc:
        proof_records = []
        approval_records = []
        current_approvals = []
        details["authorization"].append(f"approval stream unavailable: {exc}")
        details["intent"].append(f"approval stream unavailable: {exc}")
    else:
        approval_records = proof_records
    if not approval_records:
        components["authorization"] = "undecidable"
        components["intent"] = "undecidable"
        if not details["authorization"]:
            details["authorization"].append("approval basis unavailable")
        if not details["intent"]:
            details["intent"].append("approval basis unavailable")
    else:
        if not current_approvals:
            components["authorization"] = "inconsistent"
            details["authorization"].append("no applicable authorization remains")
        try:
            revision = command("git", "rev-parse", "HEAD", cwd=RUNTIME / "work").strip()
            if not any(revision in approval.get("revisions", []) for approval in current_approvals):
                components["intent"] = "inconsistent"
                details["intent"].append(f"revision {revision[:12]} is not approved")
        except (RuntimeError, OSError) as exc:
            components["intent"] = "undecidable"
            details["intent"].append(f"intent stream unavailable: {exc}")
    if rank == 2:
        return vector_result(components, details)

    if current_approvals:
        try:
            pods = kubectl_json("-n", "payments", "get", "pod", "-l", "app=payments")
            candidates = []
            for pod in pods.get("items", []):
                if pod.get("metadata", {}).get("deletionTimestamp"):
                    continue
                statuses = pod.get("status", {}).get("containerStatuses", [])
                if not statuses or not statuses[0].get("ready") or not statuses[0].get("imageID"):
                    continue
                candidates.append(statuses[0]["imageID"])
            if not candidates:
                raise RuntimeError("no active ready pod has a materialized imageID")
            image_id = sorted(candidates)[0]
            digest = image_id.split("@", 1)[-1]
            if not any(digest in approval.get("subjects", []) for approval in current_approvals):
                components["authorization"] = "inconsistent"
                details["authorization"].append(f"running digest {digest} is not approved")
        except (RuntimeError, KeyError, IndexError, json.JSONDecodeError) as exc:
            components["authorization"] = "undecidable"
            details["authorization"].append(f"artifact lineage unavailable: {exc}")
    if rank == 3:
        return vector_result(components, details)

    components["environment"] = "consistent"
    try:
        gapp = Path((RUNTIME / "gapp_latest").read_text().strip())
        current_inventory = json.loads((RUNTIME / "cloud-inventory.json").read_text())
        approved_inventory = json.loads((gapp / "sigma0.json").read_text())
        violations = violated_environment_equalities(approved_inventory, current_inventory)
        if violations:
            components["environment"] = "inconsistent"
            details["environment"].append(
                "declared environment predicates violated: " + ", ".join(violations)
            )
    except (OSError, json.JSONDecodeError, KeyError) as exc:
        components["environment"] = "undecidable"
        details["environment"].append(f"environment inventory unavailable: {exc}")
    return vector_result(components, details)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--tier", choices=TIER_RANK, default="T4")
    parser.add_argument("--plain", action="store_true")
    args = parser.parse_args()
    observed = evaluate(args.tier)
    if args.plain:
        if observed["verdict"] == "consistent":
            return 0
        print(observed["class"])
    else:
        print(json.dumps(observed, sort_keys=True))
    return 3 if observed["verdict"] == "undecidable" else (2 if observed["verdict"] == "drift" else 0)


if __name__ == "__main__":
    raise SystemExit(main())
