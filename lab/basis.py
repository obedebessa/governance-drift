#!/usr/bin/env python3
"""Activation-aware admitted-basis selection for the live laboratory."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Iterable

from snapshot_integrity import verify_snapshot


class BasisSelectionError(LookupError):
    """The admitted basis is absent, malformed, cyclic, or ambiguous."""


def _covers(record: dict, subject: str, environment: str) -> bool:
    scope = record.get("scope", {})
    subjects = scope.get("subjects", [])
    environments = scope.get("environments", [])
    return ("*" in subjects or subject in subjects) and (
        "*" in environments or environment in environments
    )


def select_basis(
    records: Iterable[dict], *, subject: str, environment: str, now: float
) -> dict:
    """Return the unique activated maximal record for ``(subject, environment)``.

    Approval timestamps never select a record. Pending and aborted snapshots
    are excluded, while expiry or revocation of an authorization instrument is
    deliberately irrelevant to selection. Overlapping activated snapshots
    must be ordered by explicit ``supersedes`` edges; otherwise selection fails
    closed instead of using a timestamp tie-breaker.
    """
    rows = list(records)
    by_id: dict[str, dict] = {}
    for row in rows:
        identifier = row.get("id")
        if not isinstance(identifier, str) or not identifier:
            raise BasisSelectionError("snapshot metadata has no stable id")
        if identifier in by_id:
            raise BasisSelectionError(f"duplicate snapshot id: {identifier}")
        by_id[identifier] = row

    candidates: dict[str, dict] = {}
    for identifier, row in by_id.items():
        state = row.get("state")
        activated_at = row.get("activated_at")
        if state != "activated" or activated_at is None:
            continue
        try:
            active = float(activated_at) <= float(now)
        except (TypeError, ValueError) as exc:
            raise BasisSelectionError(
                f"snapshot {identifier} has malformed activation time"
            ) from exc
        if active and _covers(row, subject, environment):
            candidates[identifier] = row
    if not candidates:
        raise BasisSelectionError("no activated snapshot covers the deployment")

    visiting: set[str] = set()
    memo: dict[str, set[str]] = {}

    def ancestors(identifier: str) -> set[str]:
        if identifier in memo:
            return memo[identifier]
        if identifier in visiting:
            raise BasisSelectionError("supersedes relation contains a cycle")
        visiting.add(identifier)
        found: set[str] = set()
        for predecessor in by_id[identifier].get("supersedes", []):
            if predecessor not in by_id:
                raise BasisSelectionError(
                    f"snapshot {identifier} supersedes unknown id {predecessor}"
                )
            found.add(predecessor)
            found.update(ancestors(predecessor))
        visiting.remove(identifier)
        memo[identifier] = found
        return found

    for identifier in by_id:
        ancestors(identifier)

    maxima = [
        identifier
        for identifier in candidates
        if not any(
            identifier in ancestors(other)
            for other in candidates
            if other != identifier
        )
    ]
    if len(maxima) != 1:
        raise BasisSelectionError(
            "activated snapshot overlap is ambiguous: " + ", ".join(sorted(maxima))
        )
    return candidates[maxima[0]]


def snapshot_records(runtime: Path) -> list[dict]:
    """Load activation metadata from the append-only snapshot directory."""
    root = runtime / "gapp"
    records: list[dict] = []
    for directory in sorted(path for path in root.glob("*") if path.is_dir()):
        metadata = directory / "metadata.json"
        if not metadata.exists():
            continue
        row = json.loads(metadata.read_text())
        try:
            verify_snapshot(directory, row)
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            raise BasisSelectionError(f"snapshot integrity failure: {exc}") from exc
        row["path"] = str(directory)
        records.append(row)
    return records


def select_basis_directory(
    runtime: Path,
    *,
    subject: str,
    environment: str,
    now: float | None = None,
) -> Path:
    """Resolve the selected snapshot directory, with v1.5 runtime fallback."""
    records = snapshot_records(runtime)
    if records:
        selected = select_basis(
            records,
            subject=subject,
            environment=environment,
            now=time.time() if now is None else now,
        )
        return Path(selected["path"])

    # Existing frozen v1.5 runtimes predate activation metadata. The fallback
    # preserves inspectability; every v1.6 bootstrap writes metadata and uses
    # the activation-aware path above.
    pointer = runtime / "gapp_latest"
    if not pointer.exists():
        raise BasisSelectionError("no admitted-basis history is available")
    return Path(pointer.read_text().strip())
