#!/usr/bin/env python3
"""Verify transition-inclusive controls from append-only per-poll logs."""

from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
RESULTS = ROOT / "lab/results_transition"


def fail(message: str) -> None:
    raise SystemExit(f"FAIL: {message}")


document = json.loads((RESULTS / "transition_observations.json").read_text())
rows = document.get("rows", [])
summary = document.get("summary", {})
windows = summary.get("windows_metadata", [])
if len(windows) != 20 or summary.get("windows") != 20:
    fail("expected 20 transition-inclusive windows")
if len(rows) != 777 or summary.get("polls") != 777:
    fail("canonical per-poll count changed")
if any(window.get("error") for window in windows):
    fail("one or more control actions failed")

for relative, expected in document.get("raw_sha256", {}).items():
    path = RESULTS / relative
    if not path.is_file():
        fail(f"raw observer log is missing: {relative}")
    observed = hashlib.sha256(path.read_bytes()).hexdigest()
    if observed != expected:
        fail(f"raw observer log hash mismatch: {relative}")
if len(document.get("raw_sha256", {})) != 60:
    fail("expected one raw log per window and cadence")

seen: set[tuple[int, float, int]] = set()
grouped: dict[tuple[int, float], list[dict]] = defaultdict(list)
for row in rows:
    key = (int(row["window"]), float(row["cadence_seconds"]), int(row["sequence"]))
    if key in seen:
        fail(f"duplicate poll identity: {key}")
    seen.add(key)
    grouped[key[:2]].append(row)
    if float(row["scheduler_lag_seconds"]) < 0 or float(row["evaluation_seconds"]) < 0:
        fail(f"negative timing at {key}")
if len(grouped) != 60:
    fail("expected 20 windows x 3 observer cadences")
if any(rows_for_observer[-1]["verdict"] != "consistent" for rows_for_observer in grouped.values()):
    fail("at least one observer did not recover to a consistent final poll")

non_configuration_governance_plane = sum(
    row["verdict"] == "drift"
    and any(label in {"policy", "authorization", "intent", "environment"}
            for label in row.get("class_set", []))
    for row in rows
)
configuration = sum("configuration" in row.get("class_set", []) for row in rows)
epistemic = sum(row["verdict"] == "undecidable" for row in rows)
if non_configuration_governance_plane != summary.get(
    "non_configuration_governance_plane_drift_polls"
):
    fail("non-configuration governance-plane count is not reproducible")
if configuration != summary.get("configuration_convergence_polls"):
    fail("configuration convergence count is not reproducible")
if epistemic != summary.get("epistemic_warning_polls"):
    fail("epistemic warning count is not reproducible")
if (non_configuration_governance_plane, configuration, epistemic) != (0, 7, 36):
    fail("canonical transition outcome changed")
if any(
    "active pod lacks a complete ready-container digest set" not in row.get("detail", "")
    for row in rows if row["verdict"] == "undecidable"
):
    fail("unexpected cause for an epistemic transition warning")

print(
    "PASS: 20 transition-inclusive controls reconstructed from 60 append-only "
    "observer logs (777 polls; 0 non-configuration governance-plane drift, "
    "7 configuration-convergence signals, "
    "36 fail-safe epistemic warnings; every observer recovered)"
)
