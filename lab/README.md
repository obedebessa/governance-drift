# Governance-Drift Laboratory

This directory contains the repeated Kubernetes/GitOps laboratory reported in
Sec. VII. It tests bounded realizability and detector behavior on a primary
version-pinned stack and representative component paths on a second stack. It
does not measure natural occurrence, field prevalence, production reliability,
full-stack equivalence, or transfer across organizations.

## Primary executed stack

- Kind v0.32.0 with Kubernetes v1.36.1 on darwin/arm64
- Flux v2.9.4 components (source/kustomize controller v1.9.4)
- Kyverno v1.18.2
- local smart-HTTP Git remote and local OCI registry
- file-backed approved-state snapshot and environment inventory
- dependency-free evaluator at logical cadences of 0.5, 2, and 10 seconds

`bootstrap.sh` pins upstream Flux and Kyverno manifests by SHA-256 and preloads
the required arm64 images. The frozen primary campaign used its original
single-basis snapshot representation. The v1.6/B4 fixtures and current
`snapshot.sh` record approval and activation separately, link explicit
predecessors, and seal the complete snapshot in a tamper-evident hash chain;
`basis.py` verifies the chain and rejects ambiguous activated maxima. The
activation/supersession probes validate B4 semantics separately and are not
part of the frozen primary campaign.
`evaluator.py` reads Kubernetes, Git, Kyverno PolicyReports, approvals, pod
image IDs, and the environment inventory while restricting inputs by tier
T0n–T4.
`temporal_cut.py` is the executable production-adapter contract corresponding
to the manuscript's admissible-cut definition: it rejects missing or lagging
watermarks, stale or straddling intervals, ambiguous latest records, broken
subject linkage, and excessive cross-source spread.
Those tests validate the temporal contract independently; they do not turn the
primary evaluator's sequential reads into formal admissible cuts.

The frozen primary campaign records shared controller specifications by tag,
so controller-level digest pinning is not claimed for that run. The current
bootstrap resolves images through `image-lock.json`; the exact version and
image references used by the frozen campaign remain in its raw metadata.

## Bounded Argo CD + Gatekeeper replication

The reportable cross-stack campaign ran on a separate `govdrift-cross` Kind
cluster with Kubernetes v1.36.1, Argo CD v3.4.2, and Gatekeeper v3.22.2.
Tagged upstream manifests are retained and SHA-256 verified. Five repetitions
each of S1, S3, and S4 produced 15/15 exact expected singleton class sets over
the declared evaluated components. S1 uses Argo CD's native desired/live
status. S3 uses a fresh Gatekeeper dry-run background audit whose controlled
Rego message emits the evaluated Deployment UID; the adapter withholds a polar
result unless that engine-emitted UID matches the live UID. This subject join
covers one controlled Deployment lifetime and does not establish identity
continuity across resource deletion and recreation. S4 reuses the shared T3
digest adapter and is not independent authorization validation. Intent and
environment were not evaluated.

| Slice | Exact | Median first-honest DDL | Median first substantive | Median ESC |
|---|---:|---:|---:|---:|
| S1 configuration / Argo CD native | 5/5 | 0.207 s | 0.646 s | 1.677 s |
| S3 policy / Gatekeeper native | 5/5 | 0.157 s | 1.659 s | 1.659 s |
| S4 authorization / shared adapter | 5/5 | 0.215 s | 0.215 s | 0.650 s |

First-honest DDL stops at the first completed non-consistent or undecidable
evaluation, so it may be epistemic rather than substantive. First epistemic
alert and first substantive alert are retained separately; ESC stops only when
all declared evaluated components are decidable and the projected class set is
exact. All five S1 and all five S3 observations emitted an epistemic alert
(median 0.207 and 0.157 seconds); three of five S4 observations did so (median
0.216 seconds among reached observations). The analyzer reconstructs all four
timings from explicit onset markers and per-poll evaluation completions in the
raw NDJSON and requires exact agreement with the observations and summary.

All 15 baseline restorations succeeded, with zero leaf-level desired/live
residuals, zero instrumented API-read errors, and zero undecidable evaluated
components in the final observations. The sustained resource stop rule did not
trigger, and the captured pre/delete/post proof verifies cleanup of only
`govdrift-cross`. The comparison remains descriptive: S1 and S3 are native
component-path replications, S4 is shared, and the campaign is not an
equivalence, non-inferiority, prevalence, reliability, or
production-performance study.

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
| S9 | Required live-status loss for continuing authority (proof retained) | evidence | evidence (`undecidable`) | T2 |
| S10 | Policy supersession plus expired exception | policy, authorization | authorization (policy may arrive later) | T2 |
| S11 | Artifact substitution plus environment change | authorization, environment | authorization | T4 |
| S12 | Rollback plus missing continuing-authorization status | intent, evidence | intent + evidence (`authorization` undecidable) | T2 |

