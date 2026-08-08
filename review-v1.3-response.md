# Governance Drift v1.3 — disposition of the v1.2 review

This memo records what changed in the local, unpublished v1.3 candidate.

| Review point | Disposition in v1.3 |
|---|---|
| Live evaluation privileges one priority label | Implemented. The evaluator emits the complete substantive vector plus evidence status. Scoring reports exact-set accuracy, per-component precision/recall, and Hamming loss. The first verdict is only an operator-facing projection. |
| Validate vector composition | Implemented in the closed world with S10 policy+authorization, S11 authorization+environment, and S12 compound substantive state under approval-evidence loss. Live S6 now genuinely combines intent and authorization by deploying an unapproved predecessor digest. |
| Evidence drift differs ontologically | Implemented. The model now separates five substantive distances `GD_sub` from a five-entry observability/decidability mask `O`; evidence drift is a transversal transition to undecidability. |
| Tier sufficiency is nearly tautological | Implemented. A tier-minimality proposition constructs lower-tier-indistinguishable worlds with different verdicts for policy, authorization, lineage, and environment evidence. |
| Approval and inventory streams are file-backed | Retained and made explicit as the main external-validity limitation. A second stack and production-backed services are not claimed. |
| Benign false-alarm evaluation is short | Not expanded into a 1–4 hour soak. The manuscript now states the 210-second boundary explicitly and makes no population-rate claim. |
| Loss, stale, malformed, and duplicate evidence | Partially implemented as deterministic fault-contract tests: API outage, malformed PolicyReport, missing approval basis, missing inventory, stale digest, and duplicate valid approval. Delay/reordering distributions and real transport faults remain future work. |
| Execute an observed per-mechanism comparison | Not claimed. The current study records class-resolved component outputs but does not present Flux/Kyverno/lineage/inventory as independently calibrated competing products. The conceptual comparison remains scoped as native decision capability. |
| Figure 5 says S1–S8 | Corrected to S1–S9. |
| Measurement environment is incomplete | Implemented: CPU/architecture, RAM, OS, Docker, Kind capacity and limits, concurrent-load boundary, Python, and monotonic-clock implementation/resolution are recorded. |
| Experimental unit is ambiguous | Implemented: 180 injections are the units; 540 cadence observations are correlated repeated measurements. |
| Abstract is too dense | Shortened while preserving headline results and limitations. |
| “Legitimate successors” is informal | Formalized as a finite chain of explicit, valid approval-covered revision transitions whose artifact subjects are covered. |

The candidate remains local until an explicit release/tag/deposit step is
authorized. The v1.2 Zenodo DOI does not represent these changes.
