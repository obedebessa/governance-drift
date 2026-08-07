#!/usr/bin/env bash
# Build the laboratory: kind cluster + local registry + Flux v2 against a
# local git remote + Kyverno (audit mode) + baseline workload + approved-
# state snapshot. All versions pinned; see README for the protocol.
set -euo pipefail
LAB="$(cd "$(dirname "$0")" && pwd)"
docker run -d --restart=always -p 127.0.0.1:5001:5000 --name kind-registry registry:2 || true
kind create cluster --config "$LAB/kind-cluster.yaml"
docker network connect kind kind-registry || true
git init --bare "$LAB/remote.git" 2>/dev/null || true
git clone "$LAB/remote.git" "$LAB/work" 2>/dev/null || true
cp -r "$LAB/manifests/." "$LAB/work/" && (cd "$LAB/work" && git add -A && git commit -m "rev-42: approved baseline" && git push origin HEAD)
flux install
flux create source git lab --url="file://$LAB/remote.git" --branch=master
flux create kustomization lab --source=GitRepository/lab --path=. --prune=true --interval=1m
kubectl apply -f "$LAB/policies/kyverno-install.yaml"
kubectl apply -f "$LAB/policies/kyverno-policy-v7.yaml"
"$LAB/snapshot.sh"   # record G_app at admission
