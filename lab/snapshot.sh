#!/usr/bin/env bash
# Record the approved state used as the laboratory reference.
set -euo pipefail
LAB="$(cd "$(dirname "$0")" && pwd)"
RUNTIME="$LAB/runtime"
STATE="${SNAPSHOT_STATE:-activated}"
case "$STATE" in
  activated|pending|aborted) ;;
  *) echo "invalid SNAPSHOT_STATE: $STATE" >&2; exit 2 ;;
esac
STAMP="$(date -u +%Y%m%dT%H%M%SZ)-$(python3 -c 'import time; print(time.time_ns())')"
OUT="$RUNTIME/gapp/$STAMP"
NOW_EPOCH="$(python3 -c 'import time; print(time.time())')"
APPROVED_AT="${APPROVED_AT_EPOCH:-$NOW_EPOCH}"
PREVIOUS=""
if [ -f "$RUNTIME/gapp_latest" ]; then
  PREVIOUS_PATH="$(cat "$RUNTIME/gapp_latest")"
  if [ -f "$PREVIOUS_PATH/metadata.json" ]; then
    PREVIOUS="$(basename "$PREVIOUS_PATH")"
  fi
fi
mkdir -p "$OUT"
git -C "$RUNTIME/work" rev-parse HEAD > "$OUT/git_revision"
kubectl -n payments get deployment payments -o json > "$OUT/manifest.json"
kubectl -n payments get pod -l app=payments -o json > "$OUT/pods.json"
kubectl get clusterpolicy governance-baseline -o json > "$OUT/policy.json"
cp -R "$RUNTIME/approvals" "$OUT/approvals"
cp "$RUNTIME/cloud-inventory.json" "$OUT/sigma0.json"
if [ "$STATE" = "activated" ]; then
  ACTIVATED_AT="$NOW_EPOCH"
else
  ACTIVATED_AT="null"
fi
jq -n \
  --arg id "$STAMP" \
  --arg state "$STATE" \
  --arg predecessor "$PREVIOUS" \
  --argjson approved_at "$APPROVED_AT" \
  --argjson activated_at "$ACTIVATED_AT" \
  '{id:$id, approved_at:$approved_at, activated_at:$activated_at, state:$state,
    scope:{subjects:["payments"],environments:["payments"]},
    supersedes:(if $predecessor == "" then [] else [$predecessor] end)}' \
  > "$OUT/metadata.json"
python3 "$LAB/snapshot_integrity.py" seal "$OUT"
chmod -R a-w "$OUT"
if [ "$STATE" = "activated" ]; then
  echo "$OUT" > "$RUNTIME/gapp_latest"
fi
echo "G_app $STATE snapshot recorded at $OUT"
