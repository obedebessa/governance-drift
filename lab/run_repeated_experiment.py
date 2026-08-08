#!/usr/bin/env python3
"""Repeated live-stack evaluation with cadence and benign-change controls."""

from __future__ import annotations

import argparse
import csv
import json
import math
import random
import shutil
import statistics
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

import run_experiment as base
from evaluator import evaluate


LAB = Path(__file__).resolve().parent
RUNTIME = LAB / "runtime"
SEED = 20260807
CADENCES = (0.5, 2.0, 10.0)
CLASS_LABELS = ("configuration", "policy", "authorization", "intent", "evidence", "environment")
SCENARIOS = [
    ("S1", "manual in-cluster change", ("configuration",), "T0n"),
    ("S2", "expired exception", ("authorization",), "T2"),
    ("S3", "policy supersession", ("policy",), "T1"),
    ("S4", "artifact substitution", ("authorization",), "T3"),
    ("S5", "IAM expansion", ("environment",), "T4"),
    ("S6", "unapproved Git rollback", ("intent", "authorization"), "T3"),
    ("S7", "out-of-band LB change", ("environment",), "T4"),
    ("S8", "approval subject mismatch", ("authorization",), "T3"),
    ("S9", "approval-record deletion", ("evidence",), "T2"),
    ("S10", "policy supersession plus expired exception", ("policy", "authorization"), "T2"),
    ("S11", "artifact substitution plus environment change", ("authorization", "environment"), "T4"),
    ("S12", "rollback plus missing continuing-auth status", ("evidence",), "T2"),
]
CONTROLS = (
    ("C1", "satisfied policy revision"),
    ("C2", "approved rollback"),
    ("C3", "exception removed at expiry"),
    ("C4", "approved artifact re-tag"),
    ("C5", "autoscaling replica change"),
    ("C6", "legitimate rollout restart"),
)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def deterministic_phase(*parts: object, cadence: float) -> float:
    rng = random.Random(":".join(map(str, (SEED, *parts, cadence))))
    return rng.random() * cadence


def inject_with_onset(scenario: str) -> dict:
    """Return explicit injection and operational-onset timestamps.

    For command-carried mutations, onset is conservatively operationalized as
    successful command completion. For S6 it is successful Flux convergence;
    for S4 it is successful rollout of the substituted artifact; and for S2
    it is the exception-expiry instant.
    """
    injected_mono = time.monotonic()
    injected_utc = utc_now()
    env = None
    if scenario in {"S2", "S10"}:
        env = dict(base.os.environ)
        env["EXPIRY_SECONDS"] = "3"
    base.run("bash", str(LAB / f"scenarios/{scenario.lower()}.sh"), env=env)
    if scenario in {"S2", "S10"}:
        exception = json.loads((RUNTIME / "approvals/EXC-1.json").read_text())
        delay = max(0.0, float(exception["expires_utc"]) - time.time())
        if delay:
            time.sleep(delay)
    elif scenario in {"S6", "S12"}:
        base.trigger_flux()
        base.wait_rollout()
    onset_mono = time.monotonic()
    return {
        "injected_mono": injected_mono,
        "onset_mono": onset_mono,
        "injected_utc": injected_utc,
        "onset_utc": utc_now(),
        "actuation_seconds": onset_mono - injected_mono,
    }


