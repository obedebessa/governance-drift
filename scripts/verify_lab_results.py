#!/usr/bin/env python3
"""Verify the frozen bounded-laboratory observations and generated table."""

from __future__ import annotations

import csv
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
RESULTS = ROOT / "lab" / "results"
EXPECTED = {
    "S1": ("configuration", "T0n", "drift"),
    "S2": ("authorization", "T2", "drift"),
    "S3": ("policy", "T1", "drift"),
    "S4": ("authorization", "T3", "drift"),
    "S5": ("environment", "T4", "drift"),
    "S6": ("intent", "T2", "drift"),
    "S7": ("environment", "T4", "drift"),
    "S8": ("authorization", "T3", "drift"),
    "S9": ("evidence", "T2", "undecidable"),
}
REPEATED_EXPECTED = {
    "S1": "configuration",
    "S2": "authorization",
    "S3": "policy",
    "S4": "authorization",
    "S5": "environment",
    "S6": "intent|authorization",
    "S7": "environment",
    "S8": "authorization",
    "S9": "evidence",
}
REPEATED_TIER = {
    "S1": "T0n", "S2": "T2", "S3": "T1", "S4": "T3", "S5": "T4",
    "S6": "T3", "S7": "T4", "S8": "T3", "S9": "T2",
}
CADENCES = {"0.5", "2.0", "10.0"}


def fail(message: str) -> None:
    raise SystemExit(f"FAIL: {message}")


