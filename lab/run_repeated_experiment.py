#!/usr/bin/env python3
"""Repeated live-stack evaluation with cadence and benign-change controls."""

from __future__ import annotations

import argparse
import csv
import hashlib
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
SEQUENTIAL_SCOPE = "T4-sequential-snapshot-class-set"
SCENARIOS = [
    ("S1", "manual in-cluster change", ("configuration",), "T0n"),
    ("S2", "expired exception", ("authorization",), "T2"),
    ("S3", "policy supersession", ("policy",), "T1"),
    ("S4", "artifact substitution", ("authorization",), "T3"),
    ("S5", "IAM expansion", ("environment",), "T4"),
    ("S6", "unapproved Git rollback", ("intent", "authorization"), "T3"),
    ("S7", "out-of-band LB change", ("environment",), "T4"),
    ("S8", "approval subject mismatch", ("authorization",), "T3"),
    ("S9", "continuing-authorization live-status evidence loss", ("evidence",), "T2"),
    ("S10", "policy supersession plus expired exception", ("policy", "authorization"), "T2"),
    ("S11", "artifact substitution plus environment change", ("authorization", "environment"), "T4"),
    ("S12", "rollback plus missing continuing-auth status", ("intent", "evidence"), "T2"),
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
        # Evaluate every implemented component at T4 from the lab's sequential
        # reads. ``tier`` remains the minimum evidence tier predicted to decide
        # the scenario.  Because this harness does not expose cross-stream
        # watermarks, the result is a provisional class-set classification, not
        # the formal atomic/watermark-qualified verdict bundle.
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
        detected = first["detected_mono"] if first else None
        evidence_at = first["evidence_mono"] if first else None
        exact_set_complete = item["completed_mono"] if item else None
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
            "evaluation_scope": SEQUENTIAL_SCOPE,
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
            "ddl_seconds": max(0.0, detected - onset) if detected is not None else "",
            "end_to_end_seconds": (
                max(0.0, detected - timing["injected_mono"])
                if detected is not None else ""
            ),
            "tte_seconds": max(0.0, evidence_at - onset) if evidence_at is not None else "",
            "exact_set_complete_seconds": (
                max(0.0, exact_set_complete - onset)
                if exact_set_complete is not None else ""
            ),
            "ddl_right_censored": first is None,
            "exact_set_completion_right_censored": item is None,
            "censoring_seconds": max(0.0, deadline - onset),
            "evidence_semantics": "provisional sequential-snapshot class-set record",
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
    fieldnames = list(rows[0])
    for row in rows[1:]:
        for key in row:
            if key not in fieldnames:
                fieldnames.append(key)
    with path.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def source_label(path: Path) -> str:
    """Return a non-identifying repository-relative source label."""
    resolved = path.resolve()
    try:
        return str(resolved.relative_to(LAB.parent))
    except ValueError:
        return path.name


def merge_unique_rows(
    current: list[dict], incoming: list[dict], *, keys: tuple[str, ...], label: str
) -> list[dict]:
    """Merge reportable rows while rejecting conflicting duplicate keys."""
    merged = list(current)
    by_key = {tuple(str(row[key]) for key in keys): row for row in merged}
    for row in incoming:
        key = tuple(str(row[item]) for item in keys)
        existing = by_key.get(key)
        if existing is not None:
            if existing != row:
                raise SystemExit(f"conflicting {label} row for key {key}")
            continue
        merged.append(row)
        by_key[key] = row
    return merged


def load_reuse_document(path: Path) -> dict:
    if not path.is_file():
        raise SystemExit(f"reuse source does not exist: {path}")
    document = json.loads(path.read_text())
    if not isinstance(document.get("positive_rows"), list) or not isinstance(
        document.get("control_rows"), list
    ):
        raise SystemExit(f"reuse source has no repeated-study rows: {path}")
    return document


def normalize_positive_row(row: dict) -> dict:
    """Migrate legacy live-lab labels without changing measured values."""
    normalized = dict(row)
    normalized["evaluation_scope"] = SEQUENTIAL_SCOPE
    normalized["evidence_semantics"] = (
        "provisional sequential-snapshot class-set record"
    )
    if "exact_set_complete_seconds" not in normalized:
        normalized["exact_set_complete_seconds"] = normalized.pop(
            "vector_complete_seconds", ""
        )
    else:
        normalized.pop("vector_complete_seconds", None)
    if "exact_set_completion_right_censored" not in normalized:
        normalized["exact_set_completion_right_censored"] = normalized.pop(
            "vcl_right_censored",
            not as_bool(normalized.get("classification_correct", False)),
        )
    else:
        normalized.pop("vcl_right_censored", None)
    return normalized


def write_outputs(
    out: Path,
    positives: list[dict],
    controls: list[dict],
    args: argparse.Namespace,
    platform_snapshot: dict | None = None,
) -> None:
    out.mkdir(parents=True, exist_ok=True)
    # Normalize the historical field label when earlier campaign rows are
    # assembled with a targeted semantic rerun. Timings and verdicts remain
    # unchanged; the new label removes an unsupported atomic-vector claim.
    positives[:] = [normalize_positive_row(row) for row in positives]
    positives.sort(key=lambda r: (int(r["repeat"]), r["scenario"], float(r["cadence_seconds"])))
    controls.sort(key=lambda r: (int(r["window"]), r["control"], float(r["cadence_seconds"])))
    write_csv(out / "repeated_observations.csv", positives)
    write_csv(out / "control_observations.csv", controls)
    platform = dict(platform_snapshot) if platform_snapshot is not None else base.platform_snapshot()
    platform["evaluator_cadences_seconds"] = list(CADENCES)
    actual_control_windows = len({int(row["window"]) for row in controls})
    control_durations = {float(row["duration_seconds"]) for row in controls}
    actual_control_seconds = (
        next(iter(control_durations)) if len(control_durations) == 1
        else args.control_window_seconds
    )
    represented_scenarios = sorted({str(row["scenario"]) for row in positives})
    platform["design"] = (
        f"assembled reportable dataset with {args.repetitions} repetitions per "
        f"represented scenario and randomized polling phase; "
        f"{actual_control_windows} benign-control windows of "
        f"{actual_control_seconds:g} seconds; live reads are sequential and do "
        f"not establish atomic cross-stream cuts"
    )
    payload = {
        "seed": SEED,
        "repetitions_per_scenario": args.repetitions,
        "control_windows": actual_control_windows,
        "control_window_seconds": actual_control_seconds,
        "cadences_seconds": CADENCES,
        "evaluation_scope": SEQUENTIAL_SCOPE,
        "represented_scenarios": represented_scenarios,
        "execution_selection": sorted(getattr(args, "selected_ids", set())),
        "reuse_provenance": getattr(args, "reuse_provenance", []),
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
        detected_rows = [r for r in rows if as_bool(r["detection_rate_hit"])]
        act = [float(r["actuation_seconds"]) for r in rows]
        ddl = [float(r["ddl_seconds"]) for r in detected_rows]
        e2e = [float(r["end_to_end_seconds"]) for r in detected_rows]
        tte = [float(r["tte_seconds"]) for r in detected_rows]
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
        detected_rows = [r for r in rows if as_bool(r["detection_rate_hit"])]
        ddl = [float(r["ddl_seconds"]) for r in detected_rows]
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
    class_set_rows = []
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
        class_set_rows.append({"component": label, "tp": tp, "fp": fp, "fn": fn,
                               "precision": precision, "recall": recall})
    class_set_metrics = {
        "unconditional_exact_class_set_success": (
            sum(as_bool(r["classification_correct"]) for r in positives) / len(positives)
        ),
        "exact_set_accuracy_conditional": (
            sum(as_bool(r["classification_correct"]) for r in detected_rows)
            / len(detected_rows)
        ),
        "hamming_loss": statistics.mean(float(r["hamming_loss"]) for r in positives),
        "per_component": class_set_rows,
    }
    class_set_lines = [
        "% Generated by lab/run_repeated_experiment.py",
        r"\begin{tabular}{@{}lrrrrr@{}}",
        r"\toprule",
        r"Component & TP & FP & FN & Precision & Recall \\",
        r"\midrule",
    ]
    for row in class_set_rows:
        class_set_lines.append(
            f"{row['component']} & {row['tp']} & {row['fp']} & {row['fn']} & "
            f"{100*row['precision']:.1f}\% & {100*row['recall']:.1f}\% \\\\"
        )
    class_set_lines += [r"\bottomrule", r"\end{tabular}"]
    (out / "table_class_set_metrics.tex").write_text(
        "\n".join(class_set_lines) + "\n"
    )

    summary = {
        "scenario_default_cadence": scenario_summary,
        "cadence": cadence_summary,
        "controls": control_summary,
        "class_set_metrics": class_set_metrics,
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
    parser.add_argument(
        "--reuse-positive-observations",
        type=Path,
        help=(
            "prior repeated_observations.json from which scenarios outside "
            "--scenario-ids are retained with hash provenance"
        ),
    )
    parser.add_argument(
        "--reuse-control-observations",
        type=Path,
        help=(
            "prior repeated_observations.json whose benign-control rows are "
            "retained with hash provenance"
        ),
    )
    args = parser.parse_args()
    if args.summarize_only:
        document = json.loads((args.output_dir / "repeated_observations.json").read_text())
        args.selected_ids = set(document.get("execution_selection", []))
        args.reuse_provenance = list(document.get("reuse_provenance", []))
        for record in args.reuse_provenance:
            if record.get("role") == "positive rows outside targeted semantic rerun":
                record["normalization"] = (
                    "evaluation-scope/evidence-semantics labels and legacy "
                    "completion field names only; measured values unchanged"
                )
        positives = [normalize_positive_row(row) for row in document["positive_rows"]]
        for row in positives:
            row.setdefault("first_observed_class_set", row.get("observed_class_set", ""))
            row.setdefault(
                "exact_set_complete_seconds",
                row.get("ddl_seconds", "")
                if as_bool(row.get("classification_correct", False)) else "",
            )
            if not as_bool(row.get("detection_rate_hit", False)):
                censoring = float(
                    row.get("censoring_seconds") or row.get("ddl_seconds") or 180.0
                )
                row["ddl_seconds"] = ""
                row["end_to_end_seconds"] = ""
                row["tte_seconds"] = ""
                row["exact_set_complete_seconds"] = ""
                row["ddl_right_censored"] = True
                row["exact_set_completion_right_censored"] = True
                row["censoring_seconds"] = censoring
            else:
                row.setdefault("ddl_right_censored", False)
                row.setdefault(
                    "exact_set_completion_right_censored",
                    not as_bool(row.get("classification_correct", False)),
                )
                row.setdefault("censoring_seconds", 180.0)
        write_outputs(
            args.output_dir,
            positives,
            document["control_rows"],
            args,
            platform_snapshot=document.get("platform"),
        )
        print("regenerated repeated-study summaries", flush=True)
        return
    if not (RUNTIME / "baseline_revision").exists():
        raise SystemExit("run lab/bootstrap.sh first")

    checkpoint = args.output_dir / "repeated_checkpoint.json"
    positives, controls = load_checkpoint(checkpoint) if args.resume else ([], [])
    selected_ids = set(filter(None, (item.strip() for item in args.scenario_ids.split(","))))
    args.selected_ids = selected_ids
    args.reuse_provenance = []
    if (args.reuse_positive_observations or args.reuse_control_observations) and not selected_ids:
        raise SystemExit("reuse requires an explicit --scenario-ids rerun selection")
    if args.reuse_positive_observations:
        source = args.reuse_positive_observations
        document = load_reuse_document(source)
        retained = [
            normalize_positive_row(row) for row in document["positive_rows"]
            if str(row.get("scenario")) not in selected_ids
        ]
        positives = merge_unique_rows(
            positives,
            retained,
            keys=("repeat", "scenario", "cadence_seconds"),
            label="positive",
        )
        args.reuse_provenance.append({
            "role": "positive rows outside targeted semantic rerun",
            "source_file": source_label(source),
            "source_sha256": sha256_file(source),
            "rows_retained": len(retained),
            "normalization": (
                "evaluation-scope/evidence-semantics labels and legacy "
                "completion field names only; measured values unchanged"
            ),
        })
    if args.reuse_control_observations:
        source = args.reuse_control_observations
        document = load_reuse_document(source)
        retained = [dict(row) for row in document["control_rows"]]
        controls = merge_unique_rows(
            controls,
            retained,
            keys=("window", "control", "cadence_seconds"),
            label="control",
        )
        args.reuse_provenance.append({
            "role": "unchanged benign-control observations",
            "source_file": source_label(source),
            "source_sha256": sha256_file(source),
            "rows_retained": len(retained),
            "normalization": "none",
        })
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
                f"DDL={r['ddl_seconds'] if r['ddl_seconds'] != '' else 'censored'}/"
                f"ESC={r['exact_set_complete_seconds'] if r['exact_set_complete_seconds'] != '' else 'censored'}"
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
