# Reviewer #2 Simulation — Report and Point-by-Point Response

> **Post-review v1.2.0 amendment (2026-08-07).** The Kubernetes/GitOps
> laboratory now executes 20 repetitions of S1–S9 at three evaluator cadences
> on a pinned Kind/Flux/Kyverno stack, plus 20 benign windows. It produced
> 538/540 detections, 538/538 correct classifications conditional on
> detection, and zero alarms in 543 benign-control polls. Injection, onset,
> detection, and evidence-completion clocks are separated. The manuscript
> scopes this as bounded realizability and within-laboratory behavior, not
> evidence of natural occurrence, field prevalence, or production behavior.

**Manuscript:** *Governance Drift: Measuring Divergence Between Approved Intent and Operational Reality in Cloud-Native Systems*
**Process:** the compiled draft — including the executed detector study (`code/detector_study.py`), its data (`data/*.csv`), and the laboratory artifact (`lab/`) — went to an independent adversarial review (simulated Reviewer #2, editor-assigned mission: *reject this as "just configuration drift"*). The reviewer verified every quoted number against the data, re-executed the study, audited the code against the paper's definitions, and audited the lab directory against the paper's claims about it. The review was severe and largely correct; the revision required a rewritten study (v2), text repositioning, and completion of the lab artifact — not cosmetics. §1 summarizes the review; §2 the disposition of every item.

---

## 1. The review as received (summary)

> **Recommendation: Reject** (resubmission after fundamental rework: execution of the laboratory, field evidence, and repair of the taxonomy/study circularity).

**Major objections:** **M1** most scenarios *are* configuration drift under standard-but-widened comparison semantics — the paper's own code comments concede it ("subclass of configuration at digest semantics"; "unmanaged-resource configuration drift"), AWS Config detects out-of-band cloud changes without IaC desired state, and every implemented check is a two-place comparison against some reference; of the three-pronged defense, only the counterexamples survive intact. **M2** the staircase is tautological (world and detectors co-designed; Prop. 2 "proved" by the table and the table validated by the proposition); the naive-T0 74% headline is manufactured (strawman detector, arbitrary churn parameters, no sensitivity analysis) and the governance tiers' zero FP is rigged (no benign governance events in the control — a legitimate deployment or satisfied policy revision would have tripped them). **M3** the implementation contradicts the definitions: T1 compares version strings instead of re-evaluating admission (Def. 3), T4 compares against hardcoded literals instead of recorded σ₀ — exactly the CSPM behavior the paper disclaims — and the snapshot contains no σ₀ at all. **M4** the taxonomy fails its own consistency test: a phantom "artifact" class in the code's ground truth that exists in no definition; S4 unplaceable in the taxonomy figure; S6 satisfying two class definitions with "correct classification" decided by Python if-statement ordering; evidence drift never exercised; the tier-to-class mapping not even a function. **M5** per-tier continuous detection already exists in cited tooling (Kyverno background audit — used by the paper's own lab, AWS Config, BinAuthz continuous validation): the increment is the approved-basis join, never evaluated on any real system. **M6** the "shipped" laboratory contained 3 files of the ~12 the README references; 12 pages with no field data is thin for a journal. **Minors m1–m12**: hand-edited "programmatic" table header; stale docstrings; false "equivalently" in Def. 8; undefined "policy-weighted aggregate"; figure annotation inconsistencies; S2 latency as an artifact of a strict inequality; rounding display; uncited companion-program reference; unverifiable term search; RQ numbering; bib nits.

---

## 2. Point-by-point disposition

### Major

**M1 — "This is just configuration drift."** *Resolved by concession-and-precision, which is the only honest resolution.* The revision stops fighting the mechanical point and wins the relational one: §IX-D and §X-A now concede explicitly that every governance-tier detector is itself a comparison — against a recorded basis, a validity clock, or a coverage set — and place the claim where it is defensible: governance drift is a different *relation* (three-place, history-anchored: state × current context × admitted basis) with different event sources, different verdicts ("was admitted legitimately and the world moved" ≠ "was never compliant"), and different remediations. The phantom-class code comments that undercut the taxonomy are gone (M4). §III's dependency discussion now also bounds what the separation result claims: it rules out *deciding*, not statistical *hinting*, and the correlation of governance events with configuration histories in real estates is flagged as an open empirical question for the lab. The defense now rests on the counterexamples plus the dependency analysis, with the study explicitly demoted to implementation validation (M2).

**M2 — Tautology and rigged noise.** *Resolved to the extent a closed world permits, and re-labeled for the remainder.* The v2 study adds **benign governance churn to every stream including the control**: satisfied policy revisions (which a version-string T1 *would* have flagged — and the definition-faithful T1 correctly ignores), legitimate approved updates advancing revision+digest+approval together (which a pinned-revision intent check *would* have flagged), and hygienic exceptions removed at expiry. The governance tiers' zero false alarms are now earned, and the paper says exactly against what. Churn-rate sensitivity for T0 is measured (287/285/300 alarms per run at 10/30/50% churn) and reported with the honest observation that the rate tracks state-dwell, not event rate. The "industry default" framing of T0 is withdrawn ("we deliberately do not call T0 the industry default — production reconcilers normalize"); the abstract now attributes 71% to "our churn model." Finding F1 is retitled "implementation validation of tier dependencies" and states its epistemic value in so many words ("not field evidence for the taxonomy, a role we do not claim for it"); Prop. 2's circular proof sentence is removed and replaced by an explicit separation between the input-enumeration result and the implementation check; §IX-D no longer cites the staircase as an independent evidence prong.

