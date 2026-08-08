#!/usr/bin/env python3
"""Run the controlled localhost evidence-transport fault campaign.

The campaign uses two real TCP hops on 127.0.0.1. Fault schedules and logical
evidence timestamps are deterministic; measured wall-clock recovery is retained
as a laboratory observation and is not a production latency estimate.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import random
import threading
from pathlib import Path
from typing import Any

from evidence_gateway import (
    AuditLog,
    ControlledReceiverClock,
    EvidenceGateway,
    RelayProfile,
    STREAM_DEPENDENCIES,
    canonical_json,
    make_envelope,
    start_transport,
    tcp_request,
)


SEED = 20260808
SUBJECT = "cluster-a/payments/payments#uid-demo-001"
STREAMS = tuple(sorted({item for values in STREAM_DEPENDENCIES.values() for item in values}))
RESULTS = Path(__file__).resolve().parent / "results_faults"
LAB = Path(__file__).resolve().parent


def consistent_payload(stream: str, sequence: int) -> dict[str, Any]:
    return {"consistent": True, "stream": stream, "version": sequence}


def envelope(stream: str, sequence: int, captured: float) -> dict[str, Any]:
    return make_envelope(
        stream=stream,
        subject=SUBJECT,
        sequence=sequence,
        captured_at=captured,
        delivered_at=None,
        payload=consistent_payload(stream, sequence),
    )


class ProfileRun:
    def __init__(self, name: str, relay_profile: RelayProfile, target_stream: str | None) -> None:
        self.name = name
        self.target_stream = target_stream
        self.gateway = EvidenceGateway(
            expected_subject=SUBJECT,
            max_age_seconds=2.0,
            max_transport_delay_seconds=2.0,
        )
        self.receiver_clock = ControlledReceiverClock()
        self.audit = AuditLog()
        self.audit, self.gateway_server, self.relay = start_transport(
            gateway=self.gateway,
            profile=relay_profile,
            audit=self.audit,
            delivery_clock=self.receiver_clock,
        )
        self.observations: list[dict[str, Any]] = []
        self.properties: list[dict[str, Any]] = []

    def close(self) -> None:
        self.relay.close()
        self.gateway_server.close()

    def send(
        self,
        document: dict[str, Any],
        *,
        observed_delivery_at: float,
    ) -> dict[str, Any]:
        """Deliver with a gateway-owned logical arrival time, absent from the wire."""
        if document.get("delivered_at") is not None:
            raise ValueError("the sender may not declare delivered_at")
        self.receiver_clock.set(observed_delivery_at)
        return tcp_request(self.relay.host, self.relay.port, document, timeout=3.0)

    def seed(self) -> None:
        for stream in STREAMS:
            result = self.send(envelope(stream, 1, 0.0), observed_delivery_at=0.05)
            if result.get("status") != "accepted":
                raise RuntimeError(f"{self.name}: baseline seed failed for {stream}: {result}")

    def refresh_except(self, excluded: str, *, captured: float, sequence: int = 2) -> None:
        for stream in STREAMS:
            if stream == excluded:
                continue
            result = self.send(
                envelope(stream, sequence, captured),
                observed_delivery_at=captured + 0.05,
            )
            if result.get("status") != "accepted":
                raise RuntimeError(f"{self.name}: refresh failed for {stream}: {result}")

    def observe(
        self,
        *,
        step: str,
        now: float,
        expected_undecidable: set[str] | None = None,
        recovery_of: str | None = None,
    ) -> dict[str, Any]:
        expected = set(expected_undecidable or set())
        result = self.gateway.evaluate(now=now)
        actual_undecidable = {
            component for component, verdict in result["components"].items()
            if verdict == "undecidable"
        }
        safety_ok = all(result["components"][component] != "consistent" for component in expected)
        local_masking_ok = actual_undecidable == expected
        row = {
            "record_type": "evaluation",
            "profile": self.name,
            "step": step,
            "logical_time": now,
            "target_stream": self.target_stream or "none",
            "expected_undecidable": sorted(expected),
            "actual_undecidable": sorted(actual_undecidable),
            "gateway_verdict": result["verdict"],
            "components": result["components"],
            "mask": result["mask"],
            "streams": result["streams"],
            "safety_ok": safety_ok,
            "local_masking_ok": local_masking_ok,
            "recovery_of": recovery_of,
        }
        self.observations.append(row)
        self.audit.add("evaluation", **row)
        return row

    def property(self, name: str, ok: bool, **detail: Any) -> None:
        row = {
            "record_type": "property",
            "profile": self.name,
            "property": name,
            "property_ok": bool(ok),
            **detail,
        }
        self.properties.append(row)
        self.audit.add("property", **row)

    def records(self) -> list[dict[str, Any]]:
        return [
            {
                "record_type": item.get("record_type", "transport"),
                "profile": self.name,
                **item,
            }
            for item in self.audit.snapshot()
        ]


def wait_and_observe_during_fault(
    run: ProfileRun,
    *,
    document: dict[str, Any],
    wait_event: str,
    step: str,
    now: float,
    expected: set[str],
    observed_delivery_at: float,
) -> dict[str, Any]:
    outcome: dict[str, Any] = {}

    def sender() -> None:
        outcome["result"] = run.send(
            document,
            observed_delivery_at=observed_delivery_at,
        )

    thread = threading.Thread(target=sender)
    thread.start()
    run.audit.wait_for(
        lambda item: item.get("event_type") == wait_event
        and item.get("stream") == document["stream"]
        and int(item.get("sequence", -1)) == int(document["sequence"]),
        timeout=2.0,
    )
    run.observe(step=step, now=now, expected_undecidable=expected)
    thread.join(timeout=3.0)
    if thread.is_alive():
        raise TimeoutError(f"{run.name}: faulted sender did not finish")
    return outcome["result"]


def profile_baseline() -> list[dict[str, Any]]:
    run = ProfileRun("baseline", RelayProfile("baseline"), None)
    try:
        run.seed()
        row = run.observe(step="all_streams_fresh", now=0.1)
        run.property("safety", row["safety_ok"])
        run.property("local_masking", row["local_masking_ok"])
        run.property("recovered", row["gateway_verdict"] == "consistent")
        return run.records()
    finally:
        run.close()


def profile_delay_jitter(delay_schedule_ms: list[int]) -> list[dict[str, Any]]:
    profile = RelayProfile(
        "delay_jitter",
        delay_ms={
            ("policy", sequence): delay
            for sequence, delay in enumerate(delay_schedule_ms, start=2)
        },
    )
    run = ProfileRun("delay_jitter", profile, "policy")
    try:
        run.seed()
        fault_rows: list[dict[str, Any]] = []
        recovery_rows: list[dict[str, Any]] = []
        for trial, delay_ms in enumerate(delay_schedule_ms, start=1):
            sequence = trial + 1
            captured = float(trial * 3)
            pending_step = f"jitter_trial_{trial}_pending_{delay_ms}ms"
            run.refresh_except("policy", captured=captured, sequence=sequence)
            wait_and_observe_during_fault(
                run,
                document=envelope("policy", sequence, captured),
                wait_event="record_delay",
                step=pending_step,
                now=captured + 0.2,
                expected={"policy"},
                observed_delivery_at=captured + 0.4,
            )
            fault_rows.append(run.observations[-1])
            recovery_rows.append(
                run.observe(
                    step=f"jitter_trial_{trial}_recovered",
                    now=captured + 0.4,
                    recovery_of=pending_step,
                )
            )
        run.property(
            "jitter_schedule_exercised",
            len(delay_schedule_ms) >= 3 and len(set(delay_schedule_ms)) >= 3,
            delay_schedule_ms=delay_schedule_ms,
        )
        run.property("safety", all(row["safety_ok"] for row in fault_rows))
        run.property("local_masking", all(row["local_masking_ok"] for row in fault_rows))
        run.property(
            "recovered",
            all(row["gateway_verdict"] == "consistent" for row in recovery_rows),
        )
        return run.records()
    finally:
        run.close()


def profile_record_drop() -> list[dict[str, Any]]:
    profile = RelayProfile("record_drop", drop={("policy", 2)})
    run = ProfileRun("record_drop", profile, "policy")
    try:
        run.seed()
        run.refresh_except("policy", captured=3.0)
        dropped = run.send(
            envelope("policy", 2, 3.0),
            observed_delivery_at=3.1,
        )
        fault = run.observe(
            step="complete_record_lost_above_tcp",
            now=3.2,
            expected_undecidable={"policy"},
        )
        recovery_result = run.send(
            envelope("policy", 3, 3.3),
            observed_delivery_at=3.4,
        )
        recovered = run.observe(
            step="new_record_after_application_loss",
            now=3.4,
            recovery_of="complete_record_lost_above_tcp",
        )
        run.property("record_loss_distinct_from_tcp_retry", dropped.get("status") == "record_dropped")
        run.property("safety", fault["safety_ok"])
        run.property("local_masking", fault["local_masking_ok"])
        run.property(
            "recovered",
            recovery_result.get("status") == "accepted" and recovered["gateway_verdict"] == "consistent",
        )
        return run.records()
    finally:
        run.close()


def profile_duplicate() -> list[dict[str, Any]]:
    profile = RelayProfile("duplicate", duplicate={("authorization", 2)})
    run = ProfileRun("duplicate", profile, "authorization")
    try:
        run.seed()
        response = run.send(
            envelope("authorization", 2, 1.0),
            observed_delivery_at=1.1,
        )
        observed = run.observe(step="same_record_delivered_twice", now=1.2)
        statuses = [
            item.get("status") for item in run.audit.snapshot()
            if item.get("event_type") == "gateway_ingest"
            and item.get("stream") == "authorization"
            and item.get("sequence") == 2
        ]
        run.property(
            "duplicate_idempotence",
            response.get("status") == "duplicate_delivered"
            and statuses == ["accepted", "duplicate_ignored"]
            and observed["gateway_verdict"] == "consistent",
            gateway_statuses=statuses,
        )
        run.property("safety", observed["safety_ok"])
        run.property("local_masking", observed["local_masking_ok"])
        run.property("recovered", observed["gateway_verdict"] == "consistent")
        return run.records()
    finally:
        run.close()


def profile_reorder() -> list[dict[str, Any]]:
    profile = RelayProfile("reorder", hold_for_reorder={("policy", 2)})
    run = ProfileRun("reorder", profile, "policy")
    try:
        run.seed()
        held = run.send(
            envelope("policy", 2, 1.0),
            observed_delivery_at=1.1,
        )
        delivered = run.send(
            envelope("policy", 3, 1.1),
            observed_delivery_at=1.2,
        )
        observed = run.observe(step="newer_record_then_older_record", now=1.3)
        policy_sequence = observed["components"]["policy"] == "consistent" and run.gateway.evaluate(now=1.3)["streams"]["policy"]["sequence"] == 3
        statuses = [
            item.get("status") for item in run.audit.snapshot()
            if item.get("event_type") == "gateway_ingest" and item.get("stream") == "policy"
            and item.get("sequence") in {2, 3}
        ]
        run.property(
            "non_regression",
            held.get("status") == "record_held"
            and delivered.get("status") == "reordered_delivery"
            and statuses == ["accepted", "reordered_ignored"]
            and policy_sequence,
            gateway_statuses=statuses,
        )
        run.property("safety", observed["safety_ok"])
        run.property("local_masking", observed["local_masking_ok"])
        run.property("recovered", observed["gateway_verdict"] == "consistent")
        return run.records()
    finally:
        run.close()


def profile_stale_replay() -> list[dict[str, Any]]:
    profile = RelayProfile("stale_replay", stale_replay={("inventory", 1)})
    run = ProfileRun("stale_replay", profile, "inventory")
    try:
        run.seed()
        run.refresh_except("inventory", captured=3.0)
        replay = envelope("inventory", 1, 1.5)
        response = run.send(replay, observed_delivery_at=3.1)
        fault = run.observe(
            step="old_sequence_replayed_without_refreshing_capture_time",
            now=3.2,
            expected_undecidable={"environment"},
        )
        run.send(
            envelope("inventory", 2, 3.3),
            observed_delivery_at=3.4,
        )
        recovered = run.observe(
            step="fresh_inventory_after_replay",
            now=3.4,
            recovery_of="old_sequence_replayed_without_refreshing_capture_time",
        )
        run.property(
            "stale_replay_does_not_refresh",
            response.get("status") == "duplicate_ignored"
            and fault["streams"]["inventory"]["captured_at"] == 0.0,
        )
        run.property("safety", fault["safety_ok"])
        run.property("local_masking", fault["local_masking_ok"])
        run.property("recovered", recovered["gateway_verdict"] == "consistent")
        return run.records()
    finally:
        run.close()


def profile_outage() -> list[dict[str, Any]]:
    profile = RelayProfile(
        "outage",
        outage_then_retry={("authorization", 2)},
        retry_delay_ms=70,
    )
    run = ProfileRun("outage", profile, "authorization")
    try:
        run.seed()
        run.refresh_except("authorization", captured=3.0)
        response = wait_and_observe_during_fault(
            run,
            document=envelope("authorization", 2, 3.0),
            wait_event="transport_failure",
            step="tcp_destination_unavailable_before_retry",
            now=3.1,
            expected={"authorization", "intent"},
            observed_delivery_at=3.2,
        )
        recovered = run.observe(
            step="same_record_retried_after_tcp_outage",
            now=3.2,
            recovery_of="tcp_destination_unavailable_before_retry",
        )
        retries = [
            item for item in run.audit.snapshot()
            if item.get("event_type") == "transport_retry"
            and item.get("stream") == "authorization"
            and item.get("sequence") == 2
        ]
        run.property(
            "tcp_retry_preserves_record",
            response.get("status") == "accepted" and len(retries) == 1,
        )
        run.property("safety", run.observations[0]["safety_ok"])
        run.property("local_masking", run.observations[0]["local_masking_ok"])
        run.property("recovered", recovered["gateway_verdict"] == "consistent")
        return run.records()
    finally:
        run.close()


def profile_delay_drop(delay_ms: int) -> list[dict[str, Any]]:
    profile = RelayProfile(
        "delay_drop",
        delay_ms={("policy", 2): delay_ms},
        drop={("policy", 3)},
    )
    run = ProfileRun("delay_drop", profile, "policy")
    try:
        run.seed()
        run.refresh_except("policy", captured=3.0)
        wait_and_observe_during_fault(
            run,
            document=envelope("policy", 2, 3.0),
            wait_event="record_delay",
            step="delayed_record_pending",
            now=3.2,
            expected={"policy"},
            observed_delivery_at=3.4,
        )
        first_recovery = run.observe(
            step="delayed_record_recovered",
            now=3.4,
            recovery_of="delayed_record_pending",
        )
        run.refresh_except("policy", captured=6.0, sequence=3)
        dropped = run.send(
            envelope("policy", 3, 6.0),
            observed_delivery_at=6.1,
        )
        second_fault = run.observe(
            step="later_record_dropped",
            now=6.2,
            expected_undecidable={"policy"},
        )
        run.send(
            envelope("policy", 4, 6.3),
            observed_delivery_at=6.4,
        )
        final_recovery = run.observe(
            step="fresh_record_after_combined_faults",
            now=6.4,
            recovery_of="later_record_dropped",
        )
        run.property("delay_then_drop_exercised", dropped.get("status") == "record_dropped")
        run.property("safety", run.observations[0]["safety_ok"] and second_fault["safety_ok"])
        run.property(
            "local_masking",
            run.observations[0]["local_masking_ok"] and second_fault["local_masking_ok"],
        )
        run.property(
            "recovered",
            first_recovery["gateway_verdict"] == "consistent"
            and final_recovery["gateway_verdict"] == "consistent",
        )
        return run.records()
    finally:
        run.close()


def profile_dependency_masking() -> list[dict[str, Any]]:
    run = ProfileRun("dependency_masking", RelayProfile("dependency_masking"), "lineage")
    try:
        run.seed()
        invalid = envelope("lineage", 2, 1.0)
        invalid["payload_hash"] = "0" * 64
        response = run.send(invalid, observed_delivery_at=1.1)
        fault = run.observe(
            step="lineage_hash_invalid",
            now=1.2,
            expected_undecidable={"authorization"},
        )
        run.send(
            envelope("lineage", 3, 1.3),
            observed_delivery_at=1.4,
        )
        recovered = run.observe(
            step="valid_lineage_recovery",
            now=1.4,
            recovery_of="lineage_hash_invalid",
        )
        run.property("invalid_hash_rejected", response.get("status") == "invalid_rejected")
        run.property("safety", fault["safety_ok"])
        run.property("local_masking", fault["local_masking_ok"])
        run.property("recovered", recovered["gateway_verdict"] == "consistent")
        return run.records()
    finally:
        run.close()


def flatten_evaluation(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "profile": row["profile"],
        "ordinal": row["ordinal"],
        "wall_elapsed_ms": row["wall_elapsed_ms"],
        "step": row["step"],
        "logical_time": row["logical_time"],
        "target_stream": row["target_stream"],
        "expected_undecidable": "|".join(row["expected_undecidable"]),
        "actual_undecidable": "|".join(row["actual_undecidable"]),
        "gateway_verdict": row["gateway_verdict"],
        "components_json": canonical_json(row["components"]),
        "mask_json": canonical_json(row["mask"]),
        "streams_json": canonical_json(row["streams"]),
        "safety_ok": row["safety_ok"],
        "local_masking_ok": row["local_masking_ok"],
        "recovery_of": row["recovery_of"] or "",
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", type=Path, default=RESULTS)
    args = parser.parse_args()
    rng = random.Random(SEED)
    jitter_offsets_ms = [-10, -5, 0, 5, 10]
    rng.shuffle(jitter_offsets_ms)
    delay_jitter_schedule_ms = [50 + offset for offset in jitter_offsets_ms]
    combined_delay_ms = 35 + rng.randrange(0, 11)

    records: list[dict[str, Any]] = []
    runners = (
        profile_baseline,
        lambda: profile_delay_jitter(delay_jitter_schedule_ms),
        profile_record_drop,
        profile_duplicate,
        profile_reorder,
        profile_stale_replay,
        profile_outage,
        lambda: profile_delay_drop(combined_delay_ms),
        profile_dependency_masking,
    )
    for runner in runners:
        records.extend(runner())

    args.output_dir.mkdir(parents=True, exist_ok=True)
    raw_path = args.output_dir / "fault_events.ndjson"
    raw_path.write_text("".join(canonical_json(row) + "\n" for row in records))

    evaluations = [flatten_evaluation(row) for row in records if row["record_type"] == "evaluation"]
    with (args.output_dir / "fault_observations.csv").open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(evaluations[0]), lineterminator="\n")
        writer.writeheader()
        writer.writerows(evaluations)

    metadata = {
        "campaign": "controlled localhost TCP evidence transport",
        "scope": "two real TCP hops on 127.0.0.1; deterministic record faults; not production",
        "seed": SEED,
        "source_sha256": {
            "run_fault_experiment.py": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            "evidence_gateway.py": hashlib.sha256((LAB / "evidence_gateway.py").read_bytes()).hexdigest(),
        },
        "subject": SUBJECT,
        "streams": list(STREAMS),
        "dependencies": {key: list(value) for key, value in STREAM_DEPENDENCIES.items()},
        "profiles": [
            "baseline", "delay_jitter", "record_drop", "duplicate", "reorder",
            "stale_replay", "outage", "delay_drop", "dependency_masking",
        ],
        "delay_jitter_base_ms": 50,
        "delay_jitter_offsets_ms": jitter_offsets_ms,
        "delay_jitter_schedule_ms": delay_jitter_schedule_ms,
        "combined_delay_ms": combined_delay_ms,
        "delivery_timestamp_semantics": (
            "delivered_at is absent on the sender wire envelope and stamped at the gateway "
            "from the controlled receiver clock"
        ),
        "recovery_latency_semantics": (
            "analyzer derives logical and monotonic-wall deltas from the fault evaluation "
            "to its first linked consistent evaluation; wall values are descriptive lab observations"
        ),
        "transport_semantics": {
            "tcp_failure": "connection failure followed by application retry of the unchanged envelope",
            "record_drop": "complete application record discarded by relay; TCP cannot recover it",
        },
    }
    (args.output_dir / "campaign_metadata.json").write_text(json.dumps(metadata, indent=2) + "\n")
    print(f"wrote {len(records)} raw records and {len(evaluations)} evaluations to {args.output_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
