#!/usr/bin/env python3
"""Run a deterministic synthetic in-memory scaling microbenchmark.

Measured intervals include Python evidence-index construction plus decision
logic for full sweeps, and decision logic over prebuilt indices for event
fan-out.  They exclude Kubernetes/controller/API/network/serialization costs.
External calls are reported only as an explicit architectural model.
"""

from __future__ import annotations

import argparse
import csv
import gc
import hashlib
import json
import math
import os
import platform
import random
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from batch_evaluator import (
    BatchEvaluator,
    EvidenceBundle,
    ExternalCallPlan,
    PreparedEvidence,
    UnitRef,
    Verdict,
    build_synthetic_evidence,
    with_policy_failure,
    with_unauthorized_digest,
)


LAB = Path(__file__).resolve().parent
DEFAULT_OUTPUT = LAB / "results_scaling"
DEFAULT_SIZES = (1, 10, 25, 50, 100, 250, 500, 1000)
DEFAULT_SEED = 20260808
RAW_FIELDS = (
    "sample_order",
    "benchmark",
    "n",
    "repetition",
    "affected_units",
    "inner_iterations",
    "elapsed_ns",
    "per_operation_ns",
    "milliseconds",
    "units_per_second",
    "exact_vectors",
    "expected_vectors",
    "modeled_external_calls",
    "modeled_naive_external_calls",
)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class ScalingCase:
    n: int
    units: tuple[UnitRef, ...]
    baseline: EvidenceBundle
    total_prepared: PreparedEvidence
    subset_prepared: PreparedEvidence
    subset: tuple[UnitRef, ...]
    dataset_fingerprint: str
    subset_fingerprint: str


def build_case(n: int, seed: int, evaluator: BatchEvaluator) -> ScalingCase:
    units, baseline = build_synthetic_evidence(n, seed=seed + n)
    rng = random.Random(seed * 1009 + n)
    subset_size = max(1, math.ceil(n * 0.10))
    subset = tuple(sorted(rng.sample(list(units), subset_size)))
    total_bundle = with_policy_failure(baseline, units)
    subset_bundle = with_unauthorized_digest(baseline, subset)

    baseline_result = evaluator.evaluate(units, baseline)
    if any(item.verdict != "consistent" for item in baseline_result.values()):
        raise RuntimeError(f"n={n}: synthetic baseline is not conforming")
    total_result = evaluator.evaluate(units, total_bundle)
    if any(item.drift_set != ("policy",) for item in total_result.values()):
        raise RuntimeError(f"n={n}: total fan-out ground truth failed")
    subset_result = evaluator.evaluate(units, subset_bundle)
    selected = set(subset)
    for unit, verdict in subset_result.items():
        expected = ("authorization",) if unit in selected else ()
        if verdict.drift_set != expected:
            raise RuntimeError(f"n={n}: subset fan-out ground truth failed for {unit}")

    unit_serialization = "\n".join(
        f"{unit.namespace}/{unit.name}/{unit.uid}" for unit in units
    )
    subset_serialization = "\n".join(
        f"{unit.namespace}/{unit.name}/{unit.uid}" for unit in subset
    )
    return ScalingCase(
        n=n,
        units=units,
        baseline=baseline,
        total_prepared=PreparedEvidence.build(total_bundle),
        subset_prepared=PreparedEvidence.build(subset_bundle),
        subset=subset,
        dataset_fingerprint=sha256_text(unit_serialization),
        subset_fingerprint=sha256_text(subset_serialization),
    )


def inner_iterations(affected_units: int, target_units: int, maximum: int) -> int:
    return max(1, min(maximum, math.ceil(target_units / affected_units)))


def exact_count(benchmark: str, verdicts: dict[UnitRef, Verdict]) -> int:
    if benchmark == "sweep":
        return sum(item.verdict == "consistent" for item in verdicts.values())
    expected = ("policy",) if benchmark == "fanout_total" else ("authorization",)
    return sum(item.drift_set == expected for item in verdicts.values())


