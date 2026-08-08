# Governance Drift positive trace campaign

Campaign `trace-20260808T062119Z-ca37624c` executes S1--S9 in the dedicated `govdrift-trace` namespace. Each
scenario is observed by three separate OS processes at 1, 5, and 10 second cadences.
Every poll is an fsync'd NDJSON record whose SHA-256 field chains to the prior poll.
Injection records contain a unique ID, source hashes, command-ledger indices, input
fingerprints, and monotonic plus UTC cause/effect timestamps. A trajectory reaches
"stable exact" on its second consecutive exact expected classification.

The experimental unit reported in `campaign_summary.json` is one injected scenario
episode (n=9). A scenario-by-observer-cadence trajectory is a correlated observational
unit (9 x 3 = 27). The three processes in a scenario are independently scheduled and
have distinct PIDs/UUIDs, but they observe the same injected cause and share the host,
API server, namespace, and evidence store; they are repeated measurements, not
independent statistical replicates. Pooled latency summaries across the 27 trajectories
are explicitly descriptive.

## Verification

Run `python3 scripts/verify_trace_results.py /Users/obede/Library/Mobile Documents/com~apple~CloudDocs/EB-1 Vencedor/Scholarly Articles/09_Governance_Drift/lab/results_trace/trace-20260808T062119Z-ca37624c` from the repository root.
The verifier recomputes every poll chain, injection hash, event marker, image-lock
constraint, denominator, and `manifest.sha256` entry.

## Scope and exact limitations

1. This is one positive replication campaign on one local, single-control-plane Kind
   cluster (linux/arm64), not an external-validity, throughput, or scalability study.
2. Detection times are descriptive wall-clock observations. The runner and observers
   share the host monotonic clock; no distributed-clock claim is made.
3. "Stable" means two consecutive exact polls. It does not establish long-horizon
   persistence or remediation safety.
4. Flux reconciliation is explicitly requested in setup/reset and S6, so S6 timings
   include forced reconciliation rather than a natural interval distribution.
5. S4 uses a namespaced Kyverno admission mutation to emulate artifact substitution.
   It validates the materialized-image lineage path, not a real registry compromise.
6. S5 and S7 use a file-backed cloud-inventory adapter. They validate environment
   predicates and polling traces, not a live cloud provider API.
7. All workload and container base references used by the campaign are digest locked.
   The Python git-server image is locked, but Debian packages installed inside that
   ephemeral server at startup are not snapshot-pinned; this affects reproducibility
   of the transport helper, not workload identity.
8. The campaign does not test partitions, API saturation, multi-node scheduling,
   adversarial log tampering after capture, or automatic remediation.
9. The immutable proof/basis is enforced by read-only files during each scenario, not
   by an external transparency service or hardware root of trust.
10. The results demonstrate reproducible detector behavior for the declared scenarios;
    they do not estimate real-world prevalence or false-positive rates.
