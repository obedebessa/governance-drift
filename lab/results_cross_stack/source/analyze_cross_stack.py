#!/usr/bin/env python3
"""Validate and summarize the bounded Argo CD + Gatekeeper replication."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import re
import statistics
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
STACK = ROOT / "lab" / "stacks" / "argocd-gatekeeper"
DEFAULT_RESULTS = ROOT / "lab" / "results_cross_stack"
CAMPAIGN_SOURCE_FILES = {
    "lab/run_cross_stack_experiment.py": ROOT / "lab" / "run_cross_stack_experiment.py",
    "lab/test_cross_stack_adapter.py": ROOT / "lab" / "test_cross_stack_adapter.py",
    "scripts/analyze_cross_stack.py": ROOT / "scripts" / "analyze_cross_stack.py",
}
EXPECTED = {"S1": "configuration", "S3": "policy", "S4": "authorization"}
SURFACE = {
    "S1": "argocd-native",
    "S3": "gatekeeper-native",
    "S4": "shared-artifact-adapter",
}
UPSTREAM_HASHES = {
    "argocd-v3.4.2": "69114b8c9eb48a1d08598e6f654a0869b10ae902456ea4b70796cb563760f5ec",
    "gatekeeper-v3.22.2": "72683f57fdfa4c34d4a892e5e6f457a5a7e533eba0293d781d53d08dd6614a5a",
}


def require(condition: bool, message: str) -> None:
    if not condition:
        raise SystemExit(f"CROSS-STACK ARTIFACT INVALID: {message}")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def load_ndjson(path: Path) -> list[dict[str, Any]]:
    rows = []
    for number, line in enumerate(path.read_text().splitlines(), start=1):
        try:
            row = json.loads(line)
        except json.JSONDecodeError as exc:
            raise SystemExit(f"invalid NDJSON {path.name}:{number}: {exc}") from exc
        require(isinstance(row, dict), f"{path.name}:{number} is not an object")
        rows.append(row)
    require(rows, f"{path.name} is empty")
    return rows


def numeric_field(row: dict[str, Any], key: str, label: str) -> float:
    value = row.get(key)
    require(
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(float(value)),
        f"{label}: missing or invalid {key}",
    )
    return float(value)


def parse_utc_field(row: dict[str, Any], key: str, label: str) -> datetime:
    value = row.get(key)
    require(isinstance(value, str) and bool(value), f"{label}: missing {key}")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        require(False, f"{label}: invalid {key}")
        raise AssertionError("unreachable")
    require(parsed.tzinfo is not None, f"{label}: {key} is not timezone-aware")
    return parsed


def validate_poll_timing(
    row: dict[str, Any],
    *,
    reference_id: str,
    start_field: str,
    completion_field: str,
    label: str,
) -> float:
    require(
        row.get("timing_reference_id") == reference_id,
        f"{label}: timing-reference identity mismatch",
    )
    started = numeric_field(row, start_field, label)
    completed = numeric_field(row, completion_field, label)
    duration = numeric_field(row, "evaluation_duration_seconds", label)
    require(started >= 0.0, f"{label}: evaluation starts before its reference")
    require(completed >= started, f"{label}: completion precedes start")
    require(duration >= 0.0, f"{label}: negative evaluation duration")
    require(
        math.isclose(completed - started, duration, rel_tol=0.0, abs_tol=2e-6),
        f"{label}: duration does not equal completion minus start",
    )
    return completed


def validate_classification_snapshot(
    row: dict[str, Any],
    *,
    exact_field: str,
    label: str,
) -> None:
    expected = {
        "components": row.get("components"),
        "observed_set": row.get("observed_set"),
        "undecidable_components": row.get("undecidable_components"),
        exact_field: row.get(exact_field),
    }
    require(
        row.get("classification_at_completion") == expected,
        f"{label}: completion classification is missing or disagrees with the poll",
    )


def recompute_scenario_timing(
    marker: dict[str, Any],
    polls: list[dict[str, Any]],
    expected_set: set[str],
) -> dict[str, Any]:
    scenario = str(marker.get("scenario"))
    repetition = int(marker.get("repetition", 0))
    label = f"{scenario} r{repetition}"
    require(marker.get("record_type") == "injection_onset_marker", f"{label}: onset marker absent")
    require(marker.get("reference_kind") == "operational-onset", f"{label}: onset reference changed")
    injection_id = marker.get("injection_id")
    reference_id = marker.get("timing_reference_id")
    schedule_index = int(marker.get("schedule_index", 0))
    require(isinstance(injection_id, str) and bool(injection_id), f"{label}: injection identity absent")
    require(isinstance(reference_id, str) and bool(reference_id), f"{label}: onset identity absent")
    require(schedule_index > 0, f"{label}: schedule identity absent")
    injection_utc = parse_utc_field(marker, "injection_utc", label)
    onset_utc = parse_utc_field(marker, "onset_utc", label)
    require(onset_utc >= injection_utc, f"{label}: UTC onset precedes injection")
    t_inject = numeric_field(marker, "injection_monotonic_seconds", label)
    t_onset = numeric_field(marker, "onset_monotonic_seconds", label)
    actuation = numeric_field(marker, "actuation_seconds", label)
    require(t_onset >= t_inject, f"{label}: monotonic onset precedes injection")
    require(
        math.isclose(t_onset - t_inject, actuation, rel_tol=0.0, abs_tol=2e-6),
        f"{label}: actuation does not match monotonic markers",
    )
    require(bool(polls), f"{label}: scenario polls absent")
    require(
        [int(row.get("poll_index", 0)) for row in polls]
        == list(range(1, len(polls) + 1)),
        f"{label}: scenario poll indexes are not contiguous",
    )

    first_honest: float | None = None
    first_honest_kind: str | None = None
    first_epistemic: float | None = None
    first_substantive: float | None = None
    first_substantive_set: list[str] | None = None
    exact_completion: float | None = None
    final_observed: list[str] | None = None
    previous_completion = -1.0
    exact_count = 0
    for row in polls:
        poll_label = f"{label} poll {row.get('poll_index')}"
        require(row.get("scenario") == scenario, f"{poll_label}: scenario mismatch")
        require(int(row.get("repetition", 0)) == repetition, f"{poll_label}: repetition mismatch")
        require(
            int(row.get("schedule_index", 0)) == schedule_index,
            f"{poll_label}: schedule identity mismatch",
        )
        require(row.get("injection_id") == injection_id, f"{poll_label}: injection identity mismatch")
        completed = validate_poll_timing(
            row,
            reference_id=reference_id,
            start_field="evaluation_started_since_onset_seconds",
            completion_field="evaluation_completed_since_onset_seconds",
            label=poll_label,
        )
        require(completed >= previous_completion, f"{poll_label}: completion time regressed")
        previous_completion = completed
        require(
            math.isclose(
                numeric_field(row, "elapsed_since_onset_seconds", poll_label),
                numeric_field(row, "evaluation_started_since_onset_seconds", poll_label),
                rel_tol=0.0,
                abs_tol=5e-7,
            ),
            f"{poll_label}: legacy elapsed field is not the evaluation start",
        )
        validate_classification_snapshot(row, exact_field="exact_set", label=poll_label)
        observed = set(row.get("observed_set") or [])
        undecidable = set(row.get("undecidable_components") or [])
        if (observed or undecidable) and first_honest is None:
            first_honest = completed
            if observed and undecidable:
                first_honest_kind = "substantive-and-epistemic"
            elif observed:
                first_honest_kind = "substantive-only"
            else:
                first_honest_kind = "epistemic-only"
        if undecidable and first_epistemic is None:
            first_epistemic = completed
        if observed and first_substantive is None:
            first_substantive = completed
            first_substantive_set = sorted(observed)
        exact = observed == expected_set and not undecidable
        require(row.get("exact_set") is exact, f"{poll_label}: exact-set flag was tampered")
        if exact:
            exact_count += 1
            if exact_completion is None:
                exact_completion = completed
                final_observed = sorted(observed)

    require(exact_count == 1, f"{label}: expected exactly one exact terminal poll")
    require(polls[-1].get("exact_set") is True, f"{label}: exact poll is not terminal")
    require(first_honest is not None, f"{label}: no honest alert in raw polls")
    require(first_substantive is not None, f"{label}: no substantive alert in raw polls")
    require(exact_completion is not None, f"{label}: no exact classification in raw polls")
    return {
        "schedule_index": schedule_index,
        "injection_id": injection_id,
        "timing_reference_id": reference_id,
        "injection_utc": marker["injection_utc"],
        "onset_utc": marker["onset_utc"],
        "actuation_seconds": round(actuation, 6),
        "ddl_seconds": round(first_honest, 6),
        "first_honest_verdict_kind": first_honest_kind,
        "first_epistemic_alert_seconds": (
            round(first_epistemic, 6) if first_epistemic is not None else None
        ),
        "first_substantive_alert_seconds": round(first_substantive, 6),
        "exact_set_latency_seconds": round(exact_completion, 6),
        "evidence_latency_seconds": round(exact_completion, 6),
        "first_observed_set": "|".join(first_substantive_set or []),
        "final_observed_set": "|".join(final_observed or []),
        "polls_to_exact": len(polls),
    }


def validate_cleanup_proof(cleanup: dict[str, Any]) -> None:
    cluster = cleanup.get("cluster")
    require(cluster == "govdrift-cross", "cleanup cluster changed")
    require(cleanup.get("target_scope") == "only govdrift-cross", "cleanup scope changed")
    proof = cleanup.get("cleanup_proof")
    require(isinstance(proof, dict), "cleanup command proof is absent")
    verify_command = ["kind", "get", "clusters"]
    delete_command = ["kind", "delete", "cluster", "--name", "govdrift-cross"]

    def validate_verification(stage: str) -> dict[str, Any]:
        evidence = proof.get(stage)
        require(isinstance(evidence, dict), f"cleanup {stage} evidence is absent")
        require(evidence.get("command") == verify_command, f"cleanup {stage} command changed")
        require(isinstance(evidence.get("returncode"), int), f"cleanup {stage} rc absent")
        require(isinstance(evidence.get("stdout"), str), f"cleanup {stage} stdout absent")
        require(isinstance(evidence.get("stderr"), str), f"cleanup {stage} stderr absent")
        clusters = sorted({
            row.strip() for row in evidence["stdout"].splitlines() if row.strip()
        })
        require(evidence.get("clusters") == clusters, f"cleanup {stage} cluster parse mismatch")
        return evidence

    before = validate_verification("verify_before")
    after = validate_verification("verify_after")
    delete = proof.get("delete")
    require(isinstance(delete, dict), "cleanup delete evidence is absent")
    require(delete.get("command") == delete_command, "cleanup delete command changed")
    require(isinstance(delete.get("attempted"), bool), "cleanup delete attempted flag absent")
    require(isinstance(delete.get("stdout"), str), "cleanup delete stdout absent")
    require(isinstance(delete.get("stderr"), str), "cleanup delete stderr absent")
    require(delete.get("attempted") is True, "completed run did not attempt cleanup")
    require(delete.get("returncode") == 0, "cleanup delete command failed")
    require(cleanup.get("delete_attempted") is True, "legacy cleanup attempted flag changed")
    require(cleanup.get("delete_returncode") == delete.get("returncode"), "cleanup rc mismatch")
    require(before.get("returncode") == 0, "pre-cleanup verification failed")
    require(cluster in set(before.get("clusters") or []), "target cluster absent before deletion")
    require(after.get("returncode") == 0, "post-cleanup verification failed")
    require(cluster not in set(after.get("clusters") or []), "target cluster remains after deletion")
    require(cleanup.get("verified_absent") is True, "cleanup absence flag is false")


def tex(value: str) -> str:
    return value.replace("_", r"\_").replace("+", r"$+$")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--results", type=Path, default=DEFAULT_RESULTS)
    args = parser.parse_args()
    results = args.results.resolve()

    status = json.loads((results / "run_status.json").read_text())
    cleanup = json.loads((results / "cleanup.json").read_text())
    platform = json.loads((results / "platform.json").read_text())
    observations = json.loads((results / "cross_stack_observations.json").read_text())
    raw = load_ndjson(results / "cross_stack_raw.ndjson")
    installs = load_ndjson(results / "install_events.ndjson")
    resources = load_ndjson(results / "resource_samples.ndjson")

    require(status.get("status") == "completed", "live run did not complete")
    require(status.get("api_read_errors") == 0, "live run recorded Kubernetes API read errors")
    require(status.get("stop_rule_triggered") is False, "stop rule was triggered")
    require(status.get("cluster") == "govdrift-cross", "wrong cluster name")
    require(status.get("cleanup") == cleanup, "run-status cleanup copy disagrees with cleanup.json")
    validate_cleanup_proof(cleanup)
    require(platform.get("cluster") == "govdrift-cross", "platform cluster mismatch")
    require(platform.get("argocd_version") == "v3.4.2", "Argo CD version changed")
    require(platform.get("gatekeeper_version") == "v3.22.2", "Gatekeeper version changed")
    require(
        platform.get("gatekeeper_enforcement_action") == "dryrun",
        "Gatekeeper was not dry-run",
    )
    require(platform.get("repetitions_per_slice") == 5, "repetition count changed")
    require(platform.get("poll_seconds") == 0.5, "poll cadence changed")
    require(
        platform.get("validation_scope", {}).get("intent") == "not evaluated"
        and platform.get("validation_scope", {}).get("environment") == "not evaluated",
        "unsupported components were represented as evaluated",
    )

    git = platform.get("protocol_source_state")
    require(
        isinstance(git, dict),
        "protocol-source Git provenance is absent from platform metadata",
    )
    require(
        isinstance(git.get("head"), str)
        and re.fullmatch(r"[0-9a-f]{40}", git["head"]) is not None,
        "Git HEAD provenance is absent or invalid",
    )
    require(
        isinstance(git.get("branch"), str) and bool(git["branch"]),
        "Git branch provenance is absent",
    )
    require(isinstance(git.get("detached"), bool), "Git detached state is absent")
    require(isinstance(git.get("dirty"), bool), "Git dirty state is absent")
    dirty_files = git.get("modified_or_untracked_files")
    require(
        isinstance(dirty_files, list)
        and all(isinstance(path, str) and bool(path) for path in dirty_files),
        "Git modified/untracked file inventory is absent or invalid",
    )
    require(
        dirty_files == sorted(set(dirty_files)),
        "Git modified/untracked file inventory is not deterministic",
    )
    require(
        git["dirty"] == bool(dirty_files),
        "Git dirty flag disagrees with the modified/untracked file inventory",
    )
    require(
        git.get("capture_boundary") == "campaign initialization before output mutation",
        "Git provenance capture boundary is absent or changed",
    )

    checksum_rows = list(csv.DictReader((results / "manifest_checksums.csv").open()))
    checksum_by_key: dict[tuple[str, str], dict[str, str]] = {}
    for row in checksum_rows:
        key = (row.get("scope", ""), row.get("name", ""))
        require(key not in checksum_by_key, f"duplicate checksum inventory row {key}")
        checksum_by_key[key] = row
    for name, expected_hash in UPSTREAM_HASHES.items():
        key = ("upstream-install", name)
        require(key in checksum_by_key, f"missing upstream checksum {name}")
        row = checksum_by_key[key]
        require(row["sha256"] == expected_hash, f"upstream lock changed for {name}")
        candidate = next((results / "upstream").glob(f"{name.split('-v')[0]}-v*.yaml"))
        require(sha256_file(candidate) == expected_hash, f"preserved manifest hash failed for {name}")
    for path in sorted(STACK.iterdir()):
        if path.is_file():
            key = ("local-stack", path.name)
            require(key in checksum_by_key, f"missing local manifest hash {path.name}")
            require(
                checksum_by_key[key]["sha256"] == sha256_file(path),
                f"local manifest changed after execution: {path.name}",
            )
    for name, path in CAMPAIGN_SOURCE_FILES.items():
        key = ("campaign-source", name)
        require(key in checksum_by_key, f"missing campaign source checksum {name}")
        row = checksum_by_key[key]
        require(row.get("verified") == "True", f"campaign source is not verified: {name}")
        require(row.get("sha256") == sha256_file(path), f"campaign source hash changed: {name}")
        try:
            recorded_bytes = int(row.get("bytes", ""))
        except (TypeError, ValueError):
            require(False, f"campaign source byte count is invalid: {name}")
            recorded_bytes = -1
        require(recorded_bytes == path.stat().st_size, f"campaign source size changed: {name}")

    ready_components = {
        row.get("component") for row in installs
        if row.get("record_type") == "component_ready"
    }
    require("argocd-v3.4.2" in ready_components, "Argo CD readiness absent")
    require("gatekeeper-v3.22.2" in ready_components, "Gatekeeper readiness absent")
    require(
        not any(row.get("record_type") == "api_read_error" for row in installs),
        "install trace contains a Kubernetes API read error",
    )
    require(
        any(
            row.get("record_type") == "gatekeeper_audit_configured"
            and row.get("enforcement_action") == "dryrun"
            and row.get("audit_interval_seconds") == 2
            for row in installs
        ),
        "Gatekeeper audit configuration evidence absent",
    )
    require(
        any(
            row.get("record_type") == "git_source_ready"
            and row.get("validation_origin") == "argocd-repo-server-pod"
            and row.get("revision")
            for row in installs
        ),
        "Git source was not validated from Argo repo-server",
    )

    sample_rows = [row for row in resources if row.get("record_type") == "resource_sample"]
    require(sample_rows, "resource monitor produced no samples")
    require(
        not any(row.get("stop_rule_triggered") for row in sample_rows),
        "resource trace contains a triggered stop rule",
    )
    resource_maxima: dict[str, float] = {}
    for row in sample_rows:
        for name, value in row.get("metrics", {}).items():
            if value is not None:
                resource_maxima[name] = max(resource_maxima.get(name, 0.0), float(value))

    rows = observations.get("rows", [])
    require(len(rows) == 15, f"expected 15 observations, found {len(rows)}")
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        scenario = row.get("scenario")
        require(scenario in EXPECTED, f"unexpected scenario {scenario}")
        grouped[scenario].append(row)
        require(row.get("expected_set") == EXPECTED[scenario], f"{scenario}: expected set changed")
        require(row.get("final_observed_set") == EXPECTED[scenario], f"{scenario}: final set mismatch")
        require(row.get("first_observed_set") == EXPECTED[scenario], f"{scenario}: first set mismatch")
        require(row.get("exact_set") is True, f"{scenario}: exact-set property failed")
        require(row.get("baseline_exact_empty") is True, f"{scenario}: baseline was not empty")
        require(row.get("surface") == SURFACE[scenario], f"{scenario}: evidence surface changed")
        require(row.get("intent_evaluated") is False, f"{scenario}: intent claim is unsupported")
        require(row.get("environment_evaluated") is False, f"{scenario}: environment claim is unsupported")
        require(
            isinstance(row.get("injection_id"), str)
            and isinstance(row.get("timing_reference_id"), str),
            f"{scenario}: timing identity is absent",
        )
        parse_utc_field(row, "injection_utc", f"{scenario} observation")
        parse_utc_field(row, "onset_utc", f"{scenario} observation")
        require(float(row.get("ddl_seconds", -1)) >= 0.0, f"{scenario}: invalid DDL")
        require(
            row.get("first_honest_verdict_kind")
            in {"epistemic-only", "substantive-only", "substantive-and-epistemic"},
            f"{scenario}: first-honest verdict kind is absent",
        )
        epistemic = row.get("first_epistemic_alert_seconds")
        require(
            epistemic is None or float(epistemic) >= float(row["ddl_seconds"]),
            f"{scenario}: epistemic alert precedes DDL",
        )
        require(
            float(row.get("first_substantive_alert_seconds", -1))
            >= float(row["ddl_seconds"]),
            f"{scenario}: substantive alert precedes first honest verdict",
        )
        require(
            float(row.get("exact_set_latency_seconds", -1))
            >= float(row["first_substantive_alert_seconds"]),
            f"{scenario}: exact latency precedes substantive alert",
        )
        if scenario in {"S1", "S3"}:
            require(
                row.get("argocd_gatekeeper_native_validation") is True
                and row.get("shared_adapter_only") is False,
                f"{scenario}: native replication scope mislabeled",
            )
        else:
            require(
                row.get("argocd_gatekeeper_native_validation") is False
                and row.get("shared_adapter_only") is True,
                "S4 must remain a shared-adapter observation",
            )
        if scenario == "S3":
            require(
                row.get("final_gatekeeper_joined_uid") == row.get("deployment_uid")
                and bool(row.get("deployment_uid")),
                "S3 Gatekeeper violation was not subject-linked to the live UID",
            )
            require(
                row.get("gatekeeper_emitted_resource_uid") is True
                and row.get("gatekeeper_structural_uid_field") is False,
                "S3 did not preserve the embedded-versus-structural UID boundary",
            )

    require(set(grouped) == set(EXPECTED), "scenario set changed")
    for scenario, scenario_rows in grouped.items():
        require(len(scenario_rows) == 5, f"{scenario}: expected five repetitions")
        require(
            sorted(int(row["repetition"]) for row in scenario_rows) == [1, 2, 3, 4, 5],
            f"{scenario}: repetition identities changed",
        )

    expected_keys = {
        (scenario, repetition)
        for scenario in EXPECTED
        for repetition in range(1, 6)
    }
    observation_by_key = {
        (str(row["scenario"]), int(row["repetition"])): row for row in rows
    }

    def marker_map(record_type: str) -> dict[tuple[str, int], dict[str, Any]]:
        result: dict[tuple[str, int], dict[str, Any]] = {}
        for marker in raw:
            if marker.get("record_type") != record_type:
                continue
            key = (str(marker.get("scenario")), int(marker.get("repetition", 0)))
            require(key not in result, f"duplicate {record_type} for {key}")
            result[key] = marker
        require(set(result) == expected_keys, f"{record_type} coverage is incomplete")
        return result

    injection_markers = marker_map("injection_onset_marker")
    baseline_markers = marker_map("baseline_observation_marker")
    sync_markers = marker_map("baseline_sync_marker")
    polls_by_type: dict[str, dict[tuple[str, int], list[dict[str, Any]]]] = {
        name: defaultdict(list)
        for name in ("baseline_sync_poll", "baseline_poll", "scenario_poll")
    }
    for raw_row in raw:
        record_type = raw_row.get("record_type")
        if record_type not in polls_by_type:
            continue
        key = (str(raw_row.get("scenario")), int(raw_row.get("repetition", 0)))
        polls_by_type[record_type][key].append(raw_row)
        if record_type in {"baseline_poll", "scenario_poll"}:
            components = raw_row.get("components", {})
            require(components.get("intent") == "not_evaluated", "raw intent claim changed")
            require(components.get("environment") == "not_evaluated", "raw environment claim changed")
    for record_type, by_key in polls_by_type.items():
        require(set(by_key) == expected_keys, f"{record_type} coverage is incomplete")

    restorations = [
        row for row in raw if row.get("record_type") == "baseline_restoration"
    ]
    require(len(restorations) == 15, "expected 15 Argo baseline restorations")
    for restoration in restorations:
        require(
            restoration.get("after_desired_leaf_differences") == [],
            "Argo reset left an explicit desired/live leaf difference",
        )
        operation = restoration.get("argo_operation", {})
        require(operation.get("phase") == "Succeeded", "Argo reset did not succeed")
        require(operation.get("revision"), "Argo reset revision is absent")

    raw_timing_by_key: dict[tuple[str, int], dict[str, Any]] = {}
    for scenario, repetition in sorted(expected_keys):
        key = (scenario, repetition)
        label = f"{scenario} r{repetition}"

        baseline_marker = baseline_markers[key]
        require(
            baseline_marker.get("reference_kind") == "baseline-observation-start",
            f"{label}: baseline reference changed",
        )
        baseline_reference = baseline_marker.get("timing_reference_id")
        require(
            isinstance(baseline_reference, str) and bool(baseline_reference),
            f"{label}: baseline timing identity absent",
        )
        parse_utc_field(baseline_marker, "reference_utc", f"{label} baseline marker")
        require(
            numeric_field(
                baseline_marker,
                "reference_monotonic_seconds",
                f"{label} baseline marker",
            ) >= 0.0,
            f"{label}: invalid baseline monotonic reference",
        )
        baseline_polls = polls_by_type["baseline_poll"][key]
        require(
            [int(row.get("poll_index", 0)) for row in baseline_polls]
            == list(range(1, len(baseline_polls) + 1)),
            f"{label}: baseline poll indexes are not contiguous",
        )
        previous_completion = -1.0
        for poll in baseline_polls:
            poll_label = f"{label} baseline poll {poll.get('poll_index')}"
            completed = validate_poll_timing(
                poll,
                reference_id=baseline_reference,
                start_field="evaluation_started_since_reference_seconds",
                completion_field="evaluation_completed_since_reference_seconds",
                label=poll_label,
            )
            require(completed >= previous_completion, f"{poll_label}: completion time regressed")
            previous_completion = completed
            validate_classification_snapshot(poll, exact_field="exact_empty", label=poll_label)
        require(
            baseline_polls[-1].get("exact_empty") is True,
            f"{label}: terminal baseline was not exactly empty",
        )

        sync_marker = sync_markers[key]
        require(
            sync_marker.get("reference_kind") == "argo-baseline-sync-start",
            f"{label}: sync reference changed",
        )
        sync_reference = sync_marker.get("timing_reference_id")
        require(
            isinstance(sync_reference, str) and bool(sync_reference),
            f"{label}: sync timing identity absent",
        )
        parse_utc_field(sync_marker, "reference_utc", f"{label} sync marker")
        numeric_field(sync_marker, "reference_monotonic_seconds", f"{label} sync marker")
        sync_polls = polls_by_type["baseline_sync_poll"][key]
        require(
            [int(row.get("poll_index", 0)) for row in sync_polls]
            == list(range(1, len(sync_polls) + 1)),
            f"{label}: sync poll indexes are not contiguous",
        )
        for poll in sync_polls:
            poll_label = f"{label} sync poll {poll.get('poll_index')}"
            validate_poll_timing(
                poll,
                reference_id=sync_reference,
                start_field="evaluation_started_since_reference_seconds",
                completion_field="evaluation_completed_since_reference_seconds",
                label=poll_label,
            )
            expected_sync_classification = {
                "new_history": poll.get("new_history"),
                "operation_phase": poll.get("operation_phase"),
                "terminal_success": (
                    poll.get("new_history") is True
                    and poll.get("operation_phase") == "Succeeded"
                ),
                "terminal_failure": (
                    poll.get("new_history") is True
                    and poll.get("operation_phase") in {"Error", "Failed"}
                ),
            }
            require(
                poll.get("classification_at_completion")
                == expected_sync_classification,
                f"{poll_label}: completion classification mismatch",
            )
        require(
            sync_polls[-1].get("classification_at_completion", {}).get("terminal_success")
            is True,
            f"{label}: sync trace does not terminate successfully",
        )

        scenario_polls = polls_by_type["scenario_poll"][key]
        timing = recompute_scenario_timing(
            injection_markers[key],
            scenario_polls,
            {EXPECTED[scenario]},
        )
        raw_timing_by_key[key] = timing
        observation = observation_by_key[key]
        for field in (
            "schedule_index",
            "injection_id",
            "timing_reference_id",
            "injection_utc",
            "onset_utc",
            "first_honest_verdict_kind",
            "first_observed_set",
            "final_observed_set",
            "polls_to_exact",
        ):
            require(
                observation.get(field) == timing[field],
                f"{label}: observation {field} disagrees with raw timing",
            )
        for field in (
            "actuation_seconds",
            "ddl_seconds",
            "first_substantive_alert_seconds",
            "exact_set_latency_seconds",
            "evidence_latency_seconds",
        ):
            require(
                math.isclose(
                    float(observation.get(field, -1)),
                    float(timing[field]),
                    rel_tol=0.0,
                    abs_tol=5e-7,
                ),
                f"{label}: observation {field} disagrees with raw timing",
            )
        require(
            observation.get("first_epistemic_alert_seconds")
            == timing["first_epistemic_alert_seconds"],
            f"{label}: epistemic timing disagrees with raw timing",
        )

        final = scenario_polls[-1]
        if scenario == "S1":
            require(
                final.get("evidence", {}).get("argo", {}).get("application_sync_status") == "OutOfSync",
                f"S1 r{repetition}: Argo native OutOfSync evidence absent",
            )
        elif scenario == "S3":
            gatekeeper = final.get("evidence", {}).get("gatekeeper", {})
            require(gatekeeper.get("matching_violation_count", 0) >= 1, f"S3 r{repetition}: violation absent")
            require(gatekeeper.get("audit_fresh_for_injection") is True, f"S3 r{repetition}: stale audit")
            require(
                gatekeeper.get("uid_join_source")
                == "gatekeeper-policy-message-embedded-object-uid",
                f"S3 r{repetition}: engine-emitted UID join source absent",
            )
            require(
                gatekeeper.get("gatekeeper_structural_uid_field") is False
                and gatekeeper.get("gatekeeper_message_uids") == [final.get("deployment_uid")],
                f"S3 r{repetition}: embedded object UID is not uniquely linked",
            )
            require(
                all(
                    violation.get("enforcement_action") == "dryrun"
                    for violation in gatekeeper.get("violation_identity", [])
                ),
                f"S3 r{repetition}: non-dryrun violation",
            )
        elif scenario == "S4":
            artifact = final.get("evidence", {}).get("artifact", {})
            require(
                artifact.get("independent_argocd_gatekeeper_authorization_validation") is False,
                f"S4 r{repetition}: authorization independence overstated",
            )
            require(
                set(artifact.get("running_image_ids", [])).isdisjoint(
                    artifact.get("approved_image_ids", [])
                ),
                f"S4 r{repetition}: substituted digest was covered",
            )

    summaries: list[dict[str, Any]] = []
    for scenario in ("S1", "S3", "S4"):
        scenario_timings = [
            raw_timing_by_key[(scenario, repetition)]
            for repetition in range(1, 6)
        ]
        ddl = [float(row["ddl_seconds"]) for row in scenario_timings]
        epistemic = [
            float(row["first_epistemic_alert_seconds"])
            for row in scenario_timings
            if row["first_epistemic_alert_seconds"] is not None
        ]
        substantive = [
            float(row["first_substantive_alert_seconds"])
            for row in scenario_timings
        ]
        exact_latency = [
            float(row["exact_set_latency_seconds"]) for row in scenario_timings
        ]
        summaries.append(
            {
                "scenario": scenario,
                "expected_set": EXPECTED[scenario],
                "surface": SURFACE[scenario],
                "repetitions": len(scenario_timings),
                "exact_sets": len(scenario_timings),
                "median_ddl_seconds": round(statistics.median(ddl), 6),
                "min_ddl_seconds": round(min(ddl), 6),
                "max_ddl_seconds": round(max(ddl), 6),
                "epistemic_alert_observations": len(epistemic),
                "median_first_epistemic_alert_seconds": (
                    round(statistics.median(epistemic), 6) if epistemic else None
                ),
                "median_first_substantive_alert_seconds": round(
                    statistics.median(substantive), 6
                ),
                "median_exact_latency_seconds": round(statistics.median(exact_latency), 6),
                "native_cross_stack_component_replication": scenario in {"S1", "S3"},
            }
        )

    with (results / "cross_stack_profile_summary.csv").open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(summaries[0]), lineterminator="\n")
        writer.writeheader()
        writer.writerows(summaries)

    summary = {
        "campaign": "bounded Argo CD + Gatekeeper cross-stack replication",
        "cluster": "govdrift-cross",
        "versions": {"argocd": "v3.4.2", "gatekeeper": "v3.22.2"},
        "profiles": summaries,
        "aggregate": {
            "observations": len(raw_timing_by_key),
            "exact_sets": len(raw_timing_by_key),
            "slices": len(grouped),
            "repetitions_per_slice": 5,
            "native_cross_stack_slices": 2,
            "shared_adapter_slices": 1,
            "baseline_restorations_succeeded": len(restorations),
            "post_restoration_differences": 0,
            "final_undecidable_observations": 0,
            "api_read_errors": status["api_read_errors"],
            "cleanup_verified": cleanup["verified_absent"],
            "stop_rule_triggered": False,
        },
        "resource_maxima": resource_maxima,
        "descriptive_comparison": {
            "S1": "Argo CD reproduced the desired/live configuration distinction previously exercised through Flux.",
            "S3": "Gatekeeper dry-run background audit reproduced the current-policy violation path with the evaluated object UID embedded by the controlled Rego rule.",
            "S4": "The running-digest observation reused the shared T3 adapter and is not independent Argo CD/Gatekeeper authorization validation.",
            "inference": (
                "Descriptive concordance on three bounded slices only. No equivalence, "
                "non-inferiority, prevalence, reliability, or production-performance test was performed."
            ),
        },
        "unsupported_components": {
            "intent": "not evaluated",
            "environment": "not evaluated",
            "general_authorization": "not independently evaluated; S4 is a shared digest adapter",
        },
    }
    (results / "cross_stack_summary.json").write_text(json.dumps(summary, indent=2) + "\n")

    table = [
        "% Generated by scripts/analyze_cross_stack.py",
        r"\begin{tabular}{@{}llllrrrrrr@{}}",
        r"\toprule",
        r"Slice & Expected & Evidence surface & $n$ & Exact & Median DDL & Median substantive & Median ESC & Min DDL & Max DDL \\",
        r" & & & & & \multicolumn{5}{c}{seconds} \\",
        r"\midrule",
    ]
    for row in summaries:
        table.append(
            f"{row['scenario']} & {tex(row['expected_set'])} & {tex(row['surface'])} & "
            f"{row['repetitions']} & {row['exact_sets']} & "
            f"{row['median_ddl_seconds']:.3f} & "
            f"{row['median_first_substantive_alert_seconds']:.3f} & "
            f"{row['median_exact_latency_seconds']:.3f} & "
            f"{row['min_ddl_seconds']:.3f} & "
            f"{row['max_ddl_seconds']:.3f} \\\\"
        )
    table.extend([r"\bottomrule", r"\end{tabular}"])
    (results / "table_cross_stack.tex").write_text("\n".join(table) + "\n")

    readme = f"""# Argo CD + Gatekeeper cross-stack replication

