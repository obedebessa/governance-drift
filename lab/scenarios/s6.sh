#!/usr/bin/env bash
# S6: git rollback without new approval (intent drift); Flux converges.
LAB="$(cd "$(dirname "$0")/.." && pwd)"
cd "$LAB/work" && git revert --no-edit HEAD && git push origin HEAD
