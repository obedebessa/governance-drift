# Positive trace results index

The reportable campaign is:

- `trace-20260808T085529Z-7e8fc786`

It passed `scripts/verify_trace_results.py` with 9 injected scenario episodes,
27 correlated observer trajectories, 261 hash-chained polls, 27/27 exact
classifications and 27/27 with two-poll exact persistence, and zero
adapter-error polls. Its own
`manifest.sha256` covers 133 files.

The reportable scenario and campaign summaries use schema v2 fields beginning
`two_poll_exact`; the terminology denotes exactly two consecutive exact polls,
not long-horizon stability. Raw poll chains and numerical observations are
unchanged from capture.

From the repository root, verify the reportable campaign portably with:

```bash
python3 scripts/verify_trace_results.py \
  "lab/results_trace/$(cat lab/results_trace/FINAL_CAMPAIGN)"
```

Superseded pilot, verifier-hardening, and pre-intent campaigns are ignored and
excluded from the release. `FINAL_CAMPAIGN` names the sole included and
reportable trace directory; no ignored directory is a source for numerical
claims.
