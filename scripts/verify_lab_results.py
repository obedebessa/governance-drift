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
        key = (repeat, scenario, cadence)
        seen[key] = seen.get(key, 0) + 1
        detected = row.get("detection_rate_hit") is True
        if detected:
            if row.get("classification_correct") is not True:
                fail(f"{key}: detected verdict is misclassified")
            if row.get("observed_class") not in REPEATED_EXPECTED[scenario].split("|"):
                fail(f"{key}: observed class is outside the ground-truth set")
        else:
            misses.add((repeat, scenario, cadence))
            if row.get("observed_verdict") != "timeout":
                fail(f"{key}: non-detection must be recorded as timeout")
        for field in ("actuation_seconds", "ddl_seconds", "end_to_end_seconds", "tte_seconds"):
            if float(row[field]) < 0:
                fail(f"{key}: negative {field}")
    if len(seen) != 540 or any(count != 1 for count in seen.values()):
        fail("repeated positive keys are not unique and complete")
    expected_misses = {(19, "S1", "2.0"), (19, "S1", "10.0")}
    if misses != expected_misses:
        fail(f"unexpected frozen miss set: {sorted(misses)}")

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
    with (RESULTS / "control_observations.csv").open(newline="") as handle:
        csv_controls = list(csv.DictReader(handle))
    control_projection = [{key: str(value) for key, value in row.items()} for row in controls]
    if csv_controls != control_projection:
        fail("control_observations.csv differs from JSON control rows")

    platform = document.get("platform", {})
    if platform.get("evaluator_cadences_seconds") != [0.5, 2.0, 10.0]:
        fail("repeated-study platform cadence metadata changed")
    if not str(platform.get("design", "")).startswith("20 repetitions per scenario"):
        fail("repeated-study design metadata changed")

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
    if any(row["classification_accuracy"] != 1.0 for row in cadence.values()):
        fail("conditional classification accuracy changed")

    repeated_table = (RESULTS / "table_repeated.tex").read_text()
    cadence_table = (RESULTS / "table_cadence.tex").read_text()
    control_table = (RESULTS / "table_controls.tex").read_text()
    if r"S6 & \{intent, auth.\}" not in repeated_table:
        fail("S6 ground-truth set is absent from repeated table")
    if "2.0 & 180 & 99.4 & 100.0" not in cadence_table:
        fail("cadence table does not expose the S1 miss")
    if any(f"{control} &" not in control_table for control in ("C1", "C2", "C3", "C4", "C5", "C6")):
        fail("one or more benign controls are absent from generated table")


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
        "consistent (538/540 detections; 0/543 benign-control poll alarms)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
