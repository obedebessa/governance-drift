#!/usr/bin/env python3
"""One isolated cadence observer for transition-inclusive control windows."""

from __future__ import annotations

import argparse
import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path

from evaluator import evaluate


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def append_json(path: Path, row: dict) -> None:
    with path.open("a") as handle:
        handle.write(json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n")
        handle.flush()
        os.fsync(handle.fileno())


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--campaign", required=True)
    parser.add_argument("--window", type=int, required=True)
    parser.add_argument("--control", required=True)
    parser.add_argument("--cadence", type=float, required=True)
    parser.add_argument("--phase", type=float, required=True)
    parser.add_argument("--start-mono", type=float, required=True)
    parser.add_argument("--stop-file", type=Path, required=True)
    parser.add_argument("--ready-file", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.touch()
    args.ready_file.write_text(utc_now() + "\n")
    scheduled = args.start_mono + args.phase
    sequence = 0
    while not args.stop_file.exists():
        delay = scheduled - time.monotonic()
        if delay > 0:
            time.sleep(min(delay, 0.1))
            continue
        started = time.monotonic()
        try:
            observed = evaluate("T4")
            error = ""
        except Exception as exc:  # preserve adapter failures as evidence, never lose a poll
            observed = {
                "verdict": "undecidable",
                "class": "evidence",
                "class_set": ["evidence"],
                "components": {},
                "undecidable_components": ["configuration", "policy", "authorization", "intent", "environment"],
                "detail": f"observer exception: {type(exc).__name__}: {exc}",
            }
            error = type(exc).__name__
        completed = time.monotonic()
        sequence += 1
        append_json(args.output, {
            "campaign": args.campaign,
            "window": args.window,
            "control": args.control,
            "cadence_seconds": args.cadence,
            "sequence": sequence,
            "scheduled_mono": scheduled,
            "actual_start_mono": started,
            "completed_mono": completed,
            "scheduler_lag_seconds": max(0.0, started - scheduled),
            "evaluation_seconds": completed - started,
            "observed_utc": utc_now(),
            "verdict": observed.get("verdict", "undecidable"),
            "class": observed.get("class"),
            "class_set": observed.get("class_set", []),
            "components": observed.get("components", {}),
            "undecidable_components": observed.get("undecidable_components", []),
            "detail": observed.get("detail", ""),
            "adapter_error": error,
        })
        scheduled += args.cadence
        if completed - scheduled > args.cadence:
            # Keep a deterministic schedule while explicitly recording missed slots.
            skipped = int((completed - scheduled) // args.cadence)
            scheduled += skipped * args.cadence
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
