#!/usr/bin/env bash
# Scenario S2: expired emergency authorization.
# Grants an exception with a 4-hour window, applies the emergency change it
# covers, and then deliberately does nothing at expiry: the exception's
# effect silently outlives its authorization. Governance drift onset is the
# expiry instant; nothing on the configuration plane changes at onset.
set -euo pipefail
LAB="$(cd "$(dirname "$0")/.." && pwd)"
NOW=$(date -u +%s)
EXPIRY=$((NOW + 4*3600))

cat > "$LAB/approvals/EXC-1.json" <<EOF
{
  "id": "EXC-1",
  "kind": "emergency-exception",
  "subject": "deployment/payments",
  "basis": "incident-4711",
  "granted_utc": $NOW,
  "expires_utc": $EXPIRY,
  "revoked": false
}
EOF
echo "$(date -u +%FT%TZ) INJECT s2 grant EXC-1 expires=$EXPIRY" >> "$LAB/log/injections.log"

# the emergency change the exception covers (privileged debug sidecar):
kubectl -n payments patch deployment payments --type=json -p '[
  {"op":"add","path":"/spec/template/metadata/annotations/emergency-debug",
   "value":"EXC-1"}]'

echo "NOTE: drift onset is at expiry ($(date -u -d @$EXPIRY +%FT%TZ))."
echo "measure.sh records first alarm from each tier evaluator after onset."
