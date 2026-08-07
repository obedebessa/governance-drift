#!/usr/bin/env bash
set -euo pipefail
LAB="$(cd "$(dirname "$0")" && pwd)"
exec python3 "$LAB/evaluator.py" --tier "${1:-T4}" --plain