def verify_repeated() -> None:
    document = json.loads((RESULTS / "repeated_observations.json").read_text())
    rows = document.get("positive_rows", [])
    controls = document.get("control_rows", [])
    if len(rows) != 540:
        fail("repeated study must contain 540 positive cadence observations")
    if document.get("repetitions_per_scenario") != 20:
        fail("repeated-study repetition count changed")
    if document.get("control_windows") != 20:
        fail("repeated-study control-window count changed")
    if set(map(str, document.get("cadences_seconds", []))) != CADENCES:
        fail("repeated-study cadence set changed")

    seen = {}
    misses = set()
    for row in rows:
        scenario = row["scenario"]
        cadence = str(row["cadence_seconds"])
        repeat = int(row["repeat"])
        if row.get("expected_class_set") != REPEATED_EXPECTED.get(scenario):
            fail(f"{scenario}: repeated expected-class set changed")
        if row.get("evaluator_tier") != REPEATED_TIER.get(scenario):
            fail(f"{scenario}: minimum deciding tier changed")
        if row.get("evaluation_scope") != "T4-sequential-snapshot-class-set":
            fail(f"{scenario}: sequential-snapshot evaluation scope is absent")
        if scenario == "S9" and row.get("undecidable_components") != "authorization":
            fail("S9 must mask current authorization only; intent remains decidable")
        key = (repeat, scenario, cadence)
        seen[key] = seen.get(key, 0) + 1
        detected = row.get("detection_rate_hit") is True
        if detected:
            if row.get("classification_correct") is not True:
                fail(f"{key}: detected vector does not exactly match ground truth")
            if set(row.get("observed_class_set", "").split("|")) != set(REPEATED_EXPECTED[scenario].split("|")):
                fail(f"{key}: observed class set differs from ground truth")
            if float(row.get("hamming_loss", -1)) != 0.0:
                fail(f"{key}: exact set has nonzero Hamming loss")
        else:
            misses.add((repeat, scenario, cadence))
            if row.get("observed_verdict") != "timeout":
                fail(f"{key}: non-detection must be recorded as timeout")
            if row.get("ddl_right_censored") is not True:
                fail(f"{key}: timeout must be marked as right-censored")
            if any(row.get(field) not in {"", None} for field in
                   ("ddl_seconds", "end_to_end_seconds", "tte_seconds", "exact_set_complete_seconds")):
                fail(f"{key}: censored latency must not be stored as observed")
            if float(row.get("censoring_seconds", -1)) <= 0:
                fail(f"{key}: invalid censoring bound")
        for field in ("actuation_seconds", "ddl_seconds", "end_to_end_seconds", "tte_seconds"):
            if row.get(field) not in {"", None} and float(row[field]) < 0:
                fail(f"{key}: negative {field}")
    if len(seen) != 540 or any(count != 1 for count in seen.values()):
        fail("repeated positive keys are not unique and complete")
    if len(misses) != 2 or any(scenario != "S1" for _, scenario, _ in misses):
        fail(f"unexpected frozen miss pattern: {sorted(misses)}")

    with (RESULTS / "repeated_observations.csv").open(newline="") as handle:
        csv_rows = list(csv.DictReader(handle))
    projection = [{key: str(value) for key, value in row.items()} for row in rows]
    if csv_rows != projection:
        fail("repeated_observations.csv differs from JSON positive rows")

    if len(controls) != 60 or len({int(row["window"]) for row in controls}) != 20:
        fail("control observations must contain three cadences for 20 windows")
    if sum(int(row["polls"]) for row in controls) != 543:
        fail("frozen benign-control poll total changed")
    if any(int(row["alarms"]) != 0 or row["false_alarm_window"] is not False for row in controls):
        fail("benign-control false alarm detected")
    if any(int(row.get("epistemic_warnings", 0)) != 0 for row in controls):
        fail("benign control produced an epistemic warning")
    with (RESULTS / "control_observations.csv").open(newline="") as handle:
        csv_controls = list(csv.DictReader(handle))
    control_projection = [{key: str(value) for key, value in row.items()} for row in controls]
    if csv_controls != control_projection:
        fail("control_observations.csv differs from JSON control rows")

    platform = document.get("platform", {})
    if platform.get("evaluator_cadences_seconds") != [0.5, 2.0, 10.0]:
        fail("repeated-study platform cadence metadata changed")
    if not str(platform.get("design", "")).startswith(
        "assembled reportable dataset with 20 repetitions per represented scenario"
    ):
        fail("repeated-study design metadata changed")
    if document.get("execution_selection") != ["S9"]:
        fail("targeted semantic rerun selection is not disclosed")
    provenance = document.get("reuse_provenance", [])
    if len(provenance) != 2 or {row.get("rows_retained") for row in provenance} != {60, 480}:
        fail("targeted rerun reuse provenance is incomplete")
    if any(len(str(row.get("source_sha256", ""))) != 64 for row in provenance):
        fail("targeted rerun source hash is missing")
    for field in ("host_cpu", "host_architecture", "host_ram_bytes", "host_os",
                  "docker_server", "kind_node_capacity", "kind_container_limits", "clock"):
        if field not in platform:
            fail(f"measurement environment omits {field}")
    if platform["clock"].get("monotonic") is not True:
        fail("measurement clock is not recorded as monotonic")

    summary = json.loads((RESULTS / "repeated_summary.json").read_text())
    if summary.get("total_positive_observations") != 540:
        fail("repeated summary positive total changed")
    if summary.get("total_control_observations") != 60:
        fail("repeated summary control total changed")
    cadence = {str(row["cadence_seconds"]): row for row in summary["cadence"]}
    if [cadence[c]["runs"] for c in ("0.5", "2.0", "10.0")] != [180, 180, 180]:
        fail("cadence summary run totals changed")
    if [round(cadence[c]["detection_rate"] * 180) for c in ("0.5", "2.0", "10.0")] != [180, 179, 179]:
        fail("cadence summary detection totals changed")
    if any(row["exact_set_accuracy"] != 1.0 for row in cadence.values()):
        fail("conditional exact-set accuracy changed")
    class_set = summary.get("class_set_metrics", {})
    if abs(float(class_set.get("unconditional_exact_class_set_success", -1)) - 538 / 540) > 1e-12:
        fail("unconditional exact-class-set success changed")
    if class_set.get("exact_set_accuracy_conditional") != 1.0:
        fail("conditional exact-class-set accuracy changed")
    detected = sum(row.get("detection_rate_hit") is True for row in rows)
    expected_hamming = (len(rows) - detected) / (len(rows) * 6)
    if abs(float(class_set.get("hamming_loss", -1)) - expected_hamming) > 1e-12:
        fail("class-set Hamming loss is inconsistent with the misses")
    if any(item["fp"] != 0 or item["precision"] != 1.0 for item in class_set.get("per_component", [])):
        fail("unexpected class-set false positive")

    repeated_table = (RESULTS / "table_repeated.tex").read_text()
    cadence_table = (RESULTS / "table_cadence.tex").read_text()
    control_table = (RESULTS / "table_controls.tex").read_text()
    class_set_table = (RESULTS / "table_class_set_metrics.tex").read_text()
    if r"S6 & \{intent, auth.\}" not in repeated_table:
        fail("S6 ground-truth set is absent from repeated table")
    if "2.0 & 180 & 99.4 & 100.0" not in cadence_table:
        fail("cadence table does not expose the S1 miss")
    if any(f"{control} &" not in control_table for control in ("C1", "C2", "C3", "C4", "C5", "C6")):
        fail("one or more benign controls are absent from generated table")
    if any(label not in class_set_table for label in
           ("configuration", "policy", "authorization", "intent", "evidence", "environment")):
        fail("vector metrics table omits an umbrella component")


