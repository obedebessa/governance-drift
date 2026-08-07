#!/usr/bin/env bash
# S8: approval subject mismatch -- corrupt the approval's subject digest.
LAB="$(cd "$(dirname "$0")/.." && pwd)"; RUNTIME="$LAB/runtime"
for f in "$RUNTIME"/approvals/APR-*.json; do
  jq '.subjects = ["sha256:ccc333"]' "$f" > "$f.t" && mv "$f.t" "$f"
done
