#!/usr/bin/env bash
# S5: IAM expansion out of band (environment drift, mock inventory).
LAB="$(cd "$(dirname "$0")/.." && pwd)"
jq '.iam_scope = "broadened"' "$LAB/cloud-inventory.json" > "$LAB/.t" \
  && mv "$LAB/.t" "$LAB/cloud-inventory.json"
