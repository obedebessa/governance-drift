#!/usr/bin/env bash
# S9: approval-record deletion (evidence drift -> undecidable verdict).
LAB="$(cd "$(dirname "$0")/.." && pwd)"; RUNTIME="$LAB/runtime"
mkdir -p "$RUNTIME/_deleted" && mv "$RUNTIME"/approvals/APR-*.json "$RUNTIME/_deleted/"