def main() -> int:
    document = json.loads((RESULTS / "observations.json").read_text())
    rows = document.get("rows", [])
    if [row.get("scenario") for row in rows] != list(EXPECTED):
        fail("observations.json must contain S1--S9 exactly once and in order")

    for row in rows:
        scenario = row["scenario"]
        expected_class, expected_tier, expected_verdict = EXPECTED[scenario]
        if row.get("expected_class") != expected_class:
            fail(f"{scenario}: unexpected expected_class")
        if row.get("evaluator_tier") != expected_tier:
            fail(f"{scenario}: unexpected evaluator_tier")
        if row.get("observed_class") != expected_class:
            fail(f"{scenario}: observed class does not match expectation")
        if row.get("observed_verdict") != expected_verdict:
            fail(f"{scenario}: unexpected verdict")
        if row.get("baseline_consistent") is not True or row.get("correct") is not True:
            fail(f"{scenario}: baseline or correctness invariant failed")
        try:
            latency = float(row["latency_seconds"])
        except (KeyError, TypeError, ValueError):
            fail(f"{scenario}: invalid latency")
        if latency < 0:
            fail(f"{scenario}: latency must be non-negative")

    with (RESULTS / "observations.csv").open(newline="") as handle:
        csv_rows = list(csv.DictReader(handle))
    json_projection = [
        {key: str(value) for key, value in row.items()} for row in rows
    ]
    if csv_rows != json_projection:
        fail("observations.csv is not the exact row projection of observations.json")

    platform = json.loads((RESULTS / "platforms.json").read_text())
    if platform != document.get("platform"):
        fail("platforms.json differs from the embedded platform snapshot")
    if "v1.36.1" not in platform.get("kubernetes_server", ""):
        fail("unexpected Kubernetes version")
    if not any("source-controller:v1.9.4" in image for image in platform.get("flux_images", [])):
        fail("expected Flux source-controller v1.9.4 is absent")
    if not any("kyverno:v1.18.2" in image for image in platform.get("kyverno_images", [])):
        fail("expected Kyverno v1.18.2 is absent")
    if platform.get("design") != "one bounded execution per scenario; no prevalence or reliability estimate":
        fail("experimental-design scope statement changed")

    table = (RESULTS / "table_lab.tex").read_text()
    for row in rows:
        observed = row["observed_class"] + (" (U)" if row["observed_verdict"] == "undecidable" else "")
        cells = (
            row["scenario"],
            row["injection"],
            row["expected_class"],
            observed,
            f'{float(row["latency_seconds"]):.2f}',
            "Yes",
        )
        if " & ".join(cells) + r" \\" not in table:
            fail(f"{row['scenario']}: generated LaTeX row is missing or inconsistent")

    verify_repeated()
    print(
        "PASS: frozen bounded and repeated live-lab results are internally "
        "consistent (provisional sequential-snapshot class-set scoring; no benign substantive alarms)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