**M3 — Implementation contradicts definitions.** *Resolved by re-implementation and re-execution.* v2's T1 evaluates policy *requirement sets* against the running manifest (Def. 3's admission re-evaluation): a supersession the state satisfies raises no alarm — tested by the benign policy churn. v2's approved snapshot records σ₀, and T4 compares observed environment against it (with §V now conceding plainly that the mechanics are baseline comparison — CSPM's move — and locating the difference in the reference and the joined verdict). Approvals are a proper set with subjects, covered revisions, and revocation, so "legitimate successors" exist.

**M4 — Taxonomy inconsistency.** *Resolved.* The code's class set now equals the taxonomy's six classes exactly. S4's ground truth is `authorization` (running digest not covered by any valid approval — Def. 4's third clause), matching the paper's own text; the ad-hoc artifact/authorization disambiguation rule is gone. Ground truth is a **class set** per scenario (Def. 8 now states component non-exclusivity; S6 = {intent, authorization}), classification is correct iff the reported class is in the set, and the declared evaluation order is documented as an implementation choice that affects only which member of a multi-component drift is reported first. **S9 (approval-record deletion) is added**, exercising evidence drift with the explicit undecidable verdict — all six classes now tested. Figure annotations reconciled (authorization: S2, S4, S8; evidence: S9 at T2; intent: S6 "jointly authz"); the abstract's "unlocks exactly the classes" reduced to the scenario-level statement the data supports.

**M5 — Existing tools already detect per tier.** *Resolved by explicit credit and honest repositioning.* §II now names the fragments (Kyverno background scanning re-evaluates against current policy; configuration recorders track unmanaged resources; continuous validation re-checks artifact policy) and locates the gap precisely: no joint check against the admitted basis, no validity clocks on the authorization itself. §V's "Relation to Existing Mechanisms" and §IX-D restate novelty as the join + class-resolved verdicts, state that six of nine scenarios trigger *some* existing detector, and assign the operational-value question of the increment to LH1–LH4. The repeated live laboratory now checks the joined evaluator on a controlled stack; field prevalence and operational value in a natural estate remain open.

**M6 — Incomplete lab; thin paper.** *Resolved for the controlled laboratory; field evidence remains open.* The complete Kind/Flux/Kyverno stack was executed with 20 repetitions of S1–S9, randomized scenario order, three evaluator cadences, four timestamps, and 20 benign-control windows. The run produced 538/540 detections and 538/538 correct classifications conditional on detection; zero of 543 benign-control polls alarmed. The two S1 misses at slower cadences are retained and explained by Flux reconciliation before the first scheduled poll. Raw observations, summaries, tables, manifests, versions, seed, and verification scripts ship in `lab/`. Measuring a natural estate remains a separate next step; the manuscript does not present the controlled injections as evidence of natural occurrence.

### Minor

- **m1** table header now emitted by the same program as the rows. ✔
- **m2** docstring rewritten (six tiers, correct outputs, lineage vocabulary); unused parameter removed. ✔
- **m3** Def. 8's false "equivalently" parenthetical removed (consistency now requires all components zero *and none undecidable*). ✔
- **m4** "policy-weighted aggregate" → "componentwise condition"; non-exclusivity stated. ✔
- **m5** figure annotations reconciled across Figs. 2 and 4; Fig. 3 caption unchanged but panels map to S3/S2/S4/S6 with S4 now consistently authorization-classed. ✔ (EXC-1's grant instant remains implicit in Fig. 3 — noted as a candidate polish.) △
- **m6** expiry semantics now inclusive (`>=`); S2 latency is 0 and the over-narrated "designed exception" text is gone. ✔
- **m7** latencies emitted with one decimal. ✔
- **m8** companion-program sentence rewritten in self-contained terms. ✔
- **m9** term-search query strings included. ✔
- **m10** the umbrella RQ is now answered explicitly, with conditions, in the conclusion. ✔
- **m11** bib: vendor-doc dating kept with access dates (a limitation of citing living documentation; versions to be pinned at submission); single-blog "industry usage" citation retained but the sentence claims only representativeness of usage, not a survey. △
- **m12** font-shape substitution warning is cosmetic; left for template transplant. △

---

## 3. Residual state

The revised manuscript and package include the definition-faithful closed-world study and an executed repeated live laboratory. The latter covers 180 positive injections observed at three cadences and 20 benign windows, preserves all timeout outcomes, reports S6's full expected class set, and separates actuation, DDL, end-to-end latency, and TTE. The remaining empirical boundary is explicit: no natural repository or production estate has yet been measured. The paper therefore claims bounded realizability and within-laboratory behavior, not natural occurrence, field prevalence, or production effectiveness.
