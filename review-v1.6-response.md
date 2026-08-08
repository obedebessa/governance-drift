# Governance Drift v1.6.0 — disposition of the v1.5.0 external review

This document records the disposition of the external review that rated
v1.5.0 at 9.5/10. Every completed result below is tied to executable code or
frozen raw data; unsupported components and stopped targets remain explicit.
Bounded laboratory observations are not presented as field prevalence,
production reliability, or independent external validation.

## P0 corrections

1. **Activation semantics.** Approval and activation are separate durable
   events. The selected admitted basis is the unique maximal activated,
   non-aborted snapshot under an explicit `Supersedes` relation.
   Approved-but-not-activated and aborted snapshots cannot displace the running
   basis; incomparable active maxima yield `undecidable`. The v1.6/B4 executable
   ablation probes cover unactivated, aborted, successor, and parallel
   activation; they are separate from the frozen primary live campaign.
2. **Coverage terminology.** Substantive plane consistency is
   `GovPlaneCons`, total five-component consistency is `Cons`, and `GCC` is
   reserved for Governance Conformance Coverage.
3. **Statistical labels.** Detection rate is 538/540 (99.63%), conditional
   exact-set accuracy is 538/538 (100%), unconditional exact-classification
   success is 538/540 (99.63%), and unconditional Hamming loss is 0.000617.
   Live outputs are provisional class-set classifications under the
   laboratory's sequential-snapshot assumption, not complete formal vectors.
4. **S9/S12 terminology.** S9 is required live-status evidence loss for
   continuing authority while immutable approval proof remains: authorization
   becomes undecidable, the evidence class is emitted, and historically
   authorized intent remains consistent. S12 is rollback plus loss of required
   authorization evidence: historical intent is inconsistent and authorization
   undecidable, yielding intent plus evidence. Prospective expiry/revocation
   changes authorization, not historical intent; only explicit retroactive
   invalidation can invalidate that history. Neither scenario is called
   approval-record deletion.
5. **Completion metrics.** The metrics section now defines first alert, ESC,
   diagnostic gap, and watermark-qualified Stable-VCL. The live compound
   campaign reports first provisional class-set completion under sequential
   reads rather than watermark-qualified persistence.
6. **Baseline independence.** The comparator is called a *separately
   implemented no-join composite*. It uses a distinct code path but the same
   author, world, protocol, and oracle; it is not independent validation.
7. **Scanning claim.** Zero event latency is limited to the closed-world model
   after the deciding fact is available. Live re-evaluation remains bounded by
   watches, caches, audits, scans, and cadence.
8. **Experimental units.** Compound tables separate five injected episodes
   per scenario from fifteen correlated cadence observations.

## P1 extensions implemented or executed in v1.6.0

- **Ablation ladder:** B0–B4 rises from 75.0% to 100% unconditional exact
  scenario vectors over 240 paired units; targeted semantic-probe coverage is
  3/10, 3/10, 4/10, 5/10, and 10/10 across B0--B4.
- **Real transport faults:** a two-hop localhost TCP campaign covers nine
  profiles, 636 trace events, 25 evaluations, and eleven fault-state safety
  checks with zero safety or component-local masking failure.
- **Scaling:** an in-memory batched-join campaign retains 960 path-specific
  timing samples and separates correlated paths. At 1,000 units, full-sweep
  P50/P95/P99 is 6.067/6.881/6.927 ms; these are core timings, not Kubernetes
  throughput claims.
- **Live multi-deployment path:** completed 10- and 50-Deployment targets over
  settled churn. The 50-unit phase spans 50–60 Pods and 100–120 containers;
  across both completed targets, 1,200/1,200 repeated per-unit and 240/240
  nested decisions are exact with zero measured API-command error. The
  100-unit target hit its declared readiness stop and contributes no admitted
  timing sample.
- **Transition safety:** twenty benign changes observed from before mutation
  produced 777 polls. No policy, authorization, intent, or environment drift
  was emitted; seven
  configuration-convergence and 36 fail-safe epistemic warning polls were
  retained, and all 60 trajectories ended with a provisionally consistent
  classification.
- **Temporal-cut implementation:** seven executable tests reject absent or
  lagging watermarks, stale records, straddling capture intervals, ambiguous
  latest records, and excessive cross-stream spread.
- **Real-process trace replication:** nine injected scenario episodes were
  observed by three separate 1/5/10-second processes each. All 27 correlated
  trajectories reached the exact expected set and two-poll exact persistence
  across 261 raw polls, with zero adapter error. Cause-to-first-exact
  median/P95 is 4.465/9.764 seconds, and two-poll exact-persistence median/P95
  is 9.522/19.811 seconds; 133 manifest entries and cleanup are verified. The
  two-poll check is not watermark-qualified persistence. This
  is a bounded trace audit, not 27 independent experiments or a rate estimate.
- **Second reconciler/policy-engine stack:** the isolated Argo CD
  v3.4.2/Gatekeeper v3.22.2 campaign completed five repetitions each of S1,
  S3, and S4. All 15/15 projected singleton classifications are exact over the
  declared evaluated components. Median first-honest/first-substantive/ESC
  latencies are 0.207/0.646/1.677 seconds for S1, 0.157/1.659/1.659 for S3,
  and 0.215/0.215/0.650 for S4. All 15 baseline restorations succeeded, with
  zero post-restoration differences, API-read errors, or final undecidable
  outcomes; the stop rule was not triggered and cleanup was verified. Only S1
  configuration and S3 policy are native cross-stack component paths; S4
  reuses the shared T3 digest adapter, and intent and environment remain
  unevaluated. No equivalence or independent-validation claim is made.

## Remaining boundaries stated rather than hidden

The paper does not claim natural occurrence, organizational prevalence,
production false-alarm rates, arbitrary distributed-snapshot coherence,
multi-cluster capacity, or remediation-loop stability. Unsupported targets
and aborted campaigns are never converted into positive observations. The
artifact exposes its stop rules, right-censoring, correlations, excluded
costs, evidence contracts, hashes, and cleanup records so that these limits
are inspectable rather than rhetorical.
