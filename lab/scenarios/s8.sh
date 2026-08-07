#!/usr/bin/env bash
# S8: approval subject mismatch -- corrupt the approval's subject digest.
LAB="$(cd "$(dirname "$0")/.." && pwd)"
for f in "$LAB"/approvals/APR-*.json; do
  jq '.subjects = ["sha256:ccc333"]' "$f" > "$f.t" && mv "$f.t" "$f"
done