Intent denotes historically authorized lineage. Accordingly, ordinary expiry
or prospective revocation changes current authorization without rewriting
intent; only an explicitly retroactive invalidation can invalidate historical
intent. In S9, loss of required live status leaves authorization undecidable,
so the observable class is evidence while intent remains decidable and
consistent. In S12, the rollback breaks historical intent lineage and the
missing live status makes authorization undecidable, yielding intent plus
evidence.

The seeded primary protocol runs each S1--S9 scenario 20 times in shuffled
order; the compound S10--S12 extension runs each scenario five times. Three
correlated logical observers share one serial event loop; each begins at a deterministic
random phase in `[0, cadence)`. A detection below 0.5 seconds is therefore
valid: 0.5 seconds is the polling interval, not a resolution floor. They are
not independent processes; the transition-inclusive campaign below uses
three isolated processes and records scheduler lag explicitly.

## Process-isolated positive trace replication

The reportable positive trace campaign is named by
`results_trace/FINAL_CAMPAIGN`. It replays S1--S9 once in the exclusive
`govdrift-trace` namespace, with three separate OS observer processes at 1,
5, and 10 seconds. The nine injected episodes are the experimental units;
the 27 scenario-by-cadence trajectories are correlated observational units,
not 27 independent replicates. The frozen final run contains 261 fsync'd,
hash-chained NDJSON polls, 27/27 exact trajectories, 27/27 trajectories with
two-poll exact persistence,
and zero adapter-error polls. Here the second count means only two-poll exact
persistence: the second consecutive exact poll. It is not a
watermark-qualified persistence claim.

Cause-start-to-first-exact completion was descriptively 4.465 seconds at the
median and 9.764 seconds at nearest-rank P95. Cause-start-to-two-poll exact
persistence was
9.522 and 19.811 seconds, respectively. These are pooled descriptions of 27
correlated trajectories, not DDL, production latency, or independent tail
estimates. The workload and ephemeral Git-helper references are digest locked.
S4 uses a namespaced Kyverno admission mutation; S5/S7 use the file-backed
inventory; and S6 explicitly requests Flux reconciliation. Exact limitations
and the non-reportable pilot/aborted directories are recorded in
`results_trace/README.md` and the frozen campaign README.

The trace verifier recomputes every poll hash chain and input fingerprint,
links injection hashes and command records, reconstructs causal event markers,
requires three distinct PIDs/UUIDs per scenario, checks digest references and
cleanup, and requires exact equality from raw logs through scenario summaries,
trajectory JSON/CSV, latency aggregates, and the 133-file manifest:

```bash
python3 lab/test_trace_harness.py -v
python3 scripts/verify_trace_results.py \
  "lab/results_trace/$(cat lab/results_trace/FINAL_CAMPAIGN)"
```

The absolute invocation path retained inside the frozen campaign README is
provenance from the capture host; the command above is the portable form.

## Benign controls

The frozen v1.5 data contain 20 post-stabilization 10.5-second windows in a
near-balanced round-robin allocation (4,4,3,3,3,3) across satisfied policy
revision, approved rollback, exception removal at expiry, approved artifact
retag, autoscaling outside the managed projection, and legitimate rollout
restart. Every window is observed at all three cadences. Because observation
begins after consistency, these are steady-state controls.

The v1.6 transition-inclusive campaign starts all three observer processes
before each mutation and retains append-only NDJSON for every poll. Across 20
windows and 777 polls it emitted no policy, authorization, intent, or
environment drift; it retained
seven expected rollback-convergence configuration signals, and returned 36
fail-safe epistemic warnings while rollout lineage was incomplete. Every one
of 60 observer trajectories ended with a provisionally consistent
classification. Run it with
`python3 lab/run_transition_controls.py` and verify it with
`python3 scripts/verify_transition_controls.py`.

The secondary campaign runs S10--S12 five times each at the same three
cadences and continues after the first alert until the exact class set is
available. It then executes one 300-second window for each benign control.
The frozen extension contains 45/45 exact provisional class sets under the
same sequential-snapshot assumption and 4,680 benign
polls with no substantive or epistemic alarm. These are six separate
five-minute windows, not one continuous 30-minute soak.

## Five timestamps

Each positive observation records:

- `t_inject`: command initiation;
- `t_onset`: first operational inconsistency;
- `t_first`: first honest non-consistent verdict;
- `t_complete`: first exact provisional class set;
- `t_evidence`: completion of the minimal JSON evidence bundle.

The derived measures are actuation (`onset - inject`), DDL (`first - onset`),
ESC (`complete - onset`), diagnostic gap (`complete - first`), end-to-end
(`first - inject`), and TTE (`evidence - onset`). Unreached DDL/ESC values are
right-censored, not stored as observed timeout latencies. For S2, onset is
exception expiry; for S4 it is successful substituted-artifact rollout; for
S6 it is successful Flux convergence to the unapproved revision. The legacy
one-pass `observations.*` files measured injection-to-verdict and are retained
for provenance, not relabeled as DDL.

