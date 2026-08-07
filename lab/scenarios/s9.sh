#!/usr/bin/env bash
# S9: approval-record deletion (evidence drift -> undecidable verdict).
LAB="$(cd "$(dirname "$0")/.." && pwd)"
mkdir -p "$LAB/_deleted" && mv "$LAB"/approvals/APR-*.json "$LAB/_deleted/"
