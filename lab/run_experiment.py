#!/usr/bin/env python3
"""Execute one bounded pass over the nine live laboratory scenarios."""

from __future__ import annotations

import csv
import json
import os
import shutil
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path

from evaluator import evaluate


LAB = Path(__file__).resolve().parent
RUNTIME = LAB / "runtime"
RESULTS = LAB / "results"
SCENARIOS = [
    ("S1", "manual in-cluster change", "configuration", "T0n"),
    ("S2", "expired exception", "authorization", "T2"),
    ("S3", "policy supersession", "policy", "T1"),
    ("S4", "artifact substitution", "authorization", "T3"),
    ("S5", "IAM expansion", "environment", "T4"),
    ("S6", "unapproved Git rollback", "intent", "T2"),
    ("S7", "out-of-band LB change", "environment", "T4"),
    ("S8", "approval subject mismatch", "authorization", "T3"),
    ("S9", "approval-record deletion", "evidence", "T2"),
]


def run(*args: str, cwd: Path | None = None, env: dict[str, str] | None = None) -> str:
    proc = subprocess.run(args, cwd=cwd, env=env, text=True, capture_output=True)
    if proc.returncode:
        raise RuntimeError(f"command failed ({proc.returncode}): {' '.join(args)}\n{proc.stdout}\n{proc.stderr}")
    return proc.stdout


def kubectl(*args: str) -> str:
    return run("kubectl", *args)


def ensure_git_server() -> None:
    """Repair a stale macOS bind mount and wait for the local Git CGI."""
    url = "http://govdrift-git:8000/cgi-bin/git/remote.git/info/refs?service=git-upload-pack"
    checks = (
        "docker", "exec", "govdrift-lab-control-plane", "curl", "-fsS", url,
    )
    probe = subprocess.run(checks, text=True, capture_output=True)
    script = subprocess.run(
        ["docker", "exec", "govdrift-git", "test", "-x", "/srv/cgi-bin/git"],
        text=True, capture_output=True,
    )
    if probe.returncode == 0 and script.returncode == 0:
        return
    run("docker", "restart", "govdrift-git")
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        if subprocess.run(checks, text=True, capture_output=True).returncode == 0:
            return
        time.sleep(1)
    raise RuntimeError("local Git CGI did not become ready after restart")


def trigger_flux() -> None:
    ensure_git_server()
    requested = str(time.time_ns())
    kubectl(
        "-n", "flux-system", "annotate", "gitrepository", "lab",
        f"reconcile.fluxcd.io/requestedAt={requested}", "--overwrite",
    )
    kubectl(
        "-n", "flux-system", "annotate", "kustomization", "lab",
        f"reconcile.fluxcd.io/requestedAt={requested}", "--overwrite",
    )
    deadline = time.monotonic() + 120
    revision = run("git", "rev-parse", "HEAD", cwd=RUNTIME / "work").strip()
    while time.monotonic() < deadline:
        applied = kubectl(
            "-n", "flux-system", "get", "kustomization", "lab",
            "-o", "jsonpath={.status.lastAppliedRevision}",
        )
        if revision in applied:
            return
        time.sleep(1)
    raise RuntimeError(f"Flux did not apply revision {revision[:12]}")


def wait_rollout() -> None:
    kubectl("-n", "payments", "rollout", "status", "deployment/payments", "--timeout=180s")


def wait_consistent(timeout: float = 90) -> None:
    deadline = time.monotonic() + timeout
    last = None
    while time.monotonic() < deadline:
        try:
            last = evaluate("T4")
        except (RuntimeError, KeyError, IndexError):
            last = None
        if last and last["verdict"] == "consistent":
            return
        time.sleep(1)
    raise RuntimeError(f"baseline did not become consistent: {last}")


def reset_baseline() -> None:
    baseline = (RUNTIME / "baseline_revision").read_text().strip()
    run("git", "reset", "--hard", baseline, cwd=RUNTIME / "work")
    run("git", "push", "--force", "origin", "HEAD:master", cwd=RUNTIME / "work")
    shutil.rmtree(RUNTIME / "approvals", ignore_errors=True)
    (RUNTIME / "approvals").mkdir()
    shutil.copy2(RUNTIME / "APR-1.baseline.json", RUNTIME / "approvals/APR-1.json")
    shutil.copy2(RUNTIME / "cloud-inventory.baseline.json", RUNTIME / "cloud-inventory.json")
    shutil.rmtree(RUNTIME / "_deleted", ignore_errors=True)
    kubectl("apply", "-f", str(LAB / "policies/kyverno-policy-v7.yaml"))
    run("docker", "tag", "nginx:1.27-alpine", "localhost:5001/governance-demo:1.0")
    run("docker", "push", "localhost:5001/governance-demo:1.0")
    trigger_flux()
    wait_rollout()
    kubectl("-n", "payments", "rollout", "restart", "deployment/payments")
    wait_rollout()
    wait_consistent()


