#!/usr/bin/env bash
# S9: required live-status loss; immutable approval proof remains in G_app.
LAB="$(cd "$(dirname "$0")/.." && pwd)"; RUNTIME="$LAB/runtime"
mkdir -p "$RUNTIME/_deleted" && mv "$RUNTIME"/approvals/APR-*.json "$RUNTIME/_deleted/"
