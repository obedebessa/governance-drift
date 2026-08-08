#!/usr/bin/env bash
# Build a pinned, local Kind + Flux + Kyverno governance-drift laboratory.
set -euo pipefail

LAB="$(cd "$(dirname "$0")" && pwd)"
RUNTIME="$LAB/runtime"
FLUX_VERSION="2.9.4"
KYVERNO_VERSION="1.18.2"
FLUX_SHA256="9eb86c5f9d606b2ac2cfe71223ab2f23faa2d59ccb21df4e08e5610e54d535f8"
KYVERNO_SHA256="3dcd43eaf11f0719084217148cd0c82a8fa49faa9b1a783ea5bea2cf84041bda"
IMAGE_LOCK="$LAB/image-lock.json"

for tool in docker kind kubectl git curl jq python3; do
  command -v "$tool" >/dev/null || { echo "missing required tool: $tool" >&2; exit 1; }
done

kind delete cluster --name govdrift-lab >/dev/null 2>&1 || true
docker rm -f kind-registry govdrift-git >/dev/null 2>&1 || true
rm -rf "$RUNTIME"
mkdir -p "$RUNTIME/cache" "$RUNTIME/git" "$RUNTIME/log" "$RUNTIME/approvals" "$RUNTIME/proofs"

curl -fsSL "https://github.com/fluxcd/flux2/releases/download/v${FLUX_VERSION}/install.yaml" \
  -o "$RUNTIME/cache/flux-install.yaml"
curl -fsSL "https://github.com/kyverno/kyverno/releases/download/v${KYVERNO_VERSION}/install.yaml" \
  -o "$RUNTIME/cache/kyverno-install.yaml"
printf '%s  %s\n' "$FLUX_SHA256" "$RUNTIME/cache/flux-install.yaml" \
  | shasum -a 256 -c -
printf '%s  %s\n' "$KYVERNO_SHA256" "$RUNTIME/cache/kyverno-install.yaml" \
  | shasum -a 256 -c -
python3 "$LAB/pin_manifests.py" --lock "$IMAGE_LOCK" \
  "$RUNTIME/cache/flux-install.yaml" "$RUNTIME/cache/kyverno-install.yaml"

locked_image() {
  python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["images"][sys.argv[2]])' \
    "$IMAGE_LOCK" "$1"
}

docker run -d --restart=always -p 127.0.0.1:5001:5000 \
  --name kind-registry "$(locked_image registry:2)" >/dev/null
kind create cluster --config "$LAB/kind-cluster.yaml"
docker network connect kind kind-registry >/dev/null 2>&1 || true

# Preload pinned controller images through the host cache. This avoids
# registry latency inside the single Kind node dominating bootstrap time.
CONTROLLER_IMAGES=$(awk '$1 == "image:" && NF >= 2 {gsub(/"/, "", $2); print $2}' \
  "$RUNTIME/cache/flux-install.yaml" "$RUNTIME/cache/kyverno-install.yaml" \
  | grep -E 'fluxcd/(source-controller|kustomize-controller|notification-controller)|kyverno/(kyvernopre|kyverno@|background-controller|reports-controller)' \
  | sort -u)
for image in $CONTROLLER_IMAGES; do
  docker pull "$image" >/dev/null
  docker save "$image" | docker exec -i govdrift-lab-control-plane \
    ctr --namespace=k8s.io images import --platform linux/arm64 - >/dev/null
done

git init --bare "$RUNTIME/git/remote.git"
cp "$LAB/git-hooks/post-update" "$RUNTIME/git/remote.git/hooks/post-update"
chmod +x "$RUNTIME/git/remote.git/hooks/post-update"
git clone "$RUNTIME/git/remote.git" "$RUNTIME/work"
git -C "$RUNTIME/work" config user.name "Governance Drift Lab"
git -C "$RUNTIME/work" config user.email "lab@example.invalid"
mkdir -p "$RUNTIME/work/payments"
cp "$LAB/manifests/namespace.yaml" "$RUNTIME/work/payments/namespace.yaml"
cp "$LAB/manifests/deployment-predecessor.yaml" "$RUNTIME/work/payments/deployment.yaml"
cp "$LAB/manifests/kustomization.yaml" "$RUNTIME/work/payments/kustomization.yaml"
git -C "$RUNTIME/work" add -A
git -C "$RUNTIME/work" commit -m "rev-41: predecessor"
git -C "$RUNTIME/work" push origin HEAD:master
cp "$LAB/manifests/deployment.yaml" "$RUNTIME/work/payments/deployment.yaml"
git -C "$RUNTIME/work" add payments/deployment.yaml
git -C "$RUNTIME/work" commit -m "rev-42: approved baseline"
git -C "$RUNTIME/work" push origin HEAD:master
git -C "$RUNTIME/work" rev-parse HEAD > "$RUNTIME/baseline_revision"