def measure(
    *,
    benchmark: str,
    n: int,
    repetition: int,
    sample_order: int,
    affected_units: int,
    iterations: int,
    operation: Callable[[], dict[UnitRef, Verdict]],
    modeled_calls: int,
    modeled_naive_calls: int,
) -> dict[str, int | float | str]:
    started = time.perf_counter_ns()
    verdicts: dict[UnitRef, Verdict] = {}
    for _ in range(iterations):
        verdicts = operation()
    elapsed = time.perf_counter_ns() - started
    per_operation = elapsed / iterations
    seconds = per_operation / 1_000_000_000
    correct = exact_count(benchmark, verdicts)
    if correct != affected_units:
        raise RuntimeError(
            f"{benchmark} n={n} repetition={repetition}: "
            f"{correct}/{affected_units} exact vectors"
        )
    return {
        "sample_order": sample_order,
        "benchmark": benchmark,
        "n": n,
        "repetition": repetition,
        "affected_units": affected_units,
        "inner_iterations": iterations,
        "elapsed_ns": elapsed,
        "per_operation_ns": per_operation,
        "milliseconds": per_operation / 1_000_000,
        "units_per_second": affected_units / seconds,
        "exact_vectors": correct,
        "expected_vectors": affected_units,
        "modeled_external_calls": modeled_calls,
        "modeled_naive_external_calls": modeled_naive_calls,
    }


