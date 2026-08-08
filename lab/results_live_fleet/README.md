# Live Kind fleet adapter experiment

This directory contains an adapter-and-Kubernetes-object-path experiment, not
a Flux/Kyverno full-stack or production-capacity benchmark.

The requested targets were 10, 50, and 100 Deployments, with 20 timed sweeps
per target. Sizes 10 and 50 completed. The 100-Deployment fleet did not settle
within the frozen protocol's declared 240-second readiness rule, so the
experiment stopped and deleted only the exclusive `govdrift-fleet` namespace.
The cleanup is recorded in `cleanup.json` and embedded in
`live_fleet_raw.json`; no timing sample is reported for the incomplete size.

At the largest completed size, the deterministic benign churn cohort increased
the fleet from 50 to 60 active Pods and from 100 to 120 active containers. All
1,200 baseline unit verdicts across the 40 completed sweeps were exact and
decidable, all 240 seeded policy-fan-out verdicts were exact, and no measured
API command failed. For 50 Deployments, total sweep time was 108.61 ms at P50
and 187.75 ms at P95; evaluator-core time was 0.332 ms at P50 and 0.415 ms at
P95. These numbers describe this bounded Kind campaign only.

The Kind node advertises a capacity of 110 Pods, so capacity is a plausible
explanation for the $n=100$ readiness timeout. The campaign did not retain a
scheduler diagnosis from the timeout instant and therefore assigns no cause.

Each timed sweep executes one `kubectl get deployments,pods` command. That
command performs one Kubernetes LIST for Deployments and one for Pods. Fetch
time includes `kubectl` startup, API access, and response transfer; parse time
includes JSON decoding and adapter normalization; core time includes in-memory
indexing and `BatchEvaluator` decisions. Approval, policy, and environment
records are locally synthesized and UID-scoped after the live baseline capture.

Reproduce cautiously on the configured Kind cluster:

```bash
PYTHONHASHSEED=0 python3 lab/run_live_fleet_experiment.py
python3 scripts/analyze_live_fleet.py
PYTHONHASHSEED=0 PYTHONPATH=lab python3 lab/test_live_fleet_adapter.py -v
```

The runner refuses to reuse a pre-existing `govdrift-fleet` namespace, applies
CPU, memory, API, readiness, and time stop rules, and deletes only that
namespace in a `finally` block. The frozen raw artifact records SHA-256 values
for both the runner and the shared batch evaluator; the analyzer rejects a
source-provenance mismatch.
