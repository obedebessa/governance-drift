# Governance Drift v1.4 — consolidated disposition of the v1.3 reviews

This memo consolidates the three reviews supplied on 2026-08-07. Version
1.4.0 is a local, unpublished candidate; the public DOI still resolves to
v1.2.0.

| Review point | Disposition in v1.4 |
|---|---|
| Undefined LH1–LH4 | Defined falsifiable LH1–LH3 before the protocol; removed LH4. |
| Bare `RQ` label | Renamed the umbrella question RQ0 and mapped it explicitly. |
| Single approval snapshot | Replaced with an append-only sequence and latest-applicable covering-basis rule. |
| Authorization semantics | Added one-shot, continuing, and temporary-exception modes plus prospective/retroactive revocation and retained-proof semantics. |
| Environment equality | Replaced formally with predicate violations; the evaluator now applies equality predicates only to recorded fields. |
| Evidence sufficiency | Defined presence, completeness, freshness, integrity, authenticity, subject linkage, and stream health; distinguished missing evidence from explicit negative or revoked records. |
| “Stale digest” ambiguity | Renamed the test to a fresh but uncovered running digest; genuinely stale evidence remains undecidable. |
| S9 ambiguity | Clarified that the authoritative source record proving coverage/validity is lost while the immutable snapshot pointer survives. |
| Weak T0 baseline | Removed the 71% result from the abstract and conclusion; retained T0 only as a diagnostic of counterfactual scoring. |
| Correlated cadence observations | Added cadence-wise Wilson intervals and a 50,000-resample bootstrap over 180 injection clusters. |
| Uninstantiated UCR/PVD/ADR/GCC | Instantiated fixture-level GCC and explained why the designed scenario mix cannot estimate operational UCR/PVD/ADR without manufacturing prevalence. |
| Repeated novelty defense | Kept one direct defense in Related Work and removed the duplicate Discussion subsection. |
| Broad novelty claim | Narrowed to the joint formalization of operational state, evolving context, and immutable admitted basis as a class-resolved post-admission relation. |
| Adoption path | Added the minimal T0n+T1+T2 rollout and a GCC-first expansion path. |
| Tag versus digest semantics | Stated explicitly that T0n compares declared references and T3 checks running digests against approval subjects. |
| Production-cadence miss rate | Added the idealized transient miss formula but declined a production percentage without a field dwell-time distribution. |
| Second stack, live compound cases, long soak | Not claimed as completed; retained as external-validity work. |
| GitHub/Zenodo release | Not performed in this editing pass. The manuscript remains explicitly non-archival until an intentional tag, checksum, and Zenodo deposit exist. |

The central empirical boundary is unchanged: 180 injections on one pinned
Kind/Flux/Kyverno stack establish bounded realizability and within-laboratory
behavior, not field prevalence or production effectiveness.
