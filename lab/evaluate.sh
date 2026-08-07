#!/usr/bin/env bash
# Reference evaluator: recomputes the six component distances each cycle.
# Usage: evaluate.sh <tier: T0n|T1|T2|T3|T4> ; exit code 0 = consistent,
# 2 = drift (class on stdout), 3 = undecidable/evidence.
set -euo pipefail
LAB="$(cd "$(dirname "$0")" && pwd)"
TIER="${1:-T4}"; GAPP=$(ls -d "$LAB"/gapp/* | tail -1)
now=$(date -u +%s)
# d_conf (normalized: spec-managed fields only)
desired=$(cd "$LAB/work" && git show HEAD:payments/deployment.yaml | sha256sum)
observed=$(kubectl -n payments get deploy payments -o yaml \
  | yq 'del(.status, .metadata.resourceVersion, .metadata.generation,
        .metadata.annotations["deployment.kubernetes.io/revision"],
        .spec.replicas)' | sha256sum)
[ "$desired" != "$observed" ] && { echo configuration; exit 2; }
[ "$TIER" = "T0n" ] && exit 0
# d_pol: re-evaluate via kyverno policy reports
if kubectl get polr -n payments -o json | jq -e \
   '.items[].results[] | select(.result=="fail")' >/dev/null 2>&1; then
  echo policy; exit 2; fi
[ "$TIER" = "T1" ] && exit 0
# d_auth: expired-but-present exceptions; evidence decidability; d_int
for f in "$LAB"/approvals/EXC-*.json; do [ -e "$f" ] || continue
  exp=$(jq -r .expires_utc "$f"); rem=$(jq -r '.removed // empty' "$f")
  if [ "$now" -ge "$exp" ] && [ -z "$rem" ]; then echo authorization; exit 2; fi
done
ls "$LAB"/approvals/APR-*.json >/dev/null 2>&1 || { echo evidence; exit 3; }
cur_rev=$(cd "$LAB/work" && git rev-parse HEAD)
jq -e --arg r "$cur_rev" 'select(.revisions[]? == $r)' \
  "$LAB"/approvals/APR-*.json >/dev/null || { echo intent; exit 2; }
[ "$TIER" = "T2" ] && exit 0
# d_auth at digest granularity (lineage)
run_dig=$(kubectl -n payments get pod -l app=payments \
  -o jsonpath='{.items[0].status.containerStatuses[0].imageID}' | sed 's/.*@//')
jq -e --arg d "$run_dig" 'select(.subjects[]? == $d)' \
  "$LAB"/approvals/APR-*.json >/dev/null || { echo authorization; exit 2; }
[ "$TIER" = "T3" ] && exit 0
# d_env vs sigma0
diff <(jq -S . "$LAB/cloud-inventory.json") \
     <(jq -S . "$GAPP/sigma0.json") >/dev/null || { echo environment; exit 2; }
exit 0