def observe_cadences(
    *, repeat: int, scenario: str, tier: str, expected: tuple[str, ...], timing: dict
) -> list[dict]:
    onset = timing["onset_mono"]
    next_poll = {
        cadence: onset + deterministic_phase(repeat, scenario, cadence=cadence)
        for cadence in CADENCES
    }
    attempts = {cadence: 0 for cadence in CADENCES}
    first_seen: dict[float, dict] = {}
    finished: dict[float, dict] = {}
    deadline = onset + 180.0
    while len(finished) < len(CADENCES) and time.monotonic() < deadline:
        cadence = min((c for c in CADENCES if c not in finished), key=next_poll.get)
        delay = next_poll[cadence] - time.monotonic()
        if delay > 0:
            time.sleep(delay)
        attempts[cadence] += 1
        # Evaluate the complete vector at T4. ``tier`` remains the minimum
        # evidence tier predicted to decide the scenario and is reported
        # separately from the full evaluator used for set scoring.
        observed = evaluate("T4")
        observed_at = time.monotonic()
        if observed["verdict"] != "consistent":
            evidence = {
                "scenario": scenario,
                "tier": tier,
                "verdict": observed,
                "observed_utc": utc_now(),
            }
            json.dumps(evidence, sort_keys=True)
            evidence_at = time.monotonic()
            first_seen.setdefault(cadence, {
                "observed": observed,
                "detected_mono": observed_at,
                "evidence_mono": evidence_at,
            })
            if set(observed.get("class_set", [])) == set(expected):
                finished[cadence] = {
                    "observed": observed,
                    "completed_mono": observed_at,
                    "evidence_mono": evidence_at,
                }
        if cadence not in finished:
            next_poll[cadence] += cadence

    rows = []
    for cadence in CADENCES:
        first = first_seen.get(cadence)
        item = finished.get(cadence)
        selected = item or first
        observed = selected["observed"] if selected else {
            "verdict": "timeout", "class": None, "class_set": [],
            "components": {}, "undecidable_components": [], "detail": "no verdict"
        }
        detected = first["detected_mono"] if first else deadline
        evidence_at = first["evidence_mono"] if first else deadline
        vector_complete = item["completed_mono"] if item else deadline
        observed_class = observed.get("class") or "none"
        observed_set = tuple(observed.get("class_set", []))
        expected_set = set(expected)
        predicted_set = set(observed_set)
        rows.append({
            "repeat": repeat,
            "scenario": scenario,
            "cadence_seconds": cadence,
            "phase_seconds": next_poll[cadence] - onset
            if attempts[cadence] == 0 else deterministic_phase(repeat, scenario, cadence=cadence),
            "evaluator_tier": tier,
            "evaluation_scope": "T4-full-vector",
            "expected_class_set": "|".join(expected),
            "observed_verdict": observed["verdict"],
            "observed_class": observed_class,
            "observed_class_set": "|".join(observed_set),
            "first_observed_class_set": "|".join(
                first["observed"].get("class_set", []) if first else []
            ),
            "undecidable_components": "|".join(observed.get("undecidable_components", [])),
            "detection_rate_hit": observed["verdict"] not in {"consistent", "timeout"},
            "first_priority_correct": observed_class in expected_set,
            "classification_correct": item is not None and predicted_set == expected_set,
            "hamming_loss": len(predicted_set ^ expected_set) / len(CLASS_LABELS),
            "polls": attempts[cadence],
            "injection_started_utc": timing["injected_utc"],
            "onset_observed_utc": timing["onset_utc"],
            "actuation_seconds": timing["actuation_seconds"],
            "ddl_seconds": max(0.0, detected - onset),
            "end_to_end_seconds": max(0.0, detected - timing["injected_mono"]),
            "tte_seconds": max(0.0, evidence_at - onset),
            "vector_complete_seconds": max(0.0, vector_complete - onset),
            "evidence_semantics": "minimal synchronous verdict bundle",
        })
    return rows


def registry_digest(reference: str) -> str:
    raw = base.run(
        "docker", "buildx", "imagetools", "inspect", reference,
        "--format", "{{json .Manifest.Digest}}",
    ).strip()
    return json.loads(raw)


def update_approval(*, revision: str | None = None, digest: str | None = None) -> None:
    path = RUNTIME / "approvals/APR-1.json"
    approval = json.loads(path.read_text())
    if revision is not None:
        approval["revisions"] = sorted(set(approval.get("revisions", [])) | {revision})
    if digest is not None:
        approval["subjects"] = sorted(set(approval.get("subjects", [])) | {digest})
    path.write_text(json.dumps(approval, indent=2) + "\n")


