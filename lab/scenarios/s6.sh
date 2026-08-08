#!/usr/bin/env bash
# S6: commit predecessor content without new approval; Flux converges.
set -euo pipefail
LAB="$(cd "$(dirname "$0")/.." && pwd)"; RUNTIME="$LAB/runtime"
cp "$LAB/manifests/deployment-predecessor.yaml" "$RUNTIME/work/payments/deployment.yaml"
git -C "$RUNTIME/work" add payments/deployment.yaml
git -C "$RUNTIME/work" commit -m "rollback to predecessor without approval"
git -C "$RUNTIME/work" push origin HEAD:master
