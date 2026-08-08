#!/usr/bin/env python3
"""Executable ablation ladder for the Governance Drift admitted-basis join."""

from __future__ import annotations

import csv
import json
import os
import random
import sys
from collections import defaultdict
from pathlib import Path

import detector_study as study

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lab"))
from basis import BasisSelectionError, select_basis  # noqa: E402


VARIANTS = ("B0", "B1", "B2", "B3", "B4")
VARIANT_LABELS = {
    "B0": "plane-local union",
    "B1": "+ evidence contracts",
    "B2": "+ mode-aware authorization and masking",
    "B3": "+ intent history",
    "B4": "+ activation-aware admitted-basis join",
}


def ordered(classes: set[str]) -> tuple[str, ...]:
    return tuple(name for name in study.CLASS_ORDER if name in classes)


def plane_local(world: dict, k: int) -> set[str]:
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
    return classes


def generic_stream_failure(world: dict) -> bool:
    if not world.get("basis_available", True):
        return True
    return any(
        not approval.get("proof_available", False)
        or not approval.get("live_status_available", False)
        for approval in world["approvals"].values()
    )


def detect_variant(variant: str, approved: dict, world: dict, k: int) -> tuple[str, ...]:
    classes = plane_local(world, k)
    if variant == "B0":
        return ordered(classes)

    if generic_stream_failure(world):
        classes.add("evidence")
    if variant == "B1":
        return ordered(classes)

    applicable, authorization_decidable = study.applicable_approvals(world, k)
    if not authorization_decidable:
        classes.discard("authorization")
        classes.add("evidence")
    elif world.get("basis_available", True) and all(
        approval.get("proof_available", False)
        for approval in world["approvals"].values()
    ):
        # Mode-aware semantics knows that a prospective one-shot proof does
        # not require a live-status stream after execution.
        classes.discard("evidence")
    if variant == "B2":
        return ordered(classes)

    intent_covered, intent_decidable = study.intent_coverage(world)
    if not intent_decidable:
        classes.add("evidence")
    elif not intent_covered:
        classes.add("intent")
    if variant == "B3":
        return ordered(classes)

    # B4 grounds every governance class in the same admitted basis. A missing
    # or ambiguous basis masks basis-dependent polar claims.
    if not world.get("basis_available", True):
        return ordered({"configuration"} & classes | {"evidence"})
    classes = set()
    if any(world["git"][field] != world["observed"].get(field) for field in world["git"]):
        classes.add("configuration")
    if not study.satisfies(world["observed"], world["policy_version"]):
        classes.add("policy")
    for exception in world["exceptions"]:
        if k >= exception["expires"] and (
            exception["removed_at"] is None or exception["removed_at"] > k
        ):
            classes.add("authorization")
    if not authorization_decidable:
        classes.add("evidence")
    else:
        if not applicable:
            classes.add("authorization")
        else:
            if not any(
                world["observed_digest"] in approval.get("subjects", set())
                for approval in applicable
            ):
                classes.add("authorization")
            tag_digest = world["registry"].get(world["git"]["image_tag"])
            if tag_digest is not None and not any(
                tag_digest in approval.get("subjects", set())
                for approval in applicable
            ):
                classes.add("authorization")
    if not intent_decidable:
        classes.add("evidence")
    elif not intent_covered:
        classes.add("intent")
    if world["env"] != approved["sigma0"]:
        classes.add("environment")
    return ordered(classes)


def alarm_stream(scenario: str, variant: str, seed: int) -> list[tuple[str, ...]]:
    rng = random.Random(10_000 + seed)
    approved, world = study.initial_state(rng)
    output = []
    for k in range(study.N_EVENTS):
        if rng.random() < study.CHURN_RATE:
            study.benign_runtime_churn(world, rng)
        if rng.random() < study.GOV_CHURN_RATE:
            study.benign_governance_churn(world, rng, k)
        if scenario != "S0" and k == study.ONSET:
            study.inject(scenario, world, k)
        output.append(detect_variant(variant, approved, world, k))
    return output


def run_case(scenario: str, variant: str, seed: int) -> dict:
    drift = alarm_stream(scenario, variant, seed)
    control = alarm_stream("S0", variant, seed)
    first = next(
        (index for index, pair in enumerate(zip(drift, control)) if pair[0] != pair[1]),
        None,
    )
    observed = set(drift[first]) if first is not None else set()
    expected = study.CLASSES[scenario]
    return {
        "variant": variant,
        "scenario": scenario,
        "seed": seed,
        "detected": int(first is not None),
        "observed": "|".join(ordered(observed)),
        "exact": int(observed == expected),
        "hamming_loss": len(observed ^ expected) / len(study.CLASS_ORDER),
        "false_alarm_events": sum(bool(row) for row in control),
    }


def latest_approved(records: list[dict]) -> str:
    return max(records, key=lambda row: (row["approved_at"], row["id"]))["id"]


def selection_output(variant: str, records: list[dict]) -> str:
    if variant != "B4":
        return latest_approved(records)
    try:
        return select_basis(
            records, subject="payments", environment="prod", now=10.0
        )["id"]
    except BasisSelectionError:
        return "evidence"