def source_hashes() -> dict[str, str]:
    hashes = {}
    for path in (Path(__file__), LAB / "batch_evaluator.py"):
        hashes[path.name] = hashlib.sha256(path.read_bytes()).hexdigest()
    return hashes


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repetitions", type=int, default=40)
    parser.add_argument("--warmups", type=int, default=5)
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument("--sizes", default=",".join(map(str, DEFAULT_SIZES)))
    parser.add_argument("--target-units-per-sample", type=int, default=5000)
    parser.add_argument("--max-inner-iterations", type=int, default=2000)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    sizes = tuple(int(item) for item in args.sizes.split(",") if item.strip())
    if not sizes or any(item < 1 for item in sizes):
        raise SystemExit("sizes must contain positive integers")
    if args.repetitions < 2 or args.warmups < 0:
        raise SystemExit("use at least two repetitions and non-negative warmups")

    started_utc = utc_now()
    evaluator = BatchEvaluator()
    call_plan = ExternalCallPlan()
    cases = {n: build_case(n, args.seed, evaluator) for n in sizes}

    for case in cases.values():
        for _ in range(args.warmups):
            evaluator.evaluate(case.units, case.baseline)
            evaluator.evaluate_prepared(case.units, case.total_prepared)
            evaluator.evaluate_prepared(case.subset, case.subset_prepared)

    tasks = [
        (n, repetition, benchmark)
        for repetition in range(1, args.repetitions + 1)
        for n in sizes
        for benchmark in ("sweep", "fanout_total", "fanout_subset")
    ]
    random.Random(args.seed).shuffle(tasks)
    records: list[dict[str, int | float | str]] = []
    for sample_order, (n, repetition, benchmark) in enumerate(tasks, start=1):
        case = cases[n]
        if benchmark == "sweep":
            affected = n
            operation = lambda case=case: evaluator.evaluate(case.units, case.baseline)
            modeled_calls = call_plan.batch_calls(n)
            modeled_naive = call_plan.naive_per_unit_calls(n)
        elif benchmark == "fanout_total":
            affected = n
            operation = lambda case=case: evaluator.evaluate_prepared(
                case.units, case.total_prepared
            )
            modeled_calls = 1
            modeled_naive = call_plan.naive_per_unit_calls(n)
        else:
            affected = len(case.subset)
            operation = lambda case=case: evaluator.evaluate_prepared(
                case.subset, case.subset_prepared
            )
            modeled_calls = 1
            modeled_naive = call_plan.naive_per_unit_calls(affected)
        iterations = inner_iterations(
            affected, args.target_units_per_sample, args.max_inner_iterations
        )
        row = measure(
            benchmark=benchmark,
            n=n,
            repetition=repetition,
            sample_order=sample_order,
            affected_units=affected,
            iterations=iterations,
            operation=operation,
            modeled_calls=modeled_calls,
            modeled_naive_calls=modeled_naive,
        )
        records.append(row)
        if sample_order % max(1, len(tasks) // 10) == 0:
            print(f"completed {sample_order}/{len(tasks)} samples", flush=True)

    output = args.output_dir
    output.mkdir(parents=True, exist_ok=True)
    with (output / "scaling_raw.csv").open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=RAW_FIELDS, lineterminator="\n")
        writer.writeheader()
        writer.writerows(records)

    clock = time.get_clock_info("perf_counter")
    payload = {
        "schema_version": 1,
        "experiment": "synthetic in-memory batch-evaluator scaling microbenchmark",
        "measurement_scope": (
            "Full sweeps measure Python evidence-index construction and semantic decision; "
            "fan-out samples measure semantic decision over prebuilt in-memory indices. "
            "No Kubernetes, Git, policy engine, controller, API, network, parsing, or "
            "serialization latency is measured."
        ),
        "external_call_semantics": (
            "External call counts are architectural models: six batched stream fetches "
            "for a sweep and one delivered event for fan-out. They are not observed calls."
        ),
        "limitations": [
            "Synthetic conforming evidence fixes prevalence and does not represent a field estate.",
            "One host and one Python interpreter execution are measured.",
            "CPU frequency, affinity, and ordinary host background work are not controlled.",
            "Inner iterations reuse immutable evidence and are timer-noise amortization, not independent experimental units.",
            "No evidence acquisition, decoding, persistence, transport, controller convergence, contention, or failure recovery is measured.",
        ],
        "started_utc": started_utc,
        "completed_utc": utc_now(),
        "seed": args.seed,
        "sizes": list(sizes),
        "repetitions": args.repetitions,
        "warmups_per_case": args.warmups,
        "target_units_per_sample": args.target_units_per_sample,
        "max_inner_iterations": args.max_inner_iterations,
        "fanout_subset_fraction": 0.10,
        "synthetic_unit_shape": {
            "namespaces": 17,
            "pods_per_unit": 2,
            "containers_per_pod": 2,
            "approval_scope": "exact namespace/name/UID",
        },
        "call_plan": {
            "batch_stream_fetches": list(call_plan.batch_stream_fetches),
            "batch_calls_per_sweep": call_plan.batch_calls(1),
            "naive_calls_per_unit": call_plan.per_unit_stream_fetches,
        },
        "cases": {
            str(n): {
                "units": n,
                "subset_units": len(case.subset),
                "dataset_fingerprint_sha256": case.dataset_fingerprint,
                "subset_fingerprint_sha256": case.subset_fingerprint,
            }
            for n, case in cases.items()
        },
        "platform": {
            "python": sys.version.split()[0],
            "implementation": platform.python_implementation(),
            "os": platform.platform(),
            "machine": platform.machine(),
            "logical_cpus": os.cpu_count(),
            "python_hash_seed": os.environ.get("PYTHONHASHSEED", "randomized/unset"),
            "garbage_collector_enabled": gc.isenabled(),
            "clock": {
                "function": "time.perf_counter_ns",
                "implementation": clock.implementation,
                "resolution_seconds": clock.resolution,
                "monotonic": clock.monotonic,
                "adjustable": clock.adjustable,
            },
        },
        "source_sha256": source_hashes(),
        "records": records,
    }
    (output / "scaling_raw.json").write_text(json.dumps(payload, indent=2) + "\n")
    print(f"wrote {len(records)} raw samples to {output}")


if __name__ == "__main__":
    main()
