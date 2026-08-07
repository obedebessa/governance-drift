#!/usr/bin/env bash
# S3: policy supersession pi-7 -> pi-8 (policy drift); no redeploy.
LAB="$(cd "$(dirname "$0")/.." && pwd)"
kubectl apply -f "$LAB/policies/kyverno-policy-v8.yaml"
