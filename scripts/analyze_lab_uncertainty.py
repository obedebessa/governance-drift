#!/usr/bin/env python3
"""Recalculate cadence Wilson intervals and injection-cluster bootstrap CI."""

from __future__ import annotations

import csv
import json
import math
import random
from collections import defaultdict
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "lab/results/repeated_observations.csv"
OUTPUT = ROOT / "lab/results/uncertainty_summary.json"
SEED = 20260807
RESAMPLES = 50_000


def wilson(successes: int, total: int) -> tuple[float, float]:
    z = 1.959963984540054
    p = successes / total
    denominator = 1 + z * z / total
    center = (p + z * z / (2 * total)) / denominator
    radius = z * math.sqrt(p * (1 - p) / total + z * z / (4 * total * total)) / denominator
    return center - radius, center + radius


rows = list(csv.DictReader(SOURCE.open()))
by_cadence: dict[str, list[bool]] = defaultdict(list)
by_injection: dict[tuple[str, str], list[bool]] = defaultdict(list)
by_scenario: dict[str, list[float]] = defaultdict(list)
for row in rows:
    hit = row["detection_rate_hit"] == "True"
    by_cadence[row["cadence_seconds"]].append(hit)
    by_injection[(row["repeat"], row["scenario"])].append(hit)

for (_, scenario), values in by_injection.items():
    by_scenario[scenario].append(sum(values) / len(values))
clusters = [value for values in by_scenario.values() for value in values]
rng = random.Random(SEED)
bootstrap = []
for _ in range(RESAMPLES):
    stratum_means = []
    for values in by_scenario.values():
        sample = [values[rng.randrange(len(values))] for _ in values]
        stratum_means.append(sum(sample) / len(sample))
    bootstrap.append(sum(stratum_means) / len(stratum_means))
bootstrap.sort()

result = {
    "experimental_units": len(clusters),
    "estimand": "macro-average detection over the balanced nine-scenario experimental mixture",
    "cadence_wilson_95": {
        cadence: {
            "successes": sum(values),
            "total": len(values),
            "lower": wilson(sum(values), len(values))[0],
            "upper": wilson(sum(values), len(values))[1],
        }
        for cadence, values in sorted(by_cadence.items(), key=lambda item: float(item[0]))
    },
    "scenario_detection": {
        scenario: {
            "injections": len(values),
            "cadence_observations_detected": int(round(sum(values) * 3)),
            "cadence_observations_total": len(values) * 3,
        }
        for scenario, values in sorted(by_scenario.items())
    },
    "scenario_stratified_cluster_bootstrap_95": {
        "estimate": sum(sum(values) / len(values) for values in by_scenario.values()) / len(by_scenario),
        "lower": bootstrap[int(0.025 * RESAMPLES)],
        "upper": bootstrap[int(0.975 * RESAMPLES) - 1],
        "resamples": RESAMPLES,
        "seed": SEED,
    },
}
OUTPUT.write_text(json.dumps(result, indent=2) + "\n")
print(json.dumps(result, indent=2))