def apply_control(control: str) -> None:
    if control == "C1":
        base.kubectl("apply", "-f", str(LAB / "policies/kyverno-policy-v7b.yaml"))
    elif control == "C2":
        base.run("bash", str(LAB / "scenarios/s6.sh"))
        revision = base.run("git", "rev-parse", "HEAD", cwd=RUNTIME / "work").strip()
        update_approval(
            revision=revision,
            digest=registry_digest("localhost:5001/governance-demo:alternate"),
        )
        base.trigger_flux()
        base.wait_rollout()
        base.run("bash", str(LAB / "snapshot.sh"))
    elif control == "C3":
        env = dict(base.os.environ)
        env["EXPIRY_SECONDS"] = "2"
        base.run("bash", str(LAB / "scenarios/s2.sh"), env=env)
        exception_path = RUNTIME / "approvals/EXC-1.json"
        exception = json.loads(exception_path.read_text())
        delay = max(0.0, float(exception["expires_utc"]) - time.time())
        if delay:
            time.sleep(delay)
        exception_path.unlink()
    elif control == "C4":
        base.run(
            "docker", "tag", "localhost:5001/governance-demo:alternate",
            "localhost:5001/governance-demo:1.0",
        )
        base.run("docker", "push", "localhost:5001/governance-demo:1.0")
        update_approval(digest=registry_digest("localhost:5001/governance-demo:1.0"))
        base.kubectl("-n", "payments", "rollout", "restart", "deployment/payments")
        base.wait_rollout()
        base.run("bash", str(LAB / "snapshot.sh"))
    elif control == "C5":
        base.kubectl("-n", "payments", "scale", "deployment/payments", "--replicas=2")
        base.wait_rollout()
    elif control == "C6":
        base.kubectl("-n", "payments", "rollout", "restart", "deployment/payments")
        base.wait_rollout()
    else:
        raise ValueError(control)
    base.wait_consistent()


def observe_control_window(window: int, control: str, name: str, duration: float) -> list[dict]:
    started = time.monotonic()
    next_poll = {
        cadence: started + deterministic_phase("control", window, control, cadence=cadence)
        for cadence in CADENCES
    }
    polls = {cadence: 0 for cadence in CADENCES}
    alarms = {cadence: 0 for cadence in CADENCES}
    epistemic = {cadence: 0 for cadence in CADENCES}
    observed_classes = {cadence: [] for cadence in CADENCES}
    deadline = started + duration
    while True:
        due = min(next_poll, key=next_poll.get)
        if next_poll[due] > deadline:
            break
        delay = next_poll[due] - time.monotonic()
        if delay > 0:
            time.sleep(delay)
        observed = evaluate("T4")
        polls[due] += 1
        if observed["verdict"] == "drift":
            alarms[due] += 1
            observed_classes[due].append(observed.get("class") or "none")
        elif observed["verdict"] == "undecidable":
            epistemic[due] += 1
            observed_classes[due].append("evidence")
        next_poll[due] += due
    return [{
        "window": window,
        "control": control,
        "control_name": name,
        "cadence_seconds": cadence,
        "duration_seconds": duration,
        "polls": polls[cadence],
        "alarms": alarms[cadence],
        "epistemic_warnings": epistemic[cadence],
        "false_alarm_window": alarms[cadence] > 0,
        "observed_classes": "|".join(observed_classes[cadence]),
    } for cadence in CADENCES]


def percentile95(values: list[float]) -> float:
    ordered = sorted(values)
    return ordered[max(0, math.ceil(0.95 * len(ordered)) - 1)]


def pct(value: float) -> str:
    return f"{100.0 * value:.1f}"


def as_bool(value: object) -> bool:
    return value is True or str(value).lower() == "true"


def tex_set(value: str) -> str:
    labels = ["auth." if item == "authorization" else item for item in value.split("|")]
    return r"\{" + ", ".join(labels) + r"\}"


