#!/usr/bin/env bash
# S5: IAM expansion out of band (environment drift, mock inventory).
LAB="$(cd "$(dirname "$0")/.." && pwd)"; RUNTIME="$LAB/runtime"
jq '.iam_scope = "broadened"' "$RUNTIME/cloud-inventory.json" > "$RUNTIME/.t" \
  && mv "$RUNTIME/.t" "$RUNTIME/cloud-inventory.json"
