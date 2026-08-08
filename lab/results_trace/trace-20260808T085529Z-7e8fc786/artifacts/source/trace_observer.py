#!/usr/bin/env python3
"""Independent, hash-chained observer used by the S1--S9 trace campaign."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from trace_evaluator import canonical_bytes, evaluate


GENESIS_HASH = "0" * 64


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def durable_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        json.dump(value, handle, sort_keys=True, separators=(",", ":"))
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())


def append_poll(path: Path, row: dict[str, Any]) -> None:
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, sort_keys=True, separators=(",", ":"), ensure_ascii=False))
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())


def undecidable(exc: Exception) -> dict[str, Any]:
    components = {
        component: "undecidable"
        for component in ("configuration", "policy", "authorization", "intent", "environment")
    }
    return {
        "schema": "govdrift-trace-evaluation/v1",
        "verdict": "undecidable",
        "class": "evidence",
        "class_set": ["evidence"],
        "drift_set": [],
        "undecidable_components": list(components),
        "evidence_drift": True,
        "components": components,
        "details": {name: [f"observer exception: {type(exc).__name__}: {exc}"] for name in components},
        "detail": f"observer exception: {type(exc).__name__}: {exc}",
        "inputs": {},
        "input_fingerprint_sha256": hashlib.sha256(str(exc).encode()).hexdigest(),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--campaign-id", required=True)
    parser.add_argument("--scenario", required=True)
    parser.add_argument("--cadence", type=float, required=True)
    parser.add_argument("--runtime", type=Path, required=True)
    parser.add_argument("--namespace", required=True)
    parser.add_argument("--deployment", required=True)
    parser.add_argument("--policy", required=True)
    parser.add_argument("--start-mono", type=float, required=True)
    parser.add_argument("--stop-file", type=Path, required=True)
    parser.add_argument("--ready-file", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    if args.cadence <= 0:
        parser.error("cadence must be positive")

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.touch(exist_ok=False)
    observer_id = str(uuid.uuid4())
    process_started_mono = time.monotonic()
    process_started_utc = utc_now()
    durable_json(args.ready_file, {
        "schema": "govdrift-trace-observer-ready/v1",
        "campaign_id": args.campaign_id,
        "scenario": args.scenario,
        "cadence_seconds": args.cadence,
        "observer_id": observer_id,
        "pid": os.getpid(),
        "process_started_mono": process_started_mono,
        "process_started_utc": process_started_utc,
        "output": str(args.output.resolve()),
    })

    scheduled = args.start_mono
    sequence = 0
    previous_hash = GENESIS_HASH
    skipped_slots = 0
    while not args.stop_file.exists():
        delay = scheduled - time.monotonic()
        if delay > 0:
            time.sleep(min(delay, 0.1))
            continue

        started_mono = time.monotonic()
        started_utc = utc_now()
        adapter_error: str | None = None
        try:
            evaluation = evaluate(
                runtime=args.runtime,
                namespace=args.namespace,
                deployment=args.deployment,
                policy=args.policy,
            )
        except Exception as exc:  # each failed sample remains durable evidence
            evaluation = undecidable(exc)
            adapter_error = f"{type(exc).__name__}: {exc}"
        completed_mono = time.monotonic()
        completed_utc = utc_now()
        sequence += 1

        row: dict[str, Any] = {
            "schema": "govdrift-trace-poll/v1",
            "campaign_id": args.campaign_id,
            "scenario": args.scenario,
            "cadence_seconds": args.cadence,
            "observer_id": observer_id,
            "pid": os.getpid(),
            "process_started_mono": process_started_mono,
            "process_started_utc": process_started_utc,
            "sequence": sequence,
            "scheduled_mono": scheduled,
            "actual_start_mono": started_mono,
            "actual_start_utc": started_utc,
            "completed_mono": completed_mono,
            "completed_utc": completed_utc,
            "scheduler_lag_seconds": max(0.0, started_mono - scheduled),
            "evaluation_seconds": completed_mono - started_mono,
            "skipped_slots_before_poll": skipped_slots,
            "adapter_error": adapter_error,
            "verdict": evaluation.get("verdict", "undecidable"),
            "class": evaluation.get("class"),
            "class_set": evaluation.get("class_set", ["evidence"]),
            "components": evaluation.get("components", {}),
            "input_fingerprint_sha256": evaluation.get("input_fingerprint_sha256"),
            "evaluation": evaluation,
            "previous_poll_sha256": previous_hash,
        }
        row["poll_sha256"] = hashlib.sha256(canonical_bytes(row)).hexdigest()
        append_poll(args.output, row)
        previous_hash = row["poll_sha256"]

        scheduled += args.cadence
        skipped_slots = 0
        while scheduled <= completed_mono:
            scheduled += args.cadence
            skipped_slots += 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
