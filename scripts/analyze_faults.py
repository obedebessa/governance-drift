#!/usr/bin/env python3
"""Validate and summarize the controlled localhost TCP fault campaign."""

from __future__ import annotations

import argparse
import csv
import json
import statistics
from collections import defaultdict
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_RESULTS = ROOT / "lab" / "results_faults"
EXPECTED_PROFILES = (
    "baseline",
    "delay_jitter",
    "record_drop",
    "duplicate",
    "reorder",
    "stale_replay",
    "outage",
    "delay_drop",
    "dependency_masking",
)
FAULT_LAYER = {
    "baseline": "none",
    "delay_jitter": "delay+jitter",
    "record_drop": "record loss",
    "duplicate": "record duplicate",
    "reorder": "record reorder",
    "stale_replay": "record replay",
    "outage": "TCP outage+retry",
    "delay_drop": "delay+record loss",
    "dependency_masking": "gateway validation",
}
COMPONENTS = {
    "configuration", "policy", "authorization", "intent", "environment",
}
MANDATORY_PROPERTIES = {
    "baseline": {"safety", "local_masking", "recovered"},
    "delay_jitter": {"jitter_schedule_exercised", "safety", "local_masking", "recovered"},
    "record_drop": {"record_loss_distinct_from_tcp_retry", "safety", "local_masking", "recovered"},
    "duplicate": {"duplicate_idempotence", "safety", "local_masking", "recovered"},
    "reorder": {"non_regression", "safety", "local_masking", "recovered"},
    "stale_replay": {"stale_replay_does_not_refresh", "safety", "local_masking", "recovered"},
    "outage": {"tcp_retry_preserves_record", "safety", "local_masking", "recovered"},
    "delay_drop": {"delay_then_drop_exercised", "safety", "local_masking", "recovered"},
    "dependency_masking": {"invalid_hash_rejected", "safety", "local_masking", "recovered"},
}


def tex(value: str) -> str:
    return value.replace("_", r"\_").replace("+", r"$+$")


def require(condition: bool, message: str) -> None:
    if not condition:
        raise SystemExit(f"FAULT CAMPAIGN INVALID: {message}")


