#!/usr/bin/env bash
# S12: unapproved rollback plus loss of continuing-authorization live status.
set -euo pipefail
LAB="$(cd "$(dirname "$0")/.." && pwd)"
bash "$LAB/scenarios/s6.sh"
bash "$LAB/scenarios/s9.sh"
