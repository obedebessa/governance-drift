#!/usr/bin/env python3
"""Observe benign controls before, during, and after their state transitions."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import random
import shutil
import subprocess
import sys
import tempfile
import time
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

import run_experiment as base


LAB = Path(__file__).resolve().parent
RUNTIME = LAB / "runtime"
RESULTS = LAB / "results_transition"
CADENCES = (0.5, 2.0, 10.0)
SEED = 20260808
CONTROLS = (
    ("C1", "satisfied policy revision"),
    ("C2", "approved rollback"),
    ("C3", "exception retired before expiry"),
    ("C4", "approved artifact retag materialization"),
    ("C5", "autoscaling replica change"),
    ("C6", "legitimate rollout restart"),
)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def phase(window: int, control: str, cadence: float) -> float:
    return random.Random(f"{SEED}:{window}:{control}:{cadence}").random() * cadence


def digest(reference: str) -> str:
    raw = base.run(
        "docker", "buildx", "imagetools", "inspect", reference,
        "--format", "{{json .Manifest.Digest}}",
    ).strip()
    return json.loads(raw)


def write_authorization(identifier: str, *, revisions: list[str], subjects: list[str]) -> None:
    row = {
        "id": identifier,
        "kind": "deployment-authorization",
        "mode": "continuing",
        "valid_at_execution": True,
        "subject": "deployment/payments",
        "unit_ref": {
            "cluster": "kind-govdrift-lab",
            "namespace": "payments",
            "kind": "Deployment",
            "name": "payments",
        },
        "revisions": sorted(set(revisions)),
        "subjects": sorted(set(subjects)),
        "revoked": False,
        "revocation_effect": "prospective",
    }
    for directory in (RUNTIME / "approvals", RUNTIME / "proofs"):
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / f"{identifier}.json"
        path.write_text(json.dumps(row, indent=2) + "\n")
        if directory.name == "proofs":
            path.chmod(0o444)


def prepare_approved_rollback(window: int) -> tuple[str, str]:
    baseline = (RUNTIME / "baseline_revision").read_text().strip()
    temp = Path(tempfile.mkdtemp(prefix="govdrift-c2-"))
    try:
        base.run("git", "worktree", "add", "--detach", str(temp), baseline, cwd=RUNTIME / "work")
        shutil.copy2(LAB / "manifests/deployment-predecessor.yaml", temp / "payments/deployment.yaml")
        base.run("git", "add", "payments/deployment.yaml", cwd=temp)
        env = dict(os.environ)
        env.update({
            "GIT_AUTHOR_NAME": "Governance Drift Lab",
            "GIT_AUTHOR_EMAIL": "lab@example.invalid",
            "GIT_COMMITTER_NAME": "Governance Drift Lab",
            "GIT_COMMITTER_EMAIL": "lab@example.invalid",
            "GIT_AUTHOR_DATE": f"2026-08-08T12:{window:02d}:00Z",
            "GIT_COMMITTER_DATE": f"2026-08-08T12:{window:02d}:00Z",
        })
        base.run("git", "commit", "-m", f"control C2 approved rollback window {window}", cwd=temp, env=env)
        revision = base.run("git", "rev-parse", "HEAD", cwd=temp).strip()
    finally:
        # The macOS system Git in the archived environment predates
        # ``git worktree remove``.  Delete only the freshly allocated temp
        # worktree, then prune its administrative record.
        shutil.rmtree(temp, ignore_errors=True)
        base.run("git", "worktree", "prune", cwd=RUNTIME / "work")
    alternate = digest("localhost:5001/governance-demo:alternate")
    write_authorization(f"APR-C2-{window}", revisions=[revision], subjects=[alternate])
    return revision, alternate


def prepare_control(window: int, control: str) -> dict:
    if control == "C2":
        revision, alternate = prepare_approved_rollback(window)
        return {"revision": revision, "digest": alternate}
    if control == "C3":
        env = dict(os.environ)
        env["EXPIRY_SECONDS"] = "8"
        base.run("bash", str(LAB / "scenarios/s2.sh"), env=env)
        exception = json.loads((RUNTIME / "approvals/EXC-1.json").read_text())
        return {"expires_utc": float(exception["expires_utc"])}
    if control == "C4":
        base.run(
            "docker", "tag", "localhost:5001/governance-demo:alternate",
            "localhost:5001/governance-demo:1.0",
        )
        base.run("docker", "push", "localhost:5001/governance-demo:1.0")
        alternate = digest("localhost:5001/governance-demo:1.0")
        revision = base.run("git", "rev-parse", "HEAD", cwd=RUNTIME / "work").strip()
        write_authorization(f"APR-C4-{window}", revisions=[revision], subjects=[alternate])
        return {"digest": alternate, "revision": revision}
    return {}


def apply_control(control: str, prepared: dict) -> None:
    if control == "C1":
        base.kubectl("apply", "-f", str(LAB / "policies/kyverno-policy-v7b.yaml"))
    elif control == "C2":
        manifest = RUNTIME / "work/payments/deployment.yaml"
        with tempfile.NamedTemporaryFile(dir=manifest.parent, delete=False) as handle:
            replacement = Path(handle.name)
        try:
            shutil.copyfile(LAB / "manifests/deployment-predecessor.yaml", replacement)
            os.replace(replacement, manifest)
        finally:
            replacement.unlink(missing_ok=True)
        old_revision = base.run("git", "rev-parse", "HEAD", cwd=RUNTIME / "work").strip()
        base.run(
            "git", "update-ref", "refs/heads/master", prepared["revision"], old_revision,
            cwd=RUNTIME / "work",
        )
        base.run(
            "git", "push", "--force", "origin", f"{prepared['revision']}:master",
            cwd=RUNTIME / "work",
        )
        base.trigger_flux()
        base.wait_rollout()
    elif control == "C3":
        delay = max(0.0, float(prepared["expires_utc"]) - time.time() - 0.75)
        if delay:
            time.sleep(delay)
        base.kubectl(
            "-n", "payments", "patch", "deployment", "payments", "--type=json",
            "-p", '[{"op":"remove","path":"/spec/template/metadata/annotations/emergency-debug"}]',
        )
        path = RUNTIME / "approvals/EXC-1.json"
        row = json.loads(path.read_text())
        row["removed"] = True
        row["removed_utc"] = time.time()
        path.write_text(json.dumps(row, indent=2) + "\n")
        base.wait_rollout()
    elif control == "C4":
        base.kubectl("-n", "payments", "rollout", "restart", "deployment/payments")
        base.wait_rollout()
    elif control == "C5":
        base.kubectl("-n", "payments", "scale", "deployment/payments", "--replicas=2")
        base.wait_rollout()
    elif control == "C6":
        base.kubectl("-n", "payments", "rollout", "restart", "deployment/payments")
        base.wait_rollout()
    else:
        raise ValueError(control)
    base.wait_consistent(timeout=120)


def load_ndjson(paths: list[Path]) -> list[dict]:
    rows: list[dict] = []
    for path in paths:
        for line in path.read_text().splitlines():
            if line.strip():
                rows.append(json.loads(line))
    return rows


def write_csv(path: Path, rows: list[dict]) -> None:
    if not rows:
        return
    fields: list[str] = []
    projected = []
    for row in rows:
        item = dict(row)
        for key in ("class_set", "undecidable_components"):
            item[key] = "|".join(item.get(key, []))
        for key in ("components",):
            item[key] = json.dumps(item.get(key, {}), sort_keys=True, separators=(",", ":"))
        for key in item:
            if key not in fields:
                fields.append(key)
        projected.append(item)
    with path.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, lineterminator="\n")
        writer.writeheader()
        writer.writerows(projected)


def summarize(rows: list[dict], windows: list[dict]) -> dict:
    by_window: dict[tuple[int, str, float], list[dict]] = defaultdict(list)
    for row in rows:
        by_window[(int(row["window"]), row["control"], float(row["cadence_seconds"]))].append(row)
    records = []
    for window in windows:
        for cadence in CADENCES:
            subset = by_window[(window["window"], window["control"], cadence)]
            after_start = [r for r in subset if r["completed_mono"] >= window["action_started_mono"]]
            during = [r for r in after_start if r["actual_start_mono"] <= window["action_completed_mono"]]
            governance = [
                r for r in after_start
                if r["verdict"] == "drift"
                and any(c in {"policy", "authorization", "intent", "environment"}
                        for c in r.get("class_set", []))
            ]
            configuration = [
                r for r in after_start if "configuration" in r.get("class_set", [])
            ]
            epistemic = [r for r in after_start if r["verdict"] == "undecidable"]
            records.append({
                "window": window["window"],
                "control": window["control"],
                "cadence_seconds": cadence,
                "polls_total": len(subset),
                "polls_transition_and_post": len(after_start),
                "polls_during_action": len(during),
                "substantive_governance_alarm_polls": len(governance),
                "configuration_convergence_polls": len(configuration),
                "epistemic_warning_polls": len(epistemic),
                "max_scheduler_lag_seconds": max((r["scheduler_lag_seconds"] for r in subset), default=0.0),
                "p95_evaluation_seconds": sorted(r["evaluation_seconds"] for r in subset)[max(0, int(0.95 * len(subset)) - 1)] if subset else 0.0,
            })
    return {
        "design": "transition-inclusive benign controls with three isolated observer processes",
        "seed": SEED,
        "cadences_seconds": list(CADENCES),
        "windows": len(windows),
        "polls": len(rows),
        "substantive_governance_alarm_polls": sum(
            r["substantive_governance_alarm_polls"] for r in records
        ),
        "configuration_convergence_polls": sum(r["configuration_convergence_polls"] for r in records),
        "epistemic_warning_polls": sum(r["epistemic_warning_polls"] for r in records),
        "records": records,
        "windows_metadata": windows,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--windows", type=int, default=20)
    parser.add_argument("--post-seconds", type=float, default=5.0)
    parser.add_argument("--output", type=Path, default=RESULTS)
    args = parser.parse_args()
    if not (RUNTIME / "baseline_revision").exists():
        raise SystemExit("run lab/bootstrap.sh first")
    args.output.mkdir(parents=True, exist_ok=True)
    raw_dir = args.output / "raw"
    shutil.rmtree(raw_dir, ignore_errors=True)
    raw_dir.mkdir()
    campaign = f"transition-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}"
    windows: list[dict] = []
    raw_paths: list[Path] = []
    for index in range(1, args.windows + 1):
        control, name = CONTROLS[(index - 1) % len(CONTROLS)]
        print(f"[{index}/{args.windows} {control}] reset", flush=True)
        base.reset_baseline()
        prepared = prepare_control(index, control)
        run_dir = raw_dir / f"w{index:02d}-{control}"
        run_dir.mkdir()
        stop_file = run_dir / "STOP"
        start_mono = time.monotonic() + 1.0
        processes = []
        for cadence in CADENCES:
            output = run_dir / f"cadence-{cadence:g}.ndjson"
            ready = run_dir / f"cadence-{cadence:g}.ready"
            raw_paths.append(output)
            processes.append(subprocess.Popen([
                sys.executable, str(LAB / "transition_observer.py"),
                "--campaign", campaign,
                "--window", str(index),
                "--control", control,
                "--cadence", str(cadence),
                "--phase", str(phase(index, control, cadence)),
                "--start-mono", str(start_mono),
                "--stop-file", str(stop_file),
                "--ready-file", str(ready),
                "--output", str(output),
            ]))
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            if len(list(run_dir.glob("*.ready"))) == len(CADENCES):
                break
            time.sleep(0.05)
        else:
            raise RuntimeError(f"observers did not become ready for {control}")
        if time.monotonic() < start_mono:
            time.sleep(start_mono - time.monotonic())
        action_started = time.monotonic()
        action_started_utc = utc_now()
        print(f"[{index}/{args.windows} {control}] mutate under observation", flush=True)
        error = ""
        try:
            apply_control(control, prepared)
        except Exception as exc:
            error = f"{type(exc).__name__}: {exc}"
        action_completed = time.monotonic()
        action_completed_utc = utc_now()
        time.sleep(args.post_seconds)
        stop_file.write_text(utc_now() + "\n")
        for process in processes:
            process.wait(timeout=30)
            if process.returncode:
                raise RuntimeError(f"observer exited {process.returncode}")
        windows.append({
            "window": index,
            "control": control,
            "control_name": name,
            "prepared": prepared,
            "action_started_mono": action_started,
            "action_completed_mono": action_completed,
            "action_started_utc": action_started_utc,
            "action_completed_utc": action_completed_utc,
            "action_seconds": action_completed - action_started,
            "post_seconds": args.post_seconds,
            "error": error,
        })
        if error:
            raise RuntimeError(f"{control} failed: {error}")
    rows = load_ndjson(raw_paths)
    rows.sort(key=lambda r: (r["window"], r["cadence_seconds"], r["sequence"]))
    summary = summarize(rows, windows)
    (args.output / "transition_observations.json").write_text(json.dumps({
        "campaign": campaign,
        "completed_utc": utc_now(),
        "platform": base.platform_snapshot(),
        "raw_sha256": {
            str(path.relative_to(args.output)): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in raw_paths
        },
        "rows": rows,
        "summary": summary,
    }, indent=2) + "\n")
    write_csv(args.output / "transition_observations.csv", rows)
    (args.output / "transition_summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    print(json.dumps({key: summary[key] for key in (
        "windows", "polls", "substantive_governance_alarm_polls",
        "configuration_convergence_polls", "epistemic_warning_polls",
    )}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
