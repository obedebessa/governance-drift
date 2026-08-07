#!/usr/bin/env bash
# Record the approved state used as the laboratory reference.
set -euo pipefail
LAB="$(cd "$(dirname "$0")" && pwd)"
RUNTIME="$LAB/runtime"
OUT="$RUNTIME/gapp/$(date -u +%Y%m%dT%H%M%SZ)"
mkdir -p "$OUT"
git -C "$RUNTIME/work" rev-parse HEAD > "$OUT/git_revision"
kubectl -n payments get deployment payments -o json > "$OUT/manifest.json"
kubectl -n payments get pod -l app=payments -o json > "$OUT/pods.json"
kubectl get clusterpolicy governance-baseline -o json > "$OUT/policy.json"
cp -R "$RUNTIME/approvals" "$OUT/approvals"
cp "$RUNTIME/cloud-inventory.json" "$OUT/sigma0.json"
chmod -R a-w "$OUT"
echo "$OUT" > "$RUNTIME/gapp_latest"
echo "G_app recorded at $OUT"
