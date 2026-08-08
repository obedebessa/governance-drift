# Controlled localhost evidence-transport fault campaign

This directory contains deterministic record schedules executed through two
real TCP hops on `127.0.0.1`. It validates fail-closed evidence-envelope
contracts, not production network reliability, throughput, or latency.

The sender emits `delivered_at=null`; the gateway stamps `delivered_at` from a
controlled receiver clock. Raw records carry per-profile ordinals in actual
audit order. Fault schedules, logical times, states, and verdicts are
deterministic. `wall_elapsed_ms` remains an observed monotonic-clock value and
is intentionally not deterministic or a production estimate.

`transport_failure`/`transport_retry` denote failed TCP delivery followed by
retry of the unchanged envelope. `record_drop` denotes loss of a complete
application record inside the relay; TCP retransmission cannot recover it.

Recovery latency is derived uniformly from a fault evaluation to its linked
first consistent evaluation. Logical deltas are deterministic; wall-clock
medians report the available descriptive sample count shown in the table.

The analyzer independently recomputes aggregate verdicts, component-local
masks, mandatory properties, replay/non-regression, duplicate idempotence, and
retry hash preservation from the raw trace; it exits nonzero on disagreement.

- `fault_events.ndjson`: raw evaluations, declared properties, and transport events.
- `fault_observations.csv`: raw component-mask observations.
- `fault_profile_summary.csv`: one validated row per profile.
- `fault_summary.json`: aggregate checks and scope boundary.
- `table_fault_safety.tex`, `table_fault_recovery.tex`: generated manuscript tables.
