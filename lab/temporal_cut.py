#!/usr/bin/env python3
"""Dependency-free executable contract for admissible temporal cuts.

The live laboratory reads several adapters synchronously.  This module makes
the stronger temporal contract in the manuscript executable without claiming
that those adapters already publish production watermarks.  A rejected cut is
an epistemic failure for the affected component; callers must never translate
it into a polar conformance verdict.
"""

from __future__ import annotations

import math
from typing import Any


def _number(value: Any, label: str) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label} is not numeric") from exc
    if not math.isfinite(result):
        raise ValueError(f"{label} is not finite")
    return result


def select_admissible_cut(
    records_by_source: dict[str, list[dict[str, Any]]],
    contracts: dict[str, dict[str, Any]],
    *,
    required_sources: set[str],
    unit_ref: str,
    theta: float,
    max_spread_seconds: float,
) -> tuple[bool, str, dict[str, dict[str, Any]]]:
    """Select the unique latest record per source at an admissible cut.

    Each contract must publish ``watermark``, ``freshness_seconds``, and a
    non-negative ``clock_error_bound_seconds``.  Record capture intervals are
    assumed already expanded by that bound and use ``capture_start`` and
    ``capture_end`` in one reference-time domain.
    """

    try:
        cut = _number(theta, "theta")
        max_spread = _number(max_spread_seconds, "max_spread_seconds")
    except ValueError as exc:
        return False, str(exc), {}
    if max_spread < 0:
        return False, "max_spread_seconds is negative", {}
    if not required_sources:
        return False, "required source set is empty", {}

    selected: dict[str, dict[str, Any]] = {}
    intervals: dict[str, tuple[float, float]] = {}
    for source in sorted(required_sources):
        contract = contracts.get(source)
        if not isinstance(contract, dict):
            return False, f"{source}: missing temporal contract", {}
        try:
            watermark = _number(contract["watermark"], f"{source}.watermark")
            freshness = _number(
                contract["freshness_seconds"], f"{source}.freshness_seconds"
            )
            clock_bound = _number(
                contract["clock_error_bound_seconds"],
                f"{source}.clock_error_bound_seconds",
            )
        except KeyError as exc:
            return False, f"{source}: missing {exc.args[0]}", {}
        except ValueError as exc:
            return False, str(exc), {}
        if freshness < 0 or clock_bound < 0:
            return False, f"{source}: negative temporal bound", {}
        if watermark < cut:
            return False, f"{source}: watermark has not closed the cut", {}

        linked: list[tuple[dict[str, Any], float, float]] = []
        for record in records_by_source.get(source, []):
            if record.get("unit_ref") != unit_ref:
                continue
            try:
                start = _number(record["capture_start"], f"{source}.capture_start")
                end = _number(record["capture_end"], f"{source}.capture_end")
            except KeyError as exc:
                return False, f"{source}: record missing {exc.args[0]}", {}
            except ValueError as exc:
                return False, str(exc), {}
            if start > end:
                return False, f"{source}: inverted capture interval", {}
            if start <= cut < end:
                return False, f"{source}: capture interval straddles the cut", {}
            if end <= cut:
                linked.append((record, start, end))

        if not linked:
            return False, f"{source}: no linked record at or before the cut", {}
        latest_end = max(end for _, _, end in linked)
        latest = [item for item in linked if item[2] == latest_end]
        if len(latest) != 1:
            return False, f"{source}: latest record is ambiguous", {}
        record, start, end = latest[0]
        if cut - end > freshness:
            return False, f"{source}: selected record is stale", {}
        selected[source] = record
        intervals[source] = (start, end)

    spread = max(end for _, end in intervals.values()) - min(
        start for start, _ in intervals.values()
    )
    if spread > max_spread:
        return False, "cross-source temporal spread exceeded", {}
    return True, "admissible", selected
