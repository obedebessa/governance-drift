#!/usr/bin/env python3
"""Validate the bounded related-work scoping ledger and report derived counts.

This script is intentionally offline and read-only. It does not rerun web
searches, alter the ledger, or claim that the ledger is a literature census.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
from collections import Counter
from datetime import date
from pathlib import Path


CUTOFF = date(2026, 8, 8)
REQUIRED_COLUMNS = (
    "record_id",
    "query_ids",
    "decision",
    "evidence_class",
    "work",
    "year",
    "locator",
    "scope_note",
    "verified_date",
)
ALLOWED_DECISIONS = {
    "include_same_term",
    "include_adjacent",
    "include_foundation",
    "exclude_scope",
}


def parse_args() -> argparse.Namespace:
    repository = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "ledger",
        nargs="?",
        type=Path,
        default=repository / "literature" / "scoping_review_records.csv",
        help="CSV decision ledger (defaults to the repository artifact)",
    )
    return parser.parse_args()


def validate(path: Path) -> dict[str, object]:
    raw = path.read_bytes()
    with path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        if tuple(reader.fieldnames or ()) != REQUIRED_COLUMNS:
            raise ValueError(
                f"unexpected columns: {reader.fieldnames!r}; "
                f"expected {list(REQUIRED_COLUMNS)!r}"
            )
        rows = list(reader)

    record_ids: set[str] = set()
    locators: set[str] = set()
    decisions: Counter[str] = Counter()
    evidence_classes: Counter[str] = Counter()

    for line_number, row in enumerate(rows, start=2):
        missing = [column for column in REQUIRED_COLUMNS if not row[column].strip()]
        if missing:
            raise ValueError(f"line {line_number}: empty fields {missing}")

        record_id = row["record_id"]
        if not re.fullmatch(r"SR\d{3}", record_id):
            raise ValueError(f"line {line_number}: invalid record_id {record_id!r}")
        if record_id in record_ids:
            raise ValueError(f"line {line_number}: duplicate record_id {record_id!r}")
        record_ids.add(record_id)

        query_ids = row["query_ids"].split(";")
        if not all(re.fullmatch(r"Q[1-8]", query_id) for query_id in query_ids):
            raise ValueError(f"line {line_number}: invalid query_ids {query_ids!r}")

        decision = row["decision"]
        if decision not in ALLOWED_DECISIONS:
            raise ValueError(f"line {line_number}: invalid decision {decision!r}")
        decisions[decision] += 1
        evidence_classes[row["evidence_class"]] += 1

        year = row["year"]
        if year != "undated" and not re.fullmatch(r"(?:19|20)\d{2}", year):
            raise ValueError(f"line {line_number}: invalid year {year!r}")
        if year != "undated" and int(year) > CUTOFF.year:
            raise ValueError(f"line {line_number}: post-cutoff year {year!r}")

        locator = row["locator"]
        if not locator.startswith("https://"):
            raise ValueError(f"line {line_number}: locator is not HTTPS")
        if locator in locators:
            raise ValueError(f"line {line_number}: duplicate locator {locator!r}")
        locators.add(locator)

        verified = date.fromisoformat(row["verified_date"])
        if verified > CUTOFF:
            raise ValueError(
                f"line {line_number}: verification date {verified} exceeds {CUTOFF}"
            )

    included = sum(
        count for decision, count in decisions.items() if decision.startswith("include_")
    )
    excluded = sum(
        count for decision, count in decisions.items() if decision.startswith("exclude_")
    )
    return {
        "review_type": "bounded scoping review (not systematic)",
        "cutoff": CUTOFF.isoformat(),
        "ledger": str(path),
        "ledger_sha256": hashlib.sha256(raw).hexdigest(),
        "records": len(rows),
        "included": included,
        "excluded": excluded,
        "by_decision": dict(sorted(decisions.items())),
        "by_evidence_class": dict(sorted(evidence_classes.items())),
    }


def main() -> None:
    result = validate(parse_args().ledger.resolve())
    print(json.dumps(result, indent=2, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
