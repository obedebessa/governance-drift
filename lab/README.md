# Governance-Drift Laboratory

This directory contains the executed Kubernetes/GitOps laboratory reported in
Sec. VII. It is a bounded feasibility and occurrence demonstration: one
controlled execution per scenario, not a prevalence, reliability, or
production-performance study.

## Executed stack

- Kind v0.32.0 with Kubernetes v1.36.1 on darwin/arm64
- Flux v2.9.4 components (source/kustomize controller v1.9.4)
- Kyverno v1.18.2
- local smart-HTTP Git remote
- local OCI registry for tag/digest scenarios
- approved-state snapshot and mock environment inventory
- dependency-free tier evaluator polling every 0.5 seconds

`bootstrap.sh` pins the upstream Flux and Kyverno manifests by SHA-256 and
preloads the required arm64 images. `snapshot.sh` records the approved state.
`evaluator.py` reads Kubernetes, Git, Kyverno PolicyReports, approvals, pod
image IDs, and the environment inventory, restricting inputs by tier T0n–T4.

## Scenarios

| ID | Injection | Expected class | Deciding tier |
|---|---|---|---|
| S1 | Manual in-cluster change | configuration | T0n |
| S2 | Expired exception | authorization | T2 |
| S3 | Policy supersession | policy | T1 |
| S4 | Artifact substitution and rollout | authorization | T3 |
| S5 | IAM expansion | environment | T4 |
| S6 | Unapproved Git rollback and Flux convergence | intent | T2 |
| S7 | Out-of-band load-balancer change | environment | T4 |
| S8 | Approval subject mismatch | authorization | T3 |
| S9 | Approval-record deletion | evidence (`undecidable`) | T2 |

## Reproduce

Prerequisites: Docker, Kind, `kubectl`, Git, Python 3.10+, `curl`, and a host
with enough memory for the single-node cluster. Then run:

```bash
lab/bootstrap.sh
python3 lab/run_experiment.py
```

The harness restores and verifies a governance-consistent baseline before
every injection, executes S1–S9, polls the minimum deciding tier, writes the
observations, and restores the baseline at the end. Generated outputs are:

- `results/observations.csv`
- `results/observations.json`
- `results/platforms.json`
- `results/table_lab.tex`

Verify the frozen outputs without a live cluster:

```bash
python3 scripts/verify_lab_results.py
```

## Measurement semantics

Latency is wall-clock time from injection initiation to the first
tier-appropriate verdict. S2 starts at exception expiry. S4 includes registry
mutation and rollout; S6 includes Git commit and Flux convergence; S3 includes
Kyverno background evaluation. Each scenario was executed once (`n=1`), so
the reported values are observations rather than a latency distribution.

The stronger follow-on protocol is to repeat every scenario at least twenty
times with randomized injection phase, benign runtime and governance churn,
drift-free controls, multiple evaluator cadences, and separate
time-to-detection/time-to-evidence measures. That repeated design has not yet
been executed.