def load_records(path: Path) -> list[dict[str, Any]]:
    records = []
    for number, line in enumerate(path.read_text().splitlines(), 1):
        try:
            records.append(json.loads(line))
        except json.JSONDecodeError as exc:
            raise SystemExit(f"invalid NDJSON line {number}: {exc}") from exc
    return records


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--results", type=Path, default=DEFAULT_RESULTS)
    args = parser.parse_args()
    metadata = json.loads((args.results / "campaign_metadata.json").read_text())
    records = load_records(args.results / "fault_events.ndjson")
    require("not production" in metadata.get("scope", ""), "scope must reject production inference")
    require(tuple(metadata.get("profiles", [])) == EXPECTED_PROFILES, "profile set/order changed")

    by_profile: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in records:
        by_profile[row["profile"]].append(row)
    require(tuple(by_profile) == EXPECTED_PROFILES, "raw profile set/order changed")

    summaries: list[dict[str, Any]] = []
    oracle_by_profile: dict[str, dict[str, Any]] = {}
    for profile in EXPECTED_PROFILES:
        rows = by_profile[profile]
        ordinals = [row.get("ordinal") for row in rows]
        require(
            ordinals == list(range(1, len(rows) + 1)),
            f"{profile}: raw events are not in complete chronological ordinal order",
        )
        evaluations = [row for row in rows if row["record_type"] == "evaluation"]
        properties = [row for row in rows if row["record_type"] == "property"]
        transport = [row for row in rows if row["record_type"] == "transport"]
        require(evaluations, f"{profile}: no evaluations")

        property_names = [row.get("property") for row in properties]
        require(len(property_names) == len(set(property_names)), f"{profile}: duplicate property record")
        require(
            all(type(row.get("property_ok")) is bool for row in properties),
            f"{profile}: property_ok must be boolean",
        )
        property_map = {row["property"]: row["property_ok"] for row in properties}
        missing_properties = MANDATORY_PROPERTIES[profile].difference(property_map)
        require(not missing_properties, f"{profile}: missing mandatory properties {sorted(missing_properties)}")

        recomputed_safety: list[bool] = []
        recomputed_masking: list[bool] = []
        for row in evaluations:
            components = row.get("components")
            mask = row.get("mask")
            require(isinstance(components, dict), f"{profile}: missing raw component verdicts")
            require(isinstance(mask, dict), f"{profile}: missing raw component mask")
            require(set(components) == COMPONENTS, f"{profile}: component set changed")
            require(set(mask) == COMPONENTS, f"{profile}: mask set changed")
            require(
                all(value in {"consistent", "inconsistent", "undecidable"} for value in components.values()),
                f"{profile}: invalid component verdict",
            )
            require(all(type(value) is bool for value in mask.values()), f"{profile}: non-boolean mask")
            expected = set(row.get("expected_undecidable", []))
            require(expected <= COMPONENTS, f"{profile}: unknown expected component")
            actual = {name for name, value in components.items() if value == "undecidable"}
            derived_verdict = (
                "inconsistent" if any(value == "inconsistent" for value in components.values())
                else "undecidable" if actual
                else "consistent"
            )
            mask_matches_components = all(
                mask[name] is (components[name] != "undecidable") for name in COMPONENTS
            )
            safety_ok = all(components[name] == "undecidable" for name in expected)
            masking_ok = actual == expected and mask_matches_components
            require(row.get("gateway_verdict") == derived_verdict, f"{profile}: aggregate verdict mismatch")
            require(set(row.get("actual_undecidable", [])) == actual, f"{profile}: raw actual mask mismatch")
            require(type(row.get("safety_ok")) is bool, f"{profile}: missing claimed safety boolean")
            require(type(row.get("local_masking_ok")) is bool, f"{profile}: missing claimed masking boolean")
            require(row["safety_ok"] == safety_ok, f"{profile}: claimed safety disagrees with oracle")
            require(row["local_masking_ok"] == masking_ok, f"{profile}: claimed mask disagrees with oracle")
            recomputed_safety.append(safety_ok)
            recomputed_masking.append(masking_ok)

        safety_rows = [row for row in evaluations if row.get("expected_undecidable")]
        safety_violations = sum(
            not recomputed_safety[index]
            for index, row in enumerate(evaluations)
            if row.get("expected_undecidable")
        )
        masking_failures = sum(not value for value in recomputed_masking)

        evaluation_by_step = {row["step"]: row for row in evaluations}
        require(
            len(evaluation_by_step) == len(evaluations),
            f"{profile}: duplicate evaluation step",
        )
        recovery_measurements: list[dict[str, float]] = []
        for recovery in evaluations:
            recovery_of = recovery.get("recovery_of")
            if not recovery_of:
                continue
            require(recovery_of in evaluation_by_step, f"{profile}: recovery references missing fault")
            fault = evaluation_by_step[recovery_of]
            require(fault["ordinal"] < recovery["ordinal"], f"{profile}: recovery precedes fault")
            require(recovery["gateway_verdict"] == "consistent", f"{profile}: linked recovery is not consistent")
            between = [
                row for row in evaluations
                if fault["ordinal"] < row["ordinal"] <= recovery["ordinal"]
                and row["gateway_verdict"] == "consistent"
            ]
            require(between and between[0]["ordinal"] == recovery["ordinal"], f"{profile}: recovery is not first consistent evaluation")
            wall_delta = round(
                float(recovery["wall_elapsed_ms"]) - float(fault["wall_elapsed_ms"]),
                3,
            )
            logical_delta = round(
                float(recovery["logical_time"]) - float(fault["logical_time"]),
                6,
            )
            require(wall_delta >= 0.0 and logical_delta >= 0.0, f"{profile}: negative recovery latency")
            recovery_measurements.append({"wall_ms": wall_delta, "logical_seconds": logical_delta})

        wall = [row["wall_ms"] for row in recovery_measurements]
        logical = [row["logical_seconds"] for row in recovery_measurements]
        mandatory_properties_ok = all(property_map[name] for name in MANDATORY_PROPERTIES[profile])
        all_declared_properties = mandatory_properties_ok and all(property_map.values())
        recovered_property = (
            evaluations[-1]["gateway_verdict"] == "consistent"
            and all(
                evaluation_by_step[row["recovery_of"]]["gateway_verdict"] != "consistent"
                for row in evaluations if row.get("recovery_of")
            )
        )
        item = {
            "profile": profile,
            "fault_layer": FAULT_LAYER[profile],
            "target_stream": evaluations[0].get("target_stream", "none") if evaluations else "none",
            "evaluations": len(evaluations),
            "safety_checks": len(safety_rows),
            "safety_violations": safety_violations,
            "local_masking_failures": masking_failures,
            "transport_attempts": sum(row["event_type"] == "transport_attempt" for row in transport),
            "transport_failures": sum(row["event_type"] == "transport_failure" for row in transport),
            "transport_retries": sum(row["event_type"] == "transport_retry" for row in transport),
            "application_record_drops": sum(row["event_type"] == "record_drop" for row in transport),
            "duplicate_emits": sum(row["event_type"] == "duplicate_emit" for row in transport),
            "reordered_emits": sum(row["event_type"] == "reordered_emit" for row in transport),
            "stale_replays": sum(row["event_type"] == "stale_replay" for row in transport),
            "invalid_rejections": sum(
                row["event_type"] == "gateway_ingest" and row.get("status") == "invalid_rejected"
                for row in transport
            ),
            "recovery_events": len(recovery_measurements),
            "recovery_logical_max_seconds": round(max(logical), 6) if logical else 0.0,
            "recovery_wall_median_ms": round(statistics.median(wall), 3) if wall else 0.0,
            "safety_property": safety_violations == 0,
            "local_masking_property": masking_failures == 0,
            "recovered_property": recovered_property,
            "mandatory_properties_present": not missing_properties,
            "all_declared_properties": all_declared_properties,
        }
        item["outcome"] = "PASS" if (
            safety_violations == 0
            and masking_failures == 0
            and item["all_declared_properties"]
            and item["recovered_property"]
        ) else "FAIL"
        summaries.append(item)
        oracle_by_profile[profile] = {
            "rows": rows,
            "evaluations": evaluations,
            "transport": transport,
            "properties": property_map,
            "recovery_measurements": recovery_measurements,
        }

    summary_by_name = {row["profile"]: row for row in summaries}
    require(summary_by_name["outage"]["transport_failures"] == 1, "outage must fail one TCP attempt")
    require(summary_by_name["outage"]["transport_retries"] == 1, "outage must retry the same record")
    require(summary_by_name["outage"]["application_record_drops"] == 0, "TCP outage cannot be labeled record loss")
    require(summary_by_name["record_drop"]["application_record_drops"] == 1, "record-drop profile did not lose a record")
    require(summary_by_name["record_drop"]["transport_failures"] == 0, "record loss was mislabeled as TCP failure")
    require(summary_by_name["duplicate"]["duplicate_emits"] == 1, "duplicate profile missing duplicate delivery")
    require(summary_by_name["reorder"]["reordered_emits"] == 1, "reorder profile missing reversed delivery")
    require(summary_by_name["stale_replay"]["stale_replays"] == 1, "stale replay was not exercised")
    jitter_schedule = metadata.get("delay_jitter_schedule_ms", [])
    require(len(jitter_schedule) >= 3, "delay+jitter schedule is too short")
    require(len(set(jitter_schedule)) >= 3, "delay+jitter schedule has no meaningful dispersion")
    expected_recovery_counts = {
        "baseline": 0,
        "delay_jitter": len(jitter_schedule),
        "record_drop": 1,
        "duplicate": 0,
        "reorder": 0,
        "stale_replay": 1,
        "outage": 1,
        "delay_drop": 2,
        "dependency_masking": 1,
    }
    expected_safety_checks = {
        "baseline": 0,
        "delay_jitter": len(jitter_schedule),
        "record_drop": 1,
        "duplicate": 0,
        "reorder": 0,
        "stale_replay": 1,
        "outage": 1,
        "delay_drop": 2,
        "dependency_masking": 1,
    }
    for profile in EXPECTED_PROFILES:
        require(
            summary_by_name[profile]["recovery_events"] == expected_recovery_counts[profile],
            f"{profile}: mandatory recovery evidence count changed",
        )
        require(
            summary_by_name[profile]["safety_checks"] == expected_safety_checks[profile],
            f"{profile}: mandatory safety check count changed",
        )
    require(summary_by_name["delay_drop"]["application_record_drops"] == 1, "delay+drop did not drop a record")
    require(summary_by_name["dependency_masking"]["invalid_rejections"] == 1, "invalid hash was not rejected")

    gateway_events = [
        row for profile in EXPECTED_PROFILES
        for row in oracle_by_profile[profile]["transport"]
        if row.get("event_type") == "gateway_ingest"
    ]
    require(gateway_events, "no gateway delivery events")
    require(
        all(row.get("wire_delivered_at") is None for row in gateway_events),
        "sender supplied a delivery timestamp",
    )
    require(
        all(row.get("delivery_source") == "gateway_receiver_clock" for row in gateway_events),
        "delivered_at was not stamped by the gateway receiver clock",
    )

    duplicate_statuses = [
        row.get("status") for row in oracle_by_profile["duplicate"]["transport"]
        if row.get("event_type") == "gateway_ingest"
        and row.get("stream") == "authorization"
        and row.get("sequence") == 2
    ]
    require(
        duplicate_statuses == ["accepted", "duplicate_ignored"],
        "duplicate idempotence oracle failed",
    )

    reorder_statuses = [
        (row.get("sequence"), row.get("status"))
        for row in oracle_by_profile["reorder"]["transport"]
        if row.get("event_type") == "gateway_ingest"
        and row.get("stream") == "policy"
        and row.get("sequence") in {2, 3}
    ]
    require(
        reorder_statuses == [(3, "accepted"), (2, "reordered_ignored")],
        "reorder non-regression oracle failed",
    )

    replay_statuses = [
        row.get("status") for row in oracle_by_profile["stale_replay"]["transport"]
        if row.get("event_type") == "gateway_ingest"
        and row.get("stream") == "inventory"
        and row.get("sequence") == 1
    ]
    require(
        replay_statuses == ["accepted", "duplicate_ignored"],
        "stale replay did not exercise sequence idempotence",
    )
    replay_fault = next(
        row for row in oracle_by_profile["stale_replay"]["evaluations"]
        if row["step"] == "old_sequence_replayed_without_refreshing_capture_time"
    )
    require(
        replay_fault["streams"]["inventory"]["captured_at"] == 0.0
        and replay_fault["streams"]["inventory"]["sequence"] == 1,
        "stale replay refreshed accepted evidence",
    )

    outage_rows = oracle_by_profile["outage"]["transport"]
    outage_received = next(
        row for row in outage_rows
        if row.get("event_type") == "relay_received"
        and row.get("stream") == "authorization"
        and row.get("sequence") == 2
    )
    outage_retry = next(row for row in outage_rows if row.get("event_type") == "transport_retry")
    require(
        outage_retry.get("same_payload_hash") == outage_received.get("payload_hash"),
        "TCP retry did not preserve the unchanged envelope hash",
    )

    dropped_policy_deliveries = [
        row for row in oracle_by_profile["record_drop"]["transport"]
        if row.get("event_type") == "gateway_ingest"
        and row.get("stream") == "policy"
        and row.get("sequence") == 2
    ]
    require(not dropped_policy_deliveries, "application record drop reached the gateway")

    delay_events = [
        row for row in oracle_by_profile["delay_jitter"]["transport"]
        if row.get("event_type") == "record_delay"
    ]
    require(
        [row.get("delay_ms") for row in delay_events] == jitter_schedule,
        "observed jitter events differ from deterministic schedule",
    )

    for profile in ("delay_jitter", "outage"):
        rows = oracle_by_profile[profile]["rows"]
        for recovery in (
            row for row in oracle_by_profile[profile]["evaluations"]
            if row.get("recovery_of")
        ):
            fault = next(row for row in rows if row.get("step") == recovery["recovery_of"])
            relevant_faults = [
                row for row in rows
                if fault["ordinal"] > row["ordinal"]
                and row.get("event_type") in {"record_delay", "transport_failure"}
            ]
            require(relevant_faults, f"{profile}: linked fault was not recorded before evaluation")
            require(fault["ordinal"] < recovery["ordinal"], f"{profile}: recovery chronology invalid")

    require(all(row["outcome"] == "PASS" for row in summaries), "one or more safety properties failed")

    with (args.results / "fault_profile_summary.csv").open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(summaries[0]), lineterminator="\n")
        writer.writeheader()
        writer.writerows(summaries)

    summary = {
        "campaign": metadata["campaign"],
        "scope": metadata["scope"],
        "seed": metadata["seed"],
        "profiles": summaries,
        "aggregate": {
            "profiles": len(summaries),
            "profiles_passed": sum(row["outcome"] == "PASS" for row in summaries),
            "safety_checks": sum(row["safety_checks"] for row in summaries),
            "safety_violations": sum(row["safety_violations"] for row in summaries),
            "local_masking_failures": sum(row["local_masking_failures"] for row in summaries),
            "transport_failures": sum(row["transport_failures"] for row in summaries),
            "transport_retries": sum(row["transport_retries"] for row in summaries),
            "application_record_drops": sum(row["application_record_drops"] for row in summaries),
        },
        "interpretation": (
            "Controlled localhost transport validates gateway safety contracts only; it does "
            "not estimate production network reliability or latency. delivered_at is stamped "
            "by the gateway's controlled receiver clock. Safety and masking are recomputed "
            "from raw component verdicts; monotonic wall recovery is derived from chronological "
            "fault/recovery evaluations and remains descriptive. TCP retry and application-record "
            "loss are counted separately."
        ),
    }
    (args.results / "fault_summary.json").write_text(json.dumps(summary, indent=2) + "\n")

    safety_lines = [
        "% Generated by scripts/analyze_faults.py",
        r"\begin{tabular}{@{}lllrrrr@{}}",
        r"\toprule",
        r"Profile & Fault layer & Target & Checks & Viol. & Mask fail & Outcome \\",
        r"\midrule",
    ]
    for row in summaries:
        safety_lines.append(
            f"{tex(row['profile'])} & {tex(row['fault_layer'])} & {tex(row['target_stream'])} & "
            f"{row['safety_checks']} & {row['safety_violations']} & "
            f"{row['local_masking_failures']} & {row['outcome']} \\\\"
        )
    safety_lines += [r"\bottomrule", r"\end{tabular}"]
    (args.results / "table_fault_safety.tex").write_text("\n".join(safety_lines) + "\n")

    recovery_lines = [
        "% Generated by scripts/analyze_faults.py",
        r"\begin{tabular}{@{}lrrrrrr@{}}",
        r"\toprule",
        r"Profile & TCP fail & Retry & Record loss & Recovery & Logical max & Wall median \\",
        r" & & & & events & (s) & (ms) \\",
        r"\midrule",
    ]
    for row in summaries:
        recovery_lines.append(
            f"{tex(row['profile'])} & {row['transport_failures']} & {row['transport_retries']} & "
            f"{row['application_record_drops']} & {row['recovery_events']} & "
            f"{row['recovery_logical_max_seconds']:.2f} & {row['recovery_wall_median_ms']:.2f} \\\\"
        )
    recovery_lines += [r"\bottomrule", r"\end{tabular}"]
    (args.results / "table_fault_recovery.tex").write_text("\n".join(recovery_lines) + "\n")

    readme = """# Controlled localhost evidence-transport fault campaign

This directory contains deterministic record schedules executed through two
real TCP hops on `127.0.0.1`. It validates fail-closed evidence-envelope
contracts, not production network reliability, throughput, or latency.

The sender emits `delivered_at=null`; the gateway stamps `delivered_at` from a
controlled receiver clock. Raw records carry per-profile ordinals in actual
audit order. Fault schedules, logical times, states, and verdicts are
deterministic. `wall_elapsed_ms` remains an observed monotonic-clock value and
is intentionally not deterministic or a production estimate.

`transport_failure`/`transport_retry` denote failed TCP delivery followed by
retry of the unchanged envelope. `record_drop` denotes loss of a complete
application record inside the relay; TCP retransmission cannot recover it.

Recovery latency is derived uniformly from a fault evaluation to its linked
first consistent evaluation. Logical deltas are deterministic; wall-clock
medians report the available descriptive sample count shown in the table.

The analyzer independently recomputes aggregate verdicts, component-local
masks, mandatory properties, replay/non-regression, duplicate idempotence, and
retry hash preservation from the raw trace; it exits nonzero on disagreement.

- `fault_events.ndjson`: raw evaluations, declared properties, and transport events.
- `fault_observations.csv`: raw component-mask observations.
- `fault_profile_summary.csv`: one validated row per profile.
- `fault_summary.json`: aggregate checks and scope boundary.
- `table_fault_safety.tex`, `table_fault_recovery.tex`: generated manuscript tables.
"""
    (args.results / "README.md").write_text(readme)
    print(json.dumps(summary["aggregate"], indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