def write_csv(path: Path, rows: list[dict]) -> None:
    if not rows:
        return
    with path.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]), lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def write_outputs(out: Path, positives: list[dict], controls: list[dict], args: argparse.Namespace) -> None:
    out.mkdir(parents=True, exist_ok=True)
    positives.sort(key=lambda r: (int(r["repeat"]), r["scenario"], float(r["cadence_seconds"])))
    controls.sort(key=lambda r: (int(r["window"]), r["control"], float(r["cadence_seconds"])))
    write_csv(out / "repeated_observations.csv", positives)
    write_csv(out / "control_observations.csv", controls)
    platform = base.platform_snapshot()
    platform["evaluator_cadences_seconds"] = list(CADENCES)
    platform["design"] = (
        f"{args.repetitions} repetitions per selected scenario with randomized "
        f"polling phase; {args.control_windows} balanced benign-control windows "
        f"of {args.control_window_seconds:g} seconds"
    )
    payload = {
        "seed": SEED,
        "repetitions_per_scenario": args.repetitions,
        "control_windows": args.control_windows,
        "control_window_seconds": args.control_window_seconds,
        "cadences_seconds": CADENCES,
        "completed_utc": utc_now(),
        "platform": platform,
        "positive_rows": positives,
        "control_rows": controls,
    }
    (out / "repeated_observations.json").write_text(json.dumps(payload, indent=2) + "\n")

    default_rows = [r for r in positives if float(r["cadence_seconds"]) == 0.5]
    scenario_lines = [
        "% Generated by lab/run_repeated_experiment.py",
        r"\begin{tabular}{@{}llllrrrrrrr@{}}",
        r"\toprule",
        r"ID & Expected set & Observed set & First & DR & ESA & Act. & DDL & P95 & E2E & TTE \\",
        r"\midrule",
    ]
    scenario_summary = []
    for scenario, _, expected, _ in SCENARIOS:
        rows = [r for r in default_rows if r["scenario"] == scenario]
        if not rows:
            continue
        act = [float(r["actuation_seconds"]) for r in rows]
        ddl = [float(r["ddl_seconds"]) for r in rows]
        e2e = [float(r["end_to_end_seconds"]) for r in rows]
        tte = [float(r["tte_seconds"]) for r in rows]
        detected_rows = [r for r in rows if as_bool(r["detection_rate_hit"])]
        dr = len(detected_rows) / len(rows)
        esa = sum(as_bool(r["classification_correct"]) for r in detected_rows) / len(detected_rows)
        observed = Counter(r["observed_class"] for r in rows).most_common(1)[0][0]
        observed_set = Counter(r["observed_class_set"] for r in detected_rows).most_common(1)[0][0]
        item = {
            "scenario": scenario,
            "expected_class_set": "|".join(expected),
            "observed_class_set": observed_set,
            "observed_first_priority": observed,
            "n": len(rows),
            "detection_rate": dr,
            "exact_set_accuracy": esa,
            "actuation_median_seconds": statistics.median(act),
            "ddl_median_seconds": statistics.median(ddl),
            "ddl_p95_seconds": percentile95(ddl),
            "end_to_end_median_seconds": statistics.median(e2e),
            "tte_median_seconds": statistics.median(tte),
        }
        scenario_summary.append(item)
        scenario_lines.append(
            f"{scenario} & {tex_set(item['expected_class_set'])} & "
            f"{tex_set(item['observed_class_set'])} & {observed} & {pct(dr)} & {pct(esa)} & "
            f"{item['actuation_median_seconds']:.2f} & {item['ddl_median_seconds']:.2f} & "
            f"{item['ddl_p95_seconds']:.2f} & {item['end_to_end_median_seconds']:.2f} & "
            f"{item['tte_median_seconds']:.2f} \\\\"
        )
    scenario_lines += [r"\bottomrule", r"\end{tabular}"]
    (out / "table_repeated.tex").write_text("\n".join(scenario_lines) + "\n")

    cadence_lines = [
        "% Generated by lab/run_repeated_experiment.py",
        r"\begin{tabular}{@{}rrrrrr@{}}",
        r"\toprule",
        r"Cadence & Runs & DR (\%) & ESA (\%) & Median DDL & P95 DDL \\",
        r"\midrule",
    ]
    cadence_summary = []
    for cadence in CADENCES:
        rows = [r for r in positives if float(r["cadence_seconds"]) == cadence]
        if not rows:
            continue
        ddl = [float(r["ddl_seconds"]) for r in rows]
        detected_rows = [r for r in rows if as_bool(r["detection_rate_hit"])]
        dr = len(detected_rows) / len(rows)
        esa = sum(as_bool(r["classification_correct"]) for r in detected_rows) / len(detected_rows)
        item = {
            "cadence_seconds": cadence,
            "runs": len(rows),
            "detection_rate": dr,
            "exact_set_accuracy": esa,
            "ddl_median_seconds": statistics.median(ddl),
            "ddl_p95_seconds": percentile95(ddl),
        }
        cadence_summary.append(item)
        cadence_lines.append(
            f"{cadence:.1f} & {len(rows)} & {pct(dr)} & {pct(esa)} & "
            f"{item['ddl_median_seconds']:.2f} & {item['ddl_p95_seconds']:.2f} \\\\"
        )
    cadence_lines += [r"\bottomrule", r"\end{tabular}"]
    (out / "table_cadence.tex").write_text("\n".join(cadence_lines) + "\n")

    control_lines = [
        "% Generated by lab/run_repeated_experiment.py",
        r"\begin{tabular}{@{}llrrrr@{}}",
        r"\toprule",
        r"ID & Benign control & Windows & Polls & Alarm windows & Epistemic polls \\",
        r"\midrule",
    ]
    control_summary = []
    for control, name in CONTROLS:
        rows = [r for r in controls if r["control"] == control]
        windows = len({int(r["window"]) for r in rows})
        polls = sum(int(r["polls"]) for r in rows)
        alarm_windows = len({int(r["window"]) for r in rows if as_bool(r["false_alarm_window"])})
        epistemic_polls = sum(int(r.get("epistemic_warnings", 0)) for r in rows)
        item = {"control": control, "control_name": name, "windows": windows,
                "polls": polls, "alarm_windows": alarm_windows,
                "epistemic_polls": epistemic_polls}
        control_summary.append(item)
        control_lines.append(
            f"{control} & {name} & {windows} & {polls} & {alarm_windows} & {epistemic_polls} \\\\"
        )
    control_lines += [r"\bottomrule", r"\end{tabular}"]
    (out / "table_controls.tex").write_text("\n".join(control_lines) + "\n")

    detected_rows = [r for r in positives if as_bool(r["detection_rate_hit"])]
    vector_rows = []
    for label in CLASS_LABELS:
        tp = fp = fn = 0
        for row in positives:
            expected = set(str(row["expected_class_set"]).split("|"))
            observed = set(filter(None, str(row.get("observed_class_set", "")).split("|")))
            tp += int(label in expected and label in observed)
            fp += int(label not in expected and label in observed)
            fn += int(label in expected and label not in observed)
        precision = tp / (tp + fp) if tp + fp else 1.0
        recall = tp / (tp + fn) if tp + fn else 1.0
        vector_rows.append({"component": label, "tp": tp, "fp": fp, "fn": fn,
                            "precision": precision, "recall": recall})
    vector_metrics = {
        "exact_set_accuracy_all": (
            sum(as_bool(r["classification_correct"]) for r in positives) / len(positives)
        ),
        "exact_set_accuracy_conditional": (
            sum(as_bool(r["classification_correct"]) for r in detected_rows)
            / len(detected_rows)
        ),
        "hamming_loss": statistics.mean(float(r["hamming_loss"]) for r in positives),
        "per_component": vector_rows,
    }
    vector_lines = [
        "% Generated by lab/run_repeated_experiment.py",
        r"\begin{tabular}{@{}lrrrrr@{}}",
        r"\toprule",
        r"Component & TP & FP & FN & Precision & Recall \\",
        r"\midrule",
    ]
    for row in vector_rows:
        vector_lines.append(
            f"{row['component']} & {row['tp']} & {row['fp']} & {row['fn']} & "
            f"{100*row['precision']:.1f}\% & {100*row['recall']:.1f}\% \\\\"
        )
    vector_lines += [r"\bottomrule", r"\end{tabular}"]
    (out / "table_vector_metrics.tex").write_text("\n".join(vector_lines) + "\n")

    summary = {
        "scenario_default_cadence": scenario_summary,
        "cadence": cadence_summary,
        "controls": control_summary,
        "vector_metrics": vector_metrics,
        "total_positive_observations": len(positives),
        "total_control_observations": len(controls),
    }
    (out / "repeated_summary.json").write_text(json.dumps(summary, indent=2) + "\n")