This directory preserves an executed, bounded replication on a separate Kind
cluster named `govdrift-cross`. The official Argo CD v3.4.2 and Gatekeeper
v3.22.2 manifests were downloaded from their tagged upstream repositories,
verified against the pinned SHA-256 values, and preserved under `upstream/`.

The campaign ran five repetitions each of S1, S3, and S4. It produced
{len(rows)}/15 exact projected singleton classifications over the declared
evaluated components. S1 used Argo CD's native
desired/live status; S3 used a fresh Gatekeeper background-audit violation
with `enforcementAction: dryrun`. Gatekeeper's violation record did not emit a
structural resource-UID field, so the controlled Rego rule embeds the
evaluated object's UID in the violation message. The adapter accepts a polar
policy result only when that engine-emitted UID equals the live Deployment
UID; absence or mismatch is undecidable.

Every scenario began after an explicit Argo sync to the validated Git revision.
All {len(restorations)}/15 restoration operations reached `Succeeded`, and a
leaf-by-leaf check of the pinned desired manifest found zero residual differences
after restoration. Instrumented Kubernetes reads recorded zero API errors, and
all 15 final observations had zero undecidable evaluated components.

S4 is deliberately labeled `shared-artifact-adapter`: the running image digest
was outside the approved digest set, but this is not independent authorization
validation by Argo CD or Gatekeeper. Intent and environment/inventory were not
evaluated in this replication.

