#!/usr/bin/env bash
# Approved-state recorder: writes G_app(t0) to an append-only directory.
set -euo pipefail
LAB="$(cd "$(dirname "$0")" && pwd)"
OUT="$LAB/gapp/$(date -u +%Y%m%dT%H%M%SZ)"; mkdir -p "$OUT"
(cd "$LAB/work" && git rev-parse HEAD) > "$OUT/git_revision"
kubectl -n payments get deploy payments -o yaml > "$OUT/manifest.yaml"
kubectl -n payments get deploy payments \
  -o jsonpath='{.spec.template.spec.containers[0].image}' > "$OUT/image_ref"
skopeo inspect --format '{{.Digest}}' "docker://$(cat "$OUT/image_ref")" \
  > "$OUT/resolved_digest" 2>/dev/null || echo "unresolved" > "$OUT/resolved_digest"
kubectl get clusterpolicy governance-baseline \
  -o jsonpath='{.metadata.labels.policy-version}' > "$OUT/policy_version"
cp -r "$LAB/approvals" "$OUT/approvals"
cp "$LAB/cloud-inventory.json" "$OUT/sigma0.json"
chmod -R a-w "$OUT"
echo "G_app recorded at $OUT"
