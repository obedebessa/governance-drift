#!/usr/bin/env bash
# S4: artifact substitution -- retag svc:1.4.2 to a new digest, roll pods.
docker build -t localhost:5001/svc:1.4.2 --no-cache "$LAB_ALT_CONTEXT" \
  && docker push localhost:5001/svc:1.4.2
kubectl -n payments rollout restart deploy payments