def load_checkpoint(path: Path) -> tuple[list[dict], list[dict]]:
    if not path.exists():
        return [], []
    payload = json.loads(path.read_text())
    return payload.get("positive_rows", []), payload.get("control_rows", [])


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repetitions", type=int, default=20)
    parser.add_argument("--control-windows", type=int, default=20)
    parser.add_argument("--control-window-seconds", type=float, default=10.5)
    parser.add_argument("--output-dir", type=Path, default=LAB / "results")
    parser.add_argument(
        "--scenario-ids", default="",
        help="comma-separated subset such as S10,S11,S12; empty means all",
    )
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--summarize-only", action="store_true")
    args = parser.parse_args()
    if args.summarize_only:
        document = json.loads((args.output_dir / "repeated_observations.json").read_text())
        write_outputs(
            args.output_dir,
            document["positive_rows"],
            document["control_rows"],
            args,
        )
        print("regenerated repeated-study summaries", flush=True)
        return
    if not (RUNTIME / "baseline_revision").exists():
        raise SystemExit("run lab/bootstrap.sh first")

    checkpoint = args.output_dir / "repeated_checkpoint.json"
    positives, controls = load_checkpoint(checkpoint) if args.resume else ([], [])
    completed_positive = {
        (int(r["repeat"]), r["scenario"])
        for r in positives
        if sum(1 for x in positives if int(x["repeat"]) == int(r["repeat"]) and x["scenario"] == r["scenario"]) == len(CADENCES)
    }
    completed_control = {
        int(r["window"])
        for r in controls
        if sum(1 for x in controls if int(x["window"]) == int(r["window"])) == len(CADENCES)
    }

    selected_ids = set(filter(None, (item.strip() for item in args.scenario_ids.split(","))))
    selected_scenarios = [item for item in SCENARIOS if not selected_ids or item[0] in selected_ids]
    unknown = selected_ids - {item[0] for item in SCENARIOS}
    if unknown:
        raise SystemExit(f"unknown scenario ids: {sorted(unknown)}")
    tasks = []
    for repeat in range(1, args.repetitions + 1):
        ordered = list(selected_scenarios)
        random.Random(SEED + repeat).shuffle(ordered)
        tasks.extend((repeat, item) for item in ordered)
    for repeat, (scenario, injection, expected, tier) in tasks:
        if (repeat, scenario) in completed_positive:
            continue
        print(f"[positive {repeat:02d}/{args.repetitions} {scenario}] reset", flush=True)
        base.reset_baseline()
        baseline = evaluate("T4")
        if baseline["verdict"] != "consistent":
            raise RuntimeError(f"inconsistent baseline: {baseline}")
        print(f"[positive {repeat:02d}/{args.repetitions} {scenario}] inject {injection}", flush=True)
        timing = inject_with_onset(scenario)
        rows = observe_cadences(
            repeat=repeat, scenario=scenario, tier=tier, expected=expected, timing=timing
        )
        positives.extend(rows)
        print(
            f"[positive {repeat:02d}/{args.repetitions} {scenario}] "
            + ", ".join(
                f"{r['cadence_seconds']}s:{r['observed_class']}/"
                f"DDL={float(r['ddl_seconds']):.2f}s/VC={float(r['vector_complete_seconds']):.2f}s"
                for r in rows
            ),
            flush=True,
        )
        write_outputs(args.output_dir, positives, controls, args)
        checkpoint.write_text(json.dumps({"positive_rows": positives, "control_rows": controls}, indent=2) + "\n")

    for window in range(1, args.control_windows + 1):
        if window in completed_control:
            continue
        control, name = CONTROLS[(window - 1) % len(CONTROLS)]
        print(f"[control {window:02d}/{args.control_windows} {control}] reset", flush=True)
        base.reset_baseline()
        print(f"[control {window:02d}/{args.control_windows} {control}] apply {name}", flush=True)
        apply_control(control)
        rows = observe_control_window(window, control, name, args.control_window_seconds)
        controls.extend(rows)
        print(
            f"[control {window:02d}/{args.control_windows} {control}] "
            + ", ".join(f"{r['cadence_seconds']}s:{r['alarms']}/{r['polls']}" for r in rows),
            flush=True,
        )
        write_outputs(args.output_dir, positives, controls, args)
        checkpoint.write_text(json.dumps({"positive_rows": positives, "control_rows": controls}, indent=2) + "\n")

    base.reset_baseline()
    write_outputs(args.output_dir, positives, controls, args)
    if checkpoint.exists():
        checkpoint.unlink()
    print(
        f"completed {len(positives)} positive cadence observations and "
        f"{len(controls)} control cadence observations",
        flush=True,
    )


if __name__ == "__main__":
    main()