The S3 subject link covers one object lifetime in this controlled campaign;
it does not claim continuation-safe identity across resource recreation.

The comparison with the primary Flux + Kyverno laboratory is descriptive. It
tests bounded realizability of corresponding evidence paths; it is not an
equivalence, non-inferiority, prevalence, reliability, or production-latency
study. First-honest DDL is time from operational onset to the first honest
non-consistent or undecidable verdict, so it may be epistemic rather than a
substantive class detection. First-substantive latency is reported separately.
ESC ends at the first exact projected classification over the declared
evaluated components; it is not watermark-qualified Stable-VCL.

The raw trace preserves injection/onset and baseline reference markers plus
evaluation-start, evaluation-completion, duration, and completion-classification
fields for every poll. The analyzer reconstructs DDL, first epistemic alert,
first substantive alert, and exact-classification latency from those fields.

The resource stop rule was 80% sustained for three five-second samples on host
CPU, host memory-pressure utilization, normalized Kind-node CPU, or Kind-node
memory. It did not trigger. Installation also had a strict 15-minute Ready
deadline. `cleanup.json` verifies that only `govdrift-cross` was deleted after
capture.

- `cross_stack_raw.ndjson`: every baseline and scenario poll.
- `cross_stack_observations.json` / `.csv`: one result per repetition.
- `install_events.ndjson`: manifest, readiness, and setup provenance.
- `resource_samples.ndjson`: stop-rule inputs.
- `manifest_checksums.csv`: upstream, local-stack, and campaign-source SHA-256 inventory.
- `platform.json`: exact platform, source state, images, clock, and validation boundary.
- `cross_stack_summary.json`: validated descriptive summary.
- `table_cross_stack.tex`: manuscript-ready table.
- `cleanup.json`: deletion command plus pre/post verification stdout, stderr,
  return codes, parsed cluster sets, exact target, and postcondition.
"""
    (results / "README.md").write_text(readme)

    print(json.dumps(summary["aggregate"], indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