def inject(scenario: str) -> float:
    env = os.environ.copy()
    if scenario == "S2":
        env["EXPIRY_SECONDS"] = "3"
    onset = time.monotonic()
    run("bash", str(LAB / f"scenarios/{scenario.lower()}.sh"), env=env)
    if scenario == "S2":
        exception = json.loads((RUNTIME / "approvals/EXC-1.json").read_text())
        delay = max(0, exception["expires_utc"] - time.time())
        if delay:
            time.sleep(delay)
        onset = time.monotonic()
    if scenario == "S6":
        trigger_flux()
        wait_rollout()
    return onset


def observe(tier: str, onset: float, timeout: float = 120) -> tuple[dict, float]:
    deadline = time.monotonic() + timeout
    last = {"verdict": "consistent", "class": None, "detail": ""}
    while time.monotonic() < deadline:
        last = evaluate(tier)
        if last["verdict"] != "consistent":
            return last, time.monotonic() - onset
        time.sleep(0.5)
    return last, time.monotonic() - onset


def platform_snapshot() -> dict:
    server = json.loads(kubectl("version", "-o", "json"))["serverVersion"]["gitVersion"]
    flux = json.loads(kubectl("-n", "flux-system", "get", "deployments", "-o", "json"))
    kyverno = json.loads(kubectl("-n", "kyverno", "get", "deployments", "-o", "json"))
    images = lambda payload: sorted({
        container["image"]
        for item in payload["items"]
        for container in item["spec"]["template"]["spec"]["containers"]
    })
    return {
        "kind": run("kind", "version").strip(),
        "kubernetes_server": server,
        "flux_images": images(flux),
        "kyverno_images": images(kyverno),
        "evaluator_poll_seconds": 0.5,
        "design": "one bounded execution per scenario; no prevalence or reliability estimate",
    }


def write_outputs(rows: list[dict], started: str) -> None:
    RESULTS.mkdir(exist_ok=True)
    platforms = platform_snapshot()
    payload = {"started_utc": started, "completed_utc": datetime.now(timezone.utc).isoformat(), "platform": platforms, "rows": rows}
    (RESULTS / "observations.json").write_text(json.dumps(payload, indent=2) + "\n")
    with (RESULTS / "observations.csv").open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]), lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)
    (RESULTS / "platforms.json").write_text(json.dumps(platforms, indent=2) + "\n")
    lines = [
        "% Generated by lab/run_experiment.py",
        "\\begin{tabular}{@{}llllrr@{}}",
        "\\toprule",
        "Scenario & Injection & Expected & Observed & Latency (s) & Correct? \\\\",
        "\\midrule",
    ]
    for row in rows:
        injection = row["injection"].replace("_", "\\_")
        observed = row["observed_class"] + (" (U)" if row["observed_verdict"] == "undecidable" else "")
        lines.append(
            f"{row['scenario']} & {injection} & {row['expected_class']} & {observed} & "
            f"{float(row['latency_seconds']):.2f} & {'Yes' if row['correct'] else 'No'} \\\\" 
        )
    lines += ["\\bottomrule", "\\end{tabular}"]
    (RESULTS / "table_lab.tex").write_text("\n".join(lines) + "\n")


def main() -> None:
    if not (RUNTIME / "baseline_revision").exists():
        raise SystemExit("run lab/bootstrap.sh first")
    started = datetime.now(timezone.utc).isoformat()
    rows: list[dict] = []
    for scenario, injection_name, expected, tier in SCENARIOS:
        print(f"[{scenario}] reset", flush=True)
        reset_baseline()
        baseline = evaluate("T4")
        print(f"[{scenario}] inject {injection_name}", flush=True)
        onset = inject(scenario)
        observed, latency = observe(tier, onset)
        correct = observed.get("class") == expected
        row = {
            "scenario": scenario,
            "injection": injection_name,
            "expected_class": expected,
            "evaluator_tier": tier,
            "baseline_consistent": baseline["verdict"] == "consistent",
            "observed_verdict": observed["verdict"],
            "observed_class": observed.get("class") or "none",
            "latency_seconds": f"{latency:.3f}",
            "correct": correct,
        }
        rows.append(row)
        print(f"[{scenario}] {observed} latency={latency:.3f}s correct={correct}", flush=True)
        if not correct:
            write_outputs(rows, started)
            raise SystemExit(f"{scenario} mismatch")
    reset_baseline()
    write_outputs(rows, started)
    print(f"wrote {len(rows)} observations to {RESULTS}")


if __name__ == "__main__":
    main()
