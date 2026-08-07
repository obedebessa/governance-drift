#!/usr/bin/env bash
# S4: artifact substitution -- retag the approved reference, then roll pods.
docker tag localhost:5001/governance-demo:alternate \
  localhost:5001/governance-demo:1.0
docker push localhost:5001/governance-demo:1.0 >/dev/null
kubectl -n payments rollout restart deploy payments
kubectl -n payments rollout status deploy/payments --timeout=180s
