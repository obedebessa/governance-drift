#!/usr/bin/env bash
# S11: untrusted artifact substitution plus environment predicate violation.
set -euo pipefail
LAB="$(cd "$(dirname "$0")/.." && pwd)"
bash "$LAB/scenarios/s4.sh"
bash "$LAB/scenarios/s5.sh"
