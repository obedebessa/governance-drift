# Release notes

## v1.2.0 — 2026-08-07

- Executes 20 repetitions of S1–S9 at 0.5-, 2-, and 10-second evaluator cadences: 538/540 detections and 538/538 correct classifications conditional on detection.
- Separates injection, drift onset, detection, and evidence-completion timestamps; reports actuation, DDL, end-to-end latency, and TTE without relabeling the legacy one-pass values.
- Executes 20 benign live-stack windows across six change families, producing zero alarms in 543 evaluator polls.
- Represents S6 ground truth explicitly as `{intent, authorization}` and reports the evaluator's first-priority verdict separately.
- Reframes live evidence as bounded realizability and within-laboratory behavior, not evidence of natural occurrence or production performance.
- Adds formal Data and Code Availability, exact reproduction commands, release DOI, source snapshot, and checksums.

## v1.1.0 — 2026-08-07

- Executes the bounded Kind/Flux/Kyverno laboratory over S1–S9.
- Reports 9/9 consistent baselines and expected class-resolved outcomes, with observed injection-to-verdict latencies of 0.137–8.571 seconds.
- Preserves the evidence boundary: one execution per scenario demonstrates feasibility and occurrence, not prevalence, reliability, or latency distributions.
- Adds a class-resolved risk-channel and severity taxonomy.
- Adds a comparative matrix showing which adjacent lines of work natively check configuration, policy, authorization, intent, evidence, environment, and the admitted-basis join.
- Adds executable manifests, pinned bootstrap assets, live evaluator, scenario harness, frozen observations, and integrity verification.

## v1.0.0 — 2026-08-07

- Incorporates the complete simulated Reviewer #2 revision.
- Repositions governance drift as a history-anchored relation rather than a denial of comparison mechanics.
- Adds benign governance churn and churn-rate sensitivity.
- Reimplements policy, approval, lineage, and environment checks against the paper's definitions.
- Exercises all six taxonomy classes across nine scenarios with class-set scoring.
- Completes the specified Kubernetes/GitOps laboratory artifact while clearly marking it unexecuted.
- Generates the full results table programmatically.
- Adds release metadata, integrity checks, and a verified compiled manuscript.
