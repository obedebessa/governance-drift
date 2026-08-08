#!/usr/bin/env python3
"""Compare the admitted-basis join with a composite of plane-local checks."""

from __future__ import annotations

import csv
import json
import os
import random
from collections import defaultdict

import detector_study as study
import ablation_study as ablation


def local_union(world: dict, k: int) -> tuple[str, ...]:
    """Union existing signals without snapshot selection or historical join."""
    classes: set[str] = set()
    if any(world["git"][field] != world["observed"].get(field) for field in world["git"]):
        classes.add("configuration")
    if not study.satisfies(world["observed"], world["policy_version"]):
        classes.add("policy")
    if any(
        k >= exc["expires"] and (exc["removed_at"] is None or exc["removed_at"] > k)
        for exc in world["exceptions"]
    ):
        classes.add("authorization")
    if world["observed_digest"] not in world["attested_digests"]:
        classes.add("authorization")
    if (
        world["env"].get("iam_scope") != "least-priv"
        or world["env"].get("cloud_lb") != "standard-config"
    ):
        classes.add("environment")
    return tuple(name for name in study.CLASS_ORDER if name in classes)


def stream(scenario: str, seed: int) -> list[tuple[str, ...]]:
    rng = random.Random(10_000 + seed)
    _, world = study.initial_state(rng)
    out = []
    for k in range(study.N_EVENTS):
        if rng.random() < study.CHURN_RATE:
            study.benign_runtime_churn(world, rng)
        if rng.random() < study.GOV_CHURN_RATE:
            study.benign_governance_churn(world, rng, k)
        if scenario != "S0" and k == study.ONSET:
            study.inject(scenario, world, k)
        out.append(local_union(world, k))
    return out


def local_result(scenario: str, seed: int) -> dict:
    drift, control = stream(scenario, seed), stream("S0", seed)
    first = next((i for i, pair in enumerate(zip(drift, control)) if pair[0] != pair[1]), None)
    observed = set(drift[first]) if first is not None else set()
    return {
        "scenario": scenario,
        "seed": seed,
        "detected": int(first is not None),
        "observed": "|".join(name for name in study.CLASS_ORDER if name in observed),
        "exact": int(observed == study.CLASSES[scenario]),
        "false_alarm_events": sum(bool(row) for row in control),
    }


def main() -> None:
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    out_dir = os.path.join(root, "data")
    scenarios = [name for name in study.SCENARIOS if name != "S0"]
    local_rows = [local_result(scenario, seed) for scenario in scenarios for seed in study.SEEDS]
    proposed_rows = [study.run(scenario, "T4", seed) for scenario in scenarios for seed in study.SEEDS]

    with open(os.path.join(out_dir, "join_baseline.csv"), "w", newline="") as stream_out:
        writer = csv.DictWriter(stream_out, fieldnames=list(local_rows[0].keys()), lineterminator="\n")
        writer.writeheader()
        writer.writerows(local_rows)

    by_scenario: dict[str, list[dict]] = defaultdict(list)
    for row in local_rows:
        by_scenario[row["scenario"]].append(row)
    proposed_by_scenario: dict[str, list[dict]] = defaultdict(list)
    for row in proposed_rows:
        proposed_by_scenario[row["scn"]].append(row)

    def stable_observed(rows: list[dict]) -> str:
        values = sorted({row["observed"] for row in rows})
        return values[0] if len(values) == 1 else "varies"

    table_lines = []
    labels = {
        "configuration": "config.", "policy": "policy", "intent": "intent",
        "authorization": "auth.", "environment": "env.", "evidence": "evidence",
    }
    for scenario in scenarios:
        local = by_scenario[scenario]
        proposed = proposed_by_scenario[scenario]
        expected = ",".join(labels[name] for name in study.CLASS_ORDER if name in study.CLASSES[scenario])
        local_set = ",".join(labels[name] for name in stable_observed(local).split("|") if name)
        proposed_sets = sorted({row["alarm_class_set"] for row in proposed})
        proposed_set = ",".join(labels[name] for name in proposed_sets[0].split("|") if name)
        local_exact = sum(row["exact"] for row in local) / len(local)
        proposed_exact = sum(row["correct"] for row in proposed) / len(proposed)
        table_lines.append(
            f"{scenario} & $\\{{{expected}\\}}$ & "
            f"$\\{{{local_set}\\}}$ & "
            f"$\\{{{proposed_set}\\}}$ & {100*local_exact:.0f} & {100*proposed_exact:.0f} \\\\\n"
        )

    with open(os.path.join(out_dir, "table_join_baseline.tex"), "w") as stream_out:
        stream_out.write(
            "\\begin{tabular}{@{}lccc rr@{}}\n\\toprule\n"
            "Scenario & Expected & Local union & Joined & Local exact & Joined exact \\\\\n"
            "\\midrule\n" + "".join(table_lines) + "\\bottomrule\n\\end{tabular}\n"
        )

    probe_rows = ablation.semantic_probes()
    probes_by_name: dict[str, dict[str, str]] = defaultdict(dict)
    for row in probe_rows:
        if row["variant"] in {"B0", "B4"}:
            probes_by_name[row["probe"]][row["variant"]] = row["observed"]

    summary = {
        "scenario_seed_units": len(local_rows),
        "local_union_exact": sum(row["exact"] for row in local_rows) / len(local_rows),
        "joined_exact": sum(row["correct"] for row in proposed_rows) / len(proposed_rows),
        "local_union_false_alarm_events": sum(row["false_alarm_events"] for row in local_rows),
        "semantic_probes": {
            name: {
                "local_union": list(filter(None, values.get("B0", "").split("|"))),
                "joined": list(filter(None, values.get("B4", "").split("|"))),
            }
            for name, values in sorted(probes_by_name.items())
        },
    }
    with open(os.path.join(out_dir, "join_baseline_summary.json"), "w") as stream_out:
        json.dump(summary, stream_out, indent=2)
        stream_out.write("\n")
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
