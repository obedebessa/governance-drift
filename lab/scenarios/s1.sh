#!/usr/bin/env bash
# S1: manual in-cluster change (configuration drift).
kubectl -n payments patch deploy payments --type=json -p \
 '[{"op":"replace","path":"/spec/template/spec/containers/0/resources/limits/cpu","value":"999m"}]'
