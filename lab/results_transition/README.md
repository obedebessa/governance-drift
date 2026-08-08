# Transition-inclusive benign controls

This campaign starts three isolated 0.5-, 2-, and 10-second observer processes
before each benign mutation. It retains 20 windows, 60 correlated trajectories,
and 777 append-only polls. No poll reported policy, authorization, intent, or
environment drift. Seven polls reported temporary configuration convergence
during approved rollback, and 36 polls emitted fail-safe epistemic warnings
while a complete ready-container digest set was unavailable. Every one of the
60 trajectories ended with a provisionally consistent classification.

The machine-readable field
`non_configuration_governance_plane_drift_polls` counts only policy,
authorization, intent, and environment drift. Configuration-convergence and
epistemic-warning polls have separate counters; therefore the zero value must
not be described as zero total governance drift.

- `raw/`: one NDJSON log and readiness/stop sentinels per observer process.
- `transition_observations.json`: raw observations plus campaign summary.
- `transition_summary.json`: flattened per-observer summary.
- `transition_analysis.json`: per-control reconstruction.
- `table_transition_controls.tex`: generated manuscript table.

Verify with `python3 scripts/verify_transition_controls.py` and regenerate the
analysis table with `python3 scripts/analyze_transition_controls.py`.
