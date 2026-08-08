# Positive trace results index

The reportable campaign is:

- `trace-20260808T062119Z-ca37624c`

It passed `scripts/verify_trace_results.py` with 9 injected scenario episodes,
27 correlated observer trajectories, 261 hash-chained polls, 27/27 exact and
stable classifications, and zero adapter-error polls. Its own
`manifest.sha256` covers 133 files.

From the repository root, verify the reportable campaign portably with:

```bash
python3 scripts/verify_trace_results.py \
  "lab/results_trace/$(cat lab/results_trace/FINAL_CAMPAIGN)"
```

The directories prefixed `_pilot_superseded_` and `_aborted_verifier_hardening_`
are non-reportable development records. The pilot preceded the corrected
experimental-unit wording and nearest-rank P95 calculation. The aborted run was
intentionally stopped before completion while the cross-layer verifier was
hardened. Neither directory is a source for numerical claims.
