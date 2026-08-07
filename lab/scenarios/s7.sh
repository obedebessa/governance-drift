#!/usr/bin/env bash
# S7: out-of-band cloud modification (environment drift, mock inventory).
LAB="$(cd "$(dirname "$0")/.." && pwd)"
jq '.cloud_lb = "tls-policy-downgraded"' "$LAB/cloud-inventory.json" \
  > "$LAB/.t" && mv "$LAB/.t" "$LAB/cloud-inventory.json"
