#!/usr/bin/env python3
"""Verify hashes, timelines, process independence, and denominators in trace results."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import statistics
from pathlib import Path
from typing import Any


GENESIS_HASH = "0" * 64
CADENCES = (1.0, 5.0, 10.0)
EXPECTED = {
    "S1": ["configuration"],
    "S2": ["authorization"],
    "S3": ["policy"],
    "S4": ["authorization"],
    "S5": ["environment"],
    "S6": ["authorization", "intent"],
    "S7": ["environment"],
    "S8": ["authorization"],
    "S9": ["evidence"],
}
EXPECTED_FINAL_COMPONENTS = {
    "S1": {"configuration": "inconsistent", "policy": "consistent", "authorization": "consistent", "intent": "consistent", "environment": "consistent"},
    "S2": {"configuration": "consistent", "policy": "consistent", "authorization": "inconsistent", "intent": "consistent", "environment": "consistent"},
    "S3": {"configuration": "consistent", "policy": "inconsistent", "authorization": "consistent", "intent": "consistent", "environment": "consistent"},
    "S4": {"configuration": "consistent", "policy": "consistent", "authorization": "inconsistent", "intent": "consistent", "environment": "consistent"},
    "S5": {"configuration": "consistent", "policy": "consistent", "authorization": "consistent", "intent": "consistent", "environment": "inconsistent"},
    "S6": {"configuration": "consistent", "policy": "consistent", "authorization": "inconsistent", "intent": "inconsistent", "environment": "consistent"},
    "S7": {"configuration": "consistent", "policy": "consistent", "authorization": "consistent", "intent": "consistent", "environment": "inconsistent"},
    "S8": {"configuration": "consistent", "policy": "consistent", "authorization": "inconsistent", "intent": "consistent", "environment": "consistent"},
    "S9": {"configuration": "consistent", "policy": "consistent", "authorization": "undecidable", "intent": "consistent", "environment": "consistent"},
}


def canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")


def sha256_value(value: Any) -> str:
    return hashlib.sha256(canonical_bytes(value)).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise AssertionError(f"expected object in {path}")
    return value


def load_ndjson(path: Path) -> list[dict[str, Any]]:
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]
    if not rows:
        raise AssertionError(f"empty observer log: {path}")
    return rows


def exact(row: dict[str, Any], expected: list[str]) -> bool:
    verdict = "undecidable" if expected == ["evidence"] else "drift"
    return row.get("verdict") == verdict and sorted(row.get("class_set", [])) == sorted(expected)


def markers(rows: list[dict[str, Any]], cause: float, expected: list[str]) -> dict[str, dict[str, Any]]:
    post = [row for row in rows if float(row["completed_mono"]) >= cause]
    first_alert = next((row for row in post if row["verdict"] != "consistent"), None)
    first_exact = next((row for row in post if exact(row, expected)), None)
    stable = next(
        (current for previous, current in zip(post, post[1:]) if exact(previous, expected) and exact(current, expected)),
        None,
    )
    if not first_alert or not first_exact or not stable:
        raise AssertionError("trajectory lacks first-alert, exact, or stable marker")
    return {"first_alert": first_alert, "first_exact": first_exact, "stable_exact": stable}


def verify_poll_chain(path: Path, ready: dict[str, Any]) -> list[dict[str, Any]]:
    rows = load_ndjson(path)
    previous = GENESIS_HASH
    for sequence, row in enumerate(rows, 1):
        assert row["schema"] == "govdrift-trace-poll/v1", path
        assert row["sequence"] == sequence, (path, row["sequence"], sequence)
        assert row["observer_id"] == ready["observer_id"], path
        assert row["pid"] == ready["pid"], path
        assert float(row["cadence_seconds"]) == float(ready["cadence_seconds"]), path
        assert row["previous_poll_sha256"] == previous, path
        unsigned = dict(row)
        observed_hash = unsigned.pop("poll_sha256")
        assert observed_hash == sha256_value(unsigned), (path, sequence)
        previous = observed_hash
        evaluation = row["evaluation"]
        assert row["input_fingerprint_sha256"] == evaluation["input_fingerprint_sha256"], path
        assert evaluation["input_fingerprint_sha256"] == sha256_value(evaluation["inputs"]), path
        assert float(row["actual_start_mono"]) >= float(row["scheduled_mono"]), path
        assert float(row["completed_mono"]) >= float(row["actual_start_mono"]), path
        assert not row.get("adapter_error"), (path, sequence, row.get("adapter_error"))
    return rows


def verify_manifest(root: Path) -> None:
    manifest = root / "manifest.sha256"
    entries = {}
    for line in manifest.read_text(encoding="utf-8").splitlines():
        digest, relative = line.split("  ", 1)
        if relative in entries:
            raise AssertionError(f"duplicate manifest entry: {relative}")
        entries[relative] = digest
    actual = {
        str(path.relative_to(root)): sha256_file(path)
        for path in root.rglob("*")
        if path.is_file() and path.name != "manifest.sha256"
    }
    assert entries == actual, "manifest does not exactly cover campaign files"


def verify_campaign(root: Path) -> dict[str, Any]:
    if (root / "failure.json").exists():
        raise AssertionError(f"campaign contains failure.json: {root / 'failure.json'}")
    campaign = load_json(root / "campaign.json")
    summary = load_json(root / "campaign_summary.json")
    cleanup = load_json(root / "cleanup.json")
    assert campaign["campaign_id"] == summary["campaign_id"]
    assert summary["scenario_ids"] == list(EXPECTED)
    assert [float(value) for value in summary["cadences_seconds"]] == list(CADENCES)
    assert summary["experimental_unit"] == "one injected scenario episode"
    assert summary["experimental_units_injected_episodes"] == 9
    assert summary["observational_unit"] == "one scenario-by-observer-cadence trajectory"
    assert summary["observational_units_trajectories"] == 27
    assert cleanup["namespace_absent"] and cleanup["container_absent"]
    assert cleanup["temporary_runtime_removed"] and not cleanup["errors"]

    all_pids: set[tuple[str, int]] = set()
    trajectory_count = 0
    exact_count = 0
    stable_count = 0
    verified_trajectory_rows: list[dict[str, Any]] = []
    verified_scenario_projections: list[dict[str, Any]] = []
    injection_command_sequences: dict[str, list[int]] = {}
    for scenario, expected in EXPECTED.items():
        injection = load_json(root / "injections" / f"{scenario}.json")
        recorded = injection.pop("record_sha256")
        assert recorded == sha256_value(injection), scenario
        injection["record_sha256"] = recorded
        assert injection["injection_id"].startswith(f"{campaign['campaign_id']}:{scenario}:")
        assert sorted(injection["expected_class_set"]) == sorted(expected)
        injection_command_sequences[scenario] = injection["command_sequences"]
        assert float(injection["effect_observed_mono"]) >= float(injection["cause_started_mono"])
        assert injection["before_input_fingerprint_sha256"] == sha256_value(
            injection["before_evaluation"]["inputs"]
        )
        assert injection["after_input_fingerprint_sha256"] == sha256_value(
            injection["after_effect_evaluation"]["inputs"]
        )

        scenario_summary = load_json(root / "summaries" / f"{scenario}.json")
        assert scenario_summary["injection_sha256"] == recorded
        assert scenario_summary["injection_id"] == injection["injection_id"]
        assert scenario_summary["observer_processes"] == 3
        ready_rows = []
        scenario_pids = set()
        scenario_ids = set()
        for cadence in CADENCES:
            label = f"{int(cadence):02d}s"
            ready = load_json(root / "raw" / scenario / f"observer-{label}.ready.json")
            path = root / "raw" / scenario / f"observer-{label}.ndjson"
            assert float(ready["cadence_seconds"]) == cadence
            rows = verify_poll_chain(path, ready)
            assert any(
                float(row["completed_mono"]) < float(injection["cause_started_mono"])
                and row["verdict"] == "consistent"
                for row in rows
            ), (scenario, cadence, "missing pre-cause consistent poll")
            observed = markers(rows, float(injection["cause_started_mono"]), expected)
            reported = next(
                row for row in scenario_summary["trajectories"]
                if float(row["cadence_seconds"]) == cadence
            )
            verified_trajectory_rows.append(reported)
            assert reported["first_alert"]["poll_sha256"] == observed["first_alert"]["poll_sha256"]
            assert reported["first_exact"]["poll_sha256"] == observed["first_exact"]["poll_sha256"]
            assert reported["stable_exact"]["poll_sha256"] == observed["stable_exact"]["poll_sha256"]
            assert float(observed["first_alert"]["completed_mono"]) <= float(observed["first_exact"]["completed_mono"])
            assert float(observed["first_exact"]["completed_mono"]) < float(observed["stable_exact"]["completed_mono"])
            assert exact(observed["first_exact"], expected)
            assert exact(observed["stable_exact"], expected)
            assert observed["first_exact"]["components"] == EXPECTED_FINAL_COMPONENTS[scenario], (
                scenario,
                cadence,
                "first exact component contract",
                observed["first_exact"]["components"],
            )
            assert observed["stable_exact"]["components"] == EXPECTED_FINAL_COMPONENTS[scenario], (
                scenario,
                cadence,
                "two-poll exact component contract",
                observed["stable_exact"]["components"],
            )
            scenario_pids.add(int(ready["pid"]))
            scenario_ids.add(ready["observer_id"])
            all_pids.add((scenario, int(ready["pid"])))
            ready_rows.append(ready)
            trajectory_count += 1
            exact_count += 1
            stable_count += 1
        assert len(scenario_pids) == 3, (scenario, scenario_pids)
        assert len(scenario_ids) == 3, (scenario, scenario_ids)
        assert scenario_summary["distinct_pids"] == 3
        assert scenario_summary["distinct_observer_ids"] == 3
        assert scenario_summary["all_exact"] and scenario_summary["all_stable"]
        verified_scenario_projections.append({
            "scenario": scenario_summary["scenario"],
            "expected_class_set": scenario_summary["expected_class_set"],
            "all_exact": scenario_summary["all_exact"],
            "all_stable": scenario_summary["all_stable"],
            "distinct_pids": scenario_summary["distinct_pids"],
        })

    assert trajectory_count == 27
    assert summary["trajectories"] == summary["expected_trajectories"] == trajectory_count
    assert summary["exact_trajectories"] == exact_count == 27
    assert summary["stable_trajectories"] == stable_count == 27
    assert summary["scenarios"] == summary["scenarios_all_exact"] == 9
    assert summary["distinct_observer_processes_across_windows"] == len(all_pids) == 27
    assert summary["adapter_error_polls"] == 0
    assert summary["latency_aggregation_scope"] == (
        "descriptive pooled across 27 correlated observational trajectories"
    )

    trajectory_rows = json.loads((root / "trajectories.json").read_text(encoding="utf-8"))
    assert len(trajectory_rows) == 27
    assert trajectory_rows == verified_trajectory_rows, (
        "trajectories.json is not the exact ordered concatenation of raw-verified scenario trajectories"
    )
    assert summary["scenario_summaries"] == verified_scenario_projections

    with (root / "trajectories.csv").open(newline="", encoding="utf-8") as handle:
        csv_rows = list(csv.DictReader(handle))
    assert len(csv_rows) == len(trajectory_rows)
    for csv_row, json_row in zip(csv_rows, trajectory_rows):
        assert csv_row["scenario"] == json_row["scenario"]
        assert csv_row["expected_class_set"].split("|") == json_row["expected_class_set"]
        assert float(csv_row["cadence_seconds"]) == float(json_row["cadence_seconds"])
        assert csv_row["observer_id"] == json_row["observer_id"]
        assert int(csv_row["pid"]) == int(json_row["pid"])
        assert csv_row["exact_correct"] == str(json_row["exact_correct"])
    for source_key, summary_key in (
        ("cause_to_first_exact_seconds", "cause_to_first_exact_seconds"),
        ("cause_to_stable_exact_seconds", "cause_to_stable_exact_seconds"),
    ):
        values = sorted(float(row[source_key]) for row in trajectory_rows)
        expected_aggregate = {
            "minimum": min(values),
            "median": statistics.median(values),
            "p95_nearest_rank": values[math.ceil(0.95 * len(values)) - 1],
            "maximum": max(values),
        }
        assert summary[summary_key] == expected_aggregate, (summary_key, summary[summary_key], expected_aggregate)

    refs = load_json(root / "platform.json")["image_references"]
    assert set(refs) == {"python", "baseline", "alternate"}
    assert all("@sha256:" in reference for reference in refs.values())
    baseline = load_json(root / "artifacts/deployment-baseline.json")
    predecessor = load_json(root / "artifacts/deployment-predecessor.json")
    assert baseline["spec"]["template"]["spec"]["containers"][0]["image"] == refs["baseline"]
    assert predecessor["spec"]["template"]["spec"]["containers"][0]["image"] == refs["alternate"]
    mutation = load_json(root / "artifacts/policy-mutation.json")
    mutation_image = mutation["spec"]["rules"][0]["mutate"]["patchStrategicMerge"]["spec"]["containers"][0]["image"]
    assert mutation_image == refs["alternate"]

    source = root / "artifacts/source"
    for scenario in EXPECTED:
        injection = load_json(root / "injections" / f"{scenario}.json")
        assert injection["harness_sha256"] == sha256_file(source / "run_trace_experiment.py")
        assert injection["observer_sha256"] == sha256_file(source / "trace_observer.py")
        assert injection["evaluator_sha256"] == sha256_file(source / "trace_evaluator.py")

    ledger = json.loads((root / "command_ledger.json").read_text(encoding="utf-8"))
    assert [row["sequence"] for row in ledger] == list(range(1, len(ledger) + 1))
    by_sequence = {row["sequence"]: row for row in ledger}
    for scenario, sequences in injection_command_sequences.items():
        assert all(sequence in by_sequence for sequence in sequences), scenario
        assert all(by_sequence[sequence]["returncode"] == 0 for sequence in sequences), scenario
    verify_manifest(root)
    return {
        "campaign_id": campaign["campaign_id"],
        "scenarios": 9,
        "trajectories": trajectory_count,
        "distinct_processes": len(all_pids),
        "polls": sum(
            len(load_ndjson(path)) for path in sorted((root / "raw").glob("S*/observer-*.ndjson"))
        ),
        "manifest_files": len((root / "manifest.sha256").read_text(encoding="utf-8").splitlines()),
        "status": "verified",
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("campaign", type=Path)
    args = parser.parse_args()
    result = verify_campaign(args.campaign.resolve())
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
