#!/usr/bin/env bash
# S7: out-of-band cloud modification (environment drift, mock inventory).
LAB="$(cd "$(dirname "$0")/.." && pwd)"; RUNTIME="$LAB/runtime"
jq '.cloud_lb = "tls-policy-downgraded"' "$RUNTIME/cloud-inventory.json" \
  > "$RUNTIME/.t" && mv "$RUNTIME/.t" "$RUNTIME/cloud-inventory.json"
