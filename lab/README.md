# Governance-Drift Laboratory (designed; not executed in the paper)

A reproducible Kubernetes/GitOps laboratory for measuring governance drift
with real components and wall-clock latencies. The paper (Sec. VII) specifies
the protocol; this directory contains the build and scenario scaffolding.

## Stack (all pinned in the scripts)
- kind (Kubernetes-in-Docker) single-node cluster  [kind-cluster.yaml]
- Flux v2 reconciling a local Git remote           [bootstrap.sh]
- Kyverno as policy engine; two policy versions    [policies/]
- Local OCI registry (registry:2) for tag/digest scenarios
- Approved-state recorder: snapshot.sh writes G_app (manifest digest,
  resolved image digest, policy version, approval JSON with window,
  git revision, environment assumptions) to an append-only directory
- Evaluator: evaluate.sh recomputes the six component distances each cycle
  from: kubectl (observed), git (desired+revision), policies/ (P(t)),
  approvals/ (A(t) with expiry), registry API (lineage), and a mock cloud
  inventory file (environment)

## Scenarios (scenarios/s1.sh ... s9.sh)
s1 kubectl patch deployment (manual change)
s2 grant exception with expiry=+4h; do nothing at expiry
s3 apply policy v8 (supersedes v7) without redeploying
s4 re-tag image in registry to a new digest; trigger rollout
s5 broaden a mock IAM binding in cloud-inventory.json
s6 git revert to previous revision; let Flux converge
s7 modify mock cloud LB config out of band
s8 replace approval subject digest in approvals/ with a mismatched digest
s9 delete approval records (evidence drift; evaluator returns undecidable)

## Measurement (measure.sh)
For each scenario: record injection wall-clock time; poll the evaluator and
each tier-restricted evaluator (T0n..T4) at its natural cadence; record
first-alarm times and classes; compute time-to-detection per tier and
time-to-evidence (time to assemble the supporting records for the verdict).
Repeat N times; report distributions. Controls: churn generator
(HPA min/max flapping + rollout restarts) runs throughout; false-alarm rates
are measured on drift-free windows.

## Refutation conditions (what would falsify the paper's claims)
- Any scenario detected reliably by T0n that the matrix assigns to a higher
  tier (would refute the tier-dependency analysis, Prop. 2).
- Tier-appropriate detectors failing to detect their scenarios for reasons
  other than stream fidelity (would refute tier sufficiency).
- Wall-clock TTD dominated by factors other than evaluation cadence for
  event-carried classes (would refute Finding F3's transfer).
