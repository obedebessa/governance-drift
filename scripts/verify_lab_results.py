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


def fail(message: str) -> None:
    raise SystemExit(f"FAIL: {message}")


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

    print("PASS: frozen live-lab results are internally consistent (9/9 correct)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
