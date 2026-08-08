# Governance-Drift Laboratory

This directory contains the repeated Kubernetes/GitOps laboratory reported in
Sec. VII. It tests bounded realizability and detector behavior on one pinned
stack. It does not measure natural occurrence, field prevalence, production
reliability, or transfer across organizations.

## Executed stack

- Kind v0.32.0 with Kubernetes v1.36.1 on darwin/arm64
- Flux v2.9.4 components (source/kustomize controller v1.9.4)
- Kyverno v1.18.2
- local smart-HTTP Git remote and local OCI registry
- file-backed approved-state snapshot and environment inventory
- dependency-free evaluator at logical cadences of 0.5, 2, and 10 seconds

`bootstrap.sh` pins upstream Flux and Kyverno manifests by SHA-256 and preloads
the required arm64 images. `snapshot.sh` records the approved state.
`evaluator.py` reads Kubernetes, Git, Kyverno PolicyReports, approvals, pod
image IDs, and the environment inventory while restricting inputs by tier
T0n–T4.

## Positive scenarios

| ID | Injection | Expected class set | First-priority verdict | Deciding tier |
|---|---|---|---|---|
| S1 | Manual in-cluster change | configuration | configuration | T0n |
| S2 | Expired exception | authorization | authorization | T2 |
| S3 | Policy supersession | policy | policy | T1 |
| S4 | Artifact substitution and rollout | authorization | authorization | T3 |
| S5 | IAM expansion | environment | environment | T4 |
| S6 | Unapproved Git rollback and Flux convergence | intent, authorization | intent | T3 (T2 reports intent only) |
| S7 | Out-of-band load-balancer change | environment | environment | T4 |
| S8 | Approval subject mismatch | authorization | authorization | T3 |
| S9 | Approval-record deletion | evidence | evidence (`undecidable`) | T2 |
| S10 | Policy supersession plus expired exception | policy, authorization | authorization (policy may arrive later) | T2 |
| S11 | Artifact substitution plus environment change | authorization, environment | authorization | T4 |
| S12 | Rollback plus missing continuing-authorization status | evidence | evidence (`undecidable`) | T2 |

The seeded protocol runs every scenario 20 times in shuffled order. Three
logical evaluators observe the same injection; each begins at a deterministic
random phase in `[0, cadence)`. A detection below 0.5 seconds is therefore
valid: 0.5 seconds is the polling interval, not a resolution floor.

## Benign controls

Twenty 10.5-second windows are balanced round-robin across satisfied policy
revision, approved rollback, exception removal at expiry, approved artifact
retag, autoscaling outside the managed projection, and legitimate rollout
restart. Every window is observed at all three cadences.

The v1.5 secondary campaign runs S10--S12 five times each at the same three
cadences and continues after the first alert until the exact class set is
available. It then executes one 300-second window for each benign control.
The frozen extension contains 45/45 exact final vectors and 4,680 benign
polls with no substantive or epistemic alarm.

## Four clocks

Each positive observation records:

- `t_inject`: command initiation;
- `t_onset`: first operational inconsistency;
- `t_detect`: first tier-appropriate verdict;
- `t_evidence`: completion of the minimal JSON evidence bundle.

The derived measures are actuation (`onset - inject`), DDL (`detect - onset`),
end-to-end (`detect - inject`), and TTE (`evidence - onset`). For S2, onset is
exception expiry; for S4 it is successful substituted-artifact rollout; for
S6 it is successful Flux convergence to the unapproved revision. The legacy
one-pass `observations.*` files measured injection-to-verdict and are retained
for provenance, not relabeled as DDL.

## Reproduce

Prerequisites: Docker, Kind, `kubectl`, Git, Python 3.10+, `curl`, and enough
memory for the single-node cluster.

```bash
lab/bootstrap.sh
python3 lab/run_repeated_experiment.py
PYTHONPATH=lab python3 -m unittest -v lab/test_evaluator_contract.py
python3 scripts/analyze_lab_uncertainty.py
python3 scripts/analyze_lab_extension.py
python3 scripts/verify_lab_results.py
```

The repeated harness writes:

- `results/repeated_observations.csv` and `.json` — 540 cadence observations;
- `results/control_observations.csv` — all benign-control polls;
- `results/repeated_summary.json` — protocol, cadence, scenario, and control summaries;
- `results/table_repeated.tex`, `table_cadence.tex`, `table_controls.tex`, and
  `table_vector_metrics.tex`.
- `results_extension/` — compound-vector observations, six-window soak,
  validated JSON summary, and generated table.

To regenerate summaries and LaTeX tables from frozen raw data without a
cluster:

```bash
python3 lab/run_repeated_experiment.py --summarize-only
python3 scripts/verify_lab_results.py
```

The v1.5 evaluator scores full sets rather than membership of a priority
label. The frozen run produced 538/540 detections, 538/538 conditional exact
sets, global Hamming loss 0.000617, and zero substantive or epistemic control
alerts in 543 polls; priority output is retained only for operator routing. Both
misses were S1 at the 2- and 10-second cadences in one repetition: Flux
reconciled the transient mutation before those evaluators' first polls.
Authorization records now declare one-shot, continuing, or
temporary-exception semantics, and environment checks instantiate the
approved inventory as equality predicates over recorded fields only.
