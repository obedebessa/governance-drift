# Governance Drift v1.5 — disposition of the final strategic review

Version 1.5.0 is the submission candidate produced from the v1.4 review and
archived at `10.5281/zenodo.21845707` under repository tag `v1.5.0`.

| Review point | Disposition in v1.5 |
|---|---|
| Scalability and control-plane overhead | Added an analytic time/space envelope, event-indexed incremental cost, cache and sharding strategy, and explicit API-quota boundary. No unexecuted throughput number is claimed. |
| Legacy cold start | Added a signed retrospective-baseline path that preserves provenance, marks reconstructed evidence, reports partial GCC, and never rewrites it as historical approval. |
| Fail-open versus fail-closed | Separated fail-closed governance claims from business-availability actuation and added class-, criticality-, blast-radius-, and evidence-age-aware response guidance. |
| Mutable approval anchor | Replaced residual mutable notation with the selected immutable basis `B_x(t)` and `A_B^proof` throughout policy, authorization, intent, environment, architecture, and proofs. |
| Authorization modes | Defined one-shot, continuing, and temporary-exception applicability, mode-aware legitimate successors, prospective/retroactive revocation, and undecidable evidence states. |
| Independent comparator | Added a composite plane-local baseline without the admitted-basis join: 180/240 exact units (75%) versus 240/240 (100%) for joined T4, with zero paired-control false-alarm events. |
| Live compound scenarios | Executed S10--S12 five times each at three cadences. All 45 observations reached the exact provisional class-set target under sequential reads. S10 exposed the distinction between first alert and exact-set completion. |
| Longer negative control | Executed six 300-second benign windows: 1,800 seconds and 4,680 correlated polls with zero substantive alarms and zero epistemic warnings. |
| Delayed/reordered evidence | Expanded the deterministic suite to 15 tests, including stale, excessively delayed, reordered, and subject-mismatched envelopes; the manuscript does not relabel these unit contracts as faulty-transport reliability. |
| Related scholarly foundations | Added runtime compliance monitoring, temporal authorization, TRBAC, UCON, and contemporary usage-control work; narrowed novelty to the time-indexed admitted-basis join and class-resolved post-admission relation. |
| Central visuals | Enlarged the architecture and detectability matrix to full-page width and updated the approval-history notation. |
| Metrics | Added UCR identification bounds, corrected the ADR denominator, made estate-level GCC units explicit, and retained observation-level figures only as contract exercises. |

## Verification

- Closed-world outputs and the no-join baseline regenerate byte-identically.
- All 15 evaluator contract tests pass.
- Frozen S1--S9 laboratory observations pass their CSV/JSON/table integrity checks.
- The S10--S12 extension and 30-minute benign soak pass their independent validator.
- The final PDF is compiled from source, checked for unresolved references and
  overfull boxes, rendered page by page, and visually inspected before release.