def basis_record(
    identifier: str,
    approved_at: float,
    *,
    state: str,
    activated_at: float | None,
    supersedes: tuple[str, ...] = (),
) -> dict:
    return {
        "id": identifier,
        "approved_at": approved_at,
        "state": state,
        "activated_at": activated_at,
        "scope": {"subjects": ["payments"], "environments": ["prod"]},
        "supersedes": list(supersedes),
    }


def class_probe(variant: str, name: str) -> tuple[str, str]:
    approved, world = study.initial_state(random.Random(7))
    k = study.ONSET
    if name == "policy_with_basis":
        world["policy_version"] = "pi-8"
        expected = {"policy"}
    elif name == "policy_without_basis":
        world["policy_version"] = "pi-8"
        world["basis_available"] = False
        expected = {"evidence"}
    elif name == "missing_live_continuing":
        next(iter(world["approvals"].values()))["live_status_available"] = False
        expected = {"evidence"}
    elif name == "missing_live_one_shot":
        approval = next(iter(world["approvals"].values()))
        approval.update(mode="one-shot", live_status_available=False)
        expected = set()
    elif name == "unapproved_rollback":
        study.inject("S6", world, k)
        expected = {"intent", "authorization"}
    elif name == "approved_nonstandard_environment":
        approved["sigma0"]["iam_scope"] = "broad-but-approved"
        world["env"]["iam_scope"] = "broad-but-approved"
        expected = set()
    else:
        raise ValueError(name)
    return "|".join(ordered(expected)), "|".join(detect_variant(variant, approved, world, k))


def semantic_probes() -> list[dict]:
    probe_rows: list[dict] = []
    class_names = (
        "policy_with_basis",
        "policy_without_basis",
        "missing_live_continuing",
        "missing_live_one_shot",
        "unapproved_rollback",
        "approved_nonstandard_environment",
    )
    for variant in VARIANTS:
        for name in class_names:
            expected, observed = class_probe(variant, name)
            probe_rows.append({
                "variant": variant, "probe": name, "expected": expected,
                "observed": observed, "pass": int(expected == observed),
            })

    active = basis_record("G3", 3.0, state="activated", activated_at=3.0)
    selection_cases = {
        "pending_does_not_replace": (
            [active, basis_record("G4", 4.0, state="pending", activated_at=None)], "G3"
        ),
        "aborted_does_not_replace": (
            [active, basis_record("G4", 4.0, state="aborted", activated_at=None)], "G3"
        ),
        "parallel_activation_is_ambiguous": (
            [active, basis_record("G4", 4.0, state="activated", activated_at=4.0)], "evidence"
        ),
        "activated_successor_replaces": (
            [active, basis_record("G4", 4.0, state="activated", activated_at=4.0,
                                  supersedes=("G3",))], "G4"
        ),
    }
    for variant in VARIANTS:
        for name, (records, expected) in selection_cases.items():
            observed = selection_output(variant, records)
            probe_rows.append({
                "variant": variant, "probe": name, "expected": expected,
                "observed": observed, "pass": int(expected == observed),
            })
    return probe_rows


def main() -> None:
    out = ROOT / "data"
    scenarios = [name for name in study.SCENARIOS if name != "S0"]
    rows = [
        run_case(scenario, variant, seed)
        for variant in VARIANTS
        for scenario in scenarios
        for seed in study.SEEDS
    ]
    with (out / "ablation_raw.csv").open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]), lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)

    probes = semantic_probes()
    with (out / "ablation_probes.csv").open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(probes[0]), lineterminator="\n")
        writer.writeheader()
        writer.writerows(probes)

    by_variant: dict[str, list[dict]] = defaultdict(list)
    probe_by_variant: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        by_variant[row["variant"]].append(row)
    for row in probes:
        probe_by_variant[row["variant"]].append(row)
    summary = {
        "design": "paired 12-scenario x 20-seed cumulative ablation plus ten executable semantic probes",
        "variants": [],
    }
    table = [
        "% Generated by code/ablation_study.py",
        r"\begin{tabular}{@{}llrrrr@{}}",
        r"\toprule",
        r"Variant & Added semantics & Units & Exact (\%) & Hamming & Probes \\",
        r"\midrule",
    ]
    for variant in VARIANTS:
        variant_rows = by_variant[variant]
        variant_probes = probe_by_variant[variant]
        item = {
            "variant": variant,
            "label": VARIANT_LABELS[variant],
            "scenario_seed_units": len(variant_rows),
            "exact_vector_success": sum(row["exact"] for row in variant_rows) / len(variant_rows),
            "mean_hamming_loss": sum(row["hamming_loss"] for row in variant_rows) / len(variant_rows),
            "probe_passes": sum(row["pass"] for row in variant_probes),
            "probe_total": len(variant_probes),
            "false_alarm_events": sum(row["false_alarm_events"] for row in variant_rows),
        }
        summary["variants"].append(item)
        table.append(
            f"{variant} & {item['label']} & {item['scenario_seed_units']} & "
            f"{100*item['exact_vector_success']:.1f} & {item['mean_hamming_loss']:.4f} & "
            f"{item['probe_passes']}/{item['probe_total']} \\\\"
        )
    table.extend([r"\bottomrule", r"\end{tabular}"])
    (out / "table_ablation.tex").write_text("\n".join(table) + "\n")
    (out / "ablation_summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
