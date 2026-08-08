#!/usr/bin/env bash
# S10: current-policy failure plus expired temporary exception.
set -euo pipefail
LAB="$(cd "$(dirname "$0")/.." && pwd)"
bash "$LAB/scenarios/s3.sh"
bash "$LAB/scenarios/s2.sh"