For the cross-stack campaign, `t_first` is explicitly the first honest verdict:
the earlier available completion among the first epistemic (`undecidable`) and
first substantive (`inconsistent`) events. Those two event times are also stored
separately. ESC is later when unresolved evaluated components remain, even if
the expected substantive singleton has already appeared.

## Reproduce

Prerequisites: Docker, Kind, `kubectl`, Git, Python 3.10+, `curl`, and enough
memory for the single-node cluster.

```bash
lab/bootstrap.sh
python3 lab/run_repeated_experiment.py
PYTHONPATH=lab python3 -m unittest -v lab/test_evaluator_contract.py
PYTHONPATH=lab:code python3 -m unittest -v lab/test_basis_selection.py \
  lab/test_snapshot_integrity.py lab/test_ablation_study.py \
  lab/test_batch_evaluator.py
python3 scripts/analyze_lab_uncertainty.py
python3 scripts/analyze_lab_extension.py
python3 scripts/verify_lab_results.py
python3 scripts/verify_transition_controls.py
PYTHONPATH=lab python3 -m unittest -v lab/test_evidence_gateway.py
PYTHONPATH=lab python3 -m unittest -v lab/test_temporal_cut.py
python3 scripts/analyze_faults.py
python3 scripts/analyze_scaling.py
PYTHONPATH=lab python3 -m unittest -v lab/test_live_fleet_adapter.py
python3 scripts/analyze_live_fleet.py
PYTHONPATH=lab python3 -m unittest -v lab/test_cross_stack_adapter.py
python3 scripts/analyze_cross_stack.py
python3 scripts/run_scoping_review.py
python3 lab/test_trace_harness.py -v
python3 scripts/verify_trace_results.py \
  "lab/results_trace/$(cat lab/results_trace/FINAL_CAMPAIGN)"
```

The repeated harness writes:

- `results/repeated_observations.csv` and `.json` — 540 cadence observations;
- `results/control_observations.csv` — all benign-control polls;
- `results/repeated_summary.json` — protocol, cadence, scenario, and control summaries;
- `results/table_repeated.tex`, `table_cadence.tex`, `table_controls.tex`, and
  `table_class_set_metrics.tex`.
- `results_extension/` — compound-vector observations, six-window benign-control campaign,
  validated JSON summary, and generated table.
- `results_transition/` — 60 append-only observer logs, 777 per-poll rows,
  action metadata, hashes, summary, and generated transition table.
- `results_scaling/` — synthetic in-memory batch microbenchmark raw samples,
  summaries, and tables; it explicitly excludes Kubernetes/API/network costs.
- `results_faults/` — 636-event controlled localhost TCP trace, nine fault
  profiles, 25 vector evaluations, summaries, and safety/recovery tables.
- `results_live_fleet/` — 40 live object-path sweeps at 10 and 50
  Deployments, raw API/fetch/parse/core timings, phase-separated scale/restart
  churn, the admitted readiness stop at 100 Deployments, and verified cleanup.
- `results_trace/` — final process-isolated S1--S9 campaign with 27 observer
  NDJSON chains, injection/source hashes, causal event markers, platform and
  cleanup capture, Git bundle, cross-layer summaries, and a complete SHA-256
  manifest; `FINAL_CAMPAIGN` identifies the sole reportable run.
- `results_cross_stack/` — 15/15 exact projected Argo CD/Gatekeeper
  observations, explicit onset and evaluation-completion timing, complete
  per-poll trace, upstream and local manifest hashes, resource samples,
  generated table, and verified cluster-scoped cleanup; ignored
  `_superseded_*` runs are excluded from the reportable artifact.

To regenerate summaries and LaTeX tables from frozen raw data without a
cluster:

```bash
python3 lab/run_repeated_experiment.py --summarize-only
python3 scripts/verify_lab_results.py
python3 scripts/analyze_cross_stack.py
```

The evaluator scores complete provisional class sets rather than membership of
a priority label. The frozen run produced 538/540 detections, 538/538
conditional exact sets, global Hamming loss 0.000617, and zero substantive or epistemic control
alerts in 543 polls; priority output is retained only for operator routing. Both
misses were S1 at the 2- and 10-second cadences in one repetition: Flux
reconciled the transient mutation before those evaluators' first polls.
Authorization records now declare one-shot, continuing, or
temporary-exception semantics, and environment checks instantiate the
approved inventory as equality predicates over recorded fields only.
These live classifications assume a laboratory sequential snapshot. The
separate `temporal_cut.py` suite tests the formal cut contract; it does not make
the frozen live observations complete formal-vector or `Cons` evaluations.
