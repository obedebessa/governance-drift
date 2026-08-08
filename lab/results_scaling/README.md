# Synthetic batch-evaluator scaling results

This directory contains a deterministic-data, in-memory Python
microbenchmark. It is not a Kubernetes, controller, API-server, network, or
production-capacity benchmark.

Reproduce the experiment and derived outputs from the repository root:

```bash
PYTHONHASHSEED=0 python3 lab/run_scaling_experiment.py
python3 scripts/analyze_scaling.py
PYTHONPATH=lab python3 lab/test_batch_evaluator.py -v
```

The frozen protocol evaluates 1, 10, 25, 50, 100, 250, 500, and 1,000
synthetic `UnitRef`s. Each unit has an exact namespace/name/UID identity, a
UID-scoped approval, two active pods, and two containers per pod. Forty
randomized timing samples are collected for a full sweep, a total policy-event
fan-out, and a deterministic 10% artifact-event fan-out at every estate size.
Inner iterations amortize timer noise; every raw row records their count and
the unamortized elapsed nanoseconds.

All 960 measured vectors were exact. At 1,000 units, a full in-memory sweep
took 6.067 ms at P50, 6.881 ms at P95, and 6.927 ms at P99, corresponding to a
median 164,828 evaluated units per second. These are evaluator-core results,
not end-to-end production throughput.

- `scaling_raw.csv` and `scaling_raw.json`: 960 raw timing samples and complete
  protocol/platform metadata.
- `scaling_summary.json`: validated P50/P95/P99 and throughput summaries.
- `table_scaling.tex`: full-sweep results and modeled call counts.
- `table_fanout.tex`: total and subset event-index fan-out results.

The six-call full-sweep and one-call event counts are architectural models,
not instrumented calls. Report them only with that qualification. The measured
times cover Python indexing and semantic decision for a full sweep, and
semantic decision over prepared indices for fan-out. Evidence acquisition,
decoding, transport, controller convergence, contention, persistence, and
failure recovery are outside the measurement boundary.

CPU frequency and affinity were not controlled, ordinary host background work
was not disabled, and the repeated inner loops are timer-noise amortization
rather than independent experimental units. The frozen raw artifact records
SHA-256 values for the runner and evaluator; the analyzer rejects source
provenance mismatch.