docker run -d --restart=always --network kind --name govdrift-git \
  -v "$RUNTIME/git:/git:ro" -v "$LAB/git-http:/srv/cgi-bin:ro" \
  "$(locked_image python:3.12-slim)" sh -c \
  'apt-get update -qq && apt-get install -y -qq git >/dev/null && cd /srv && exec python3 -m http.server --cgi 8000' \
  >/dev/null

BASE_IMAGE="$(locked_image nginx:1.27-alpine)"
ALT_IMAGE="$(locked_image nginx:1.26-alpine)"
docker pull "$BASE_IMAGE" >/dev/null
docker tag "$BASE_IMAGE" localhost:5001/governance-demo:1.0
docker push localhost:5001/governance-demo:1.0 >/dev/null
docker pull "$ALT_IMAGE" >/dev/null
docker tag "$ALT_IMAGE" localhost:5001/governance-demo:alternate
docker push localhost:5001/governance-demo:alternate >/dev/null

kubectl apply -f "$RUNTIME/cache/flux-install.yaml" >/dev/null
kubectl -n flux-system scale deployment helm-controller image-automation-controller \
  image-reflector-controller source-watcher --replicas=0 >/dev/null
kubectl -n flux-system rollout status deployment/source-controller --timeout=180s
kubectl -n flux-system rollout status deployment/kustomize-controller --timeout=180s
kubectl -n flux-system rollout status deployment/notification-controller --timeout=180s
kubectl apply -f "$LAB/flux-source.yaml" >/dev/null

kubectl apply --server-side -f "$RUNTIME/cache/kyverno-install.yaml" >/dev/null
kubectl -n kyverno scale deployment/kyverno-cleanup-controller --replicas=0 >/dev/null
kubectl -n kyverno rollout status deployment/kyverno-admission-controller --timeout=240s
kubectl -n kyverno rollout status deployment/kyverno-background-controller --timeout=240s
kubectl -n kyverno rollout status deployment/kyverno-reports-controller --timeout=240s
kubectl apply -f "$LAB/policies/kyverno-policy-v7.yaml" >/dev/null

kubectl -n flux-system annotate gitrepository lab \
  reconcile.fluxcd.io/requestedAt="$(date -u +%s)" --overwrite >/dev/null
kubectl -n flux-system wait gitrepository/lab --for=condition=Ready --timeout=180s
kubectl -n flux-system wait kustomization/lab --for=condition=Ready --timeout=180s
kubectl -n payments rollout status deployment/payments --timeout=180s

BASE_DIGEST=$(kubectl -n payments get pod -l app=payments \
  -o jsonpath='{.items[0].status.containerStatuses[0].imageID}' | sed 's/.*@//')
LOCKED_BASE_DIGEST="${BASE_IMAGE##*@}"
BASE_REVISION=$(cat "$RUNTIME/baseline_revision")
jq --arg d "$BASE_DIGEST" --arg ld "$LOCKED_BASE_DIGEST" --arg r "$BASE_REVISION" \
  '.subjects=([$d,$ld] | unique) | .revisions=[$r]' "$LAB/approvals/APR-1.json" \
  > "$RUNTIME/approvals/APR-1.json"
cp "$RUNTIME/approvals/APR-1.json" "$RUNTIME/APR-1.baseline.json"
cp "$LAB/cloud-inventory.json" "$RUNTIME/cloud-inventory.json"
cp "$LAB/cloud-inventory.json" "$RUNTIME/cloud-inventory.baseline.json"
"$LAB/snapshot.sh"

kubectl version -o json > "$RUNTIME/platform-kubernetes.json"
kubectl -n flux-system get deployments -o json > "$RUNTIME/platform-flux.json"
kubectl -n kyverno get deployments -o json > "$RUNTIME/platform-kyverno.json"
echo "laboratory ready"
