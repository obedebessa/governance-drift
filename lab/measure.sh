#!/usr/bin/env bash
# Measurement harness: runs a scenario, polls tier evaluators at their
# cadence, records first-alarm wall-clock times and classes, and appends a
# CSV row per tier. Churn generator (churn.sh) must be running. Repeat with
# -n N for N trials; drift-free windows measure false alarms.
set -euo pipefail
LAB="$(cd "$(dirname "$0")" && pwd)"; SCN="$1"; CADENCE="${2:-30}"
inject_ts=$(date -u +%s)
"$LAB/scenarios/$SCN.sh"
echo "$SCN injected at $inject_ts (cadence ${CADENCE}s)"
declare -A first
while :; do
  for tier in T0n T1 T2 T3 T4; do
    if [ -z "${first[$tier]:-}" ]; then
      cls=$("$LAB/evaluate.sh" "$tier" || true)
      if [ -n "$cls" ]; then
        first[$tier]="$(date -u +%s),$cls"
        echo "$SCN,$tier,$inject_ts,${first[$tier]}" >> "$LAB/log/ttd.csv"
      fi
    fi
  done
  [ ${#first[@]} -eq 5 ] && break
  sleep "$CADENCE"
done
