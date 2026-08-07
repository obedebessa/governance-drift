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


def result(verdict: str, drift_class: str | None = None, detail: str = "") -> dict:
    return {"verdict": verdict, "class": drift_class, "detail": detail}


def evaluate(tier: str) -> dict:
    rank = TIER_RANK[tier]
    desired = kubectl_json(
        "create", "--dry-run=client", "-f",
        str(RUNTIME / "work/payments/deployment.yaml"),
    )
    observed = kubectl_json("-n", "payments", "get", "deployment", "payments")
    if projection(desired) != projection(observed):
        return result("drift", "configuration", "managed deployment projection differs from Git")
    if rank == 0:
        return result("consistent")

    proc = subprocess.run(
        ["kubectl", "get", "policyreport", "-A", "-o", "json"],
        text=True, capture_output=True,
    )
    if proc.returncode == 0:
        for item in json.loads(proc.stdout).get("items", []):
            scope = item.get("scope", {})
            if scope.get("kind") != "Deployment" or scope.get("name") != "payments":
                continue
            for row in item.get("results", []):
                if row.get("policy") == "governance-baseline" and row.get("result") == "fail":
                    return result("drift", "policy", row.get("rule", "policy failure"))
    if rank == 1:
        return result("consistent")

    now = int(time.time())
    for path in sorted((RUNTIME / "approvals").glob("EXC-*.json")):
        exception = json.loads(path.read_text())
        if now >= int(exception["expires_utc"]) and not exception.get("removed"):
            return result("drift", "authorization", f"expired exception {exception['id']}")
    current_approvals = approvals()
    if not current_approvals:
        return result("undecidable", "evidence", "approval basis unavailable")
    revision = command("git", "rev-parse", "HEAD", cwd=RUNTIME / "work").strip()
    if not any(revision in approval.get("revisions", []) for approval in current_approvals):
        return result("drift", "intent", f"revision {revision[:12]} is not approved")
    if rank == 2:
        return result("consistent")

    pods = kubectl_json("-n", "payments", "get", "pod", "-l", "app=payments")
    image_id = pods["items"][0]["status"]["containerStatuses"][0]["imageID"]
    digest = image_id.split("@", 1)[-1]
    if not any(digest in approval.get("subjects", []) for approval in current_approvals):
        return result("drift", "authorization", f"running digest {digest} is not approved")
    if rank == 3:
        return result("consistent")

    gapp = Path((RUNTIME / "gapp_latest").read_text().strip())
    current_inventory = json.loads((RUNTIME / "cloud-inventory.json").read_text())
    approved_inventory = json.loads((gapp / "sigma0.json").read_text())
    if current_inventory != approved_inventory:
        return result("drift", "environment", "declared environment assumptions changed")
    return result("consistent")


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
