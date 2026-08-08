# Governance Drift Related-Work Scoping Protocol

- Protocol version: 1.0
- Search and verification cutoff: 2026-08-08
- Reviewer: one
- Record of decisions: `literature/scoping_review_records.csv`

## Review type and question

This is a bounded scoping review, not a systematic literature review, a
meta-analysis, or a bibliometric census. It updates the paper's terminology and
nearest-neighbor analysis; it does not estimate prevalence, effect size, or the
total number of works in any field.

The review asks:

1. Which primary research or official sources available by the cutoff use
   *governance drift* or a closely adjacent construct?
2. Which prior systems contribute configuration baselines, policy consistency,
   temporal authorization, or cross-layer/time-aware provenance?
3. Does a screened source natively join (a) a deployment's current operational
   state, (b) evolving governance context, and (c) the uniquely activated,
   immutable approval basis, and then emit class-resolved post-admission
   verdicts under explicit evidence and temporal-coherence rules?

Question 3 is the manuscript's deliberately narrow novelty test. A work can be
highly relevant and included even when its unit of analysis differs.

## Search log

All queries below were run on 2026-08-08. Quotation marks indicate phrase
searches. General web search was used only for discovery; a claim entered the
manuscript only after checking an official product/standards page, a publisher
or DOI record, an arXiv/OpenReview primary manuscript, or a primary proceedings
page.

| ID | Exact query | Discovery sources | Purpose |
|---|---|---|---|
| Q1 | `"governance drift"` | web scholarly discovery; Crossref metadata/DOI resolution | exact-term precedence and false positives |
| Q2 | `"governance drift" cloud infrastructure configuration` | web scholarly discovery; AWS documentation; Crossref; SSRN | cloud-native and multi-cloud neighbors |
| Q3 | `"AI governance drift"` | web scholarly discovery; Taylor & Francis/Crossref; arXiv; OpenReview | AI-specific uses and unit-of-analysis distinctions |
| Q4 | `("policy drift" OR "policy consistency") multi-cloud governance` | web scholarly discovery; Crossref; SSRN | cross-provider policy consistency |
| Q5 | `("approval basis" OR "admission basis" OR "approved baseline") drift cloud deployment governance` | web scholarly discovery; NIST CSRC/NVL Publications | historical-baseline and admitted-basis candidates |
| Q6 | `"end-to-end authorization"`; `"layering in provenance systems"`; `"time-aware provenance" distributed systems` | USENIX proceedings and papers | authorization and provenance foundations |
| Q7 | `"stateful governance" stale authorization approval`; `"governance evidence degradation"` | arXiv primary records and manuscripts | closest temporal-authorization and evidence-monitoring work |
| Q8 | exact DOI lookups `10.2139/ssrn.6713338` and `10.1080/07366981.2026.2685305` | DOI content negotiation; Crossref; publisher/SSRN records | authoritative 2026 metadata and status |

Primary source roots checked were:

- `docs.aws.amazon.com/controltower/`
- `csrc.nist.gov/` and `nvlpubs.nist.gov/`
- `usenix.org/`
- `papers.ssrn.com/` and DOI/Crossref metadata
- `tandfonline.com/` and DOI/Crossref metadata
- `arxiv.org/`
- `openreview.net/`

## Eligibility and screening

Included records satisfy at least one of these criteria:

- an official standard or product definition establishes terminology or a
  comparator relevant to the model;
- a primary research paper, proceedings paper, or clearly labelled preprint
  defines an adjacent decision object; or
- a primary systems paper supplies a foundational mechanism the manuscript
  reuses (historical baselines, end-to-end authority, layered provenance, or
  time-aware provenance).

Excluded records are retained in the ledger when they are useful false
positives but concern a different object, such as statistical concept drift,
construction-project risk, or institutional ethics. Secondary marketing pages,
duplicates, inaccessible snippets without resolvable primary metadata, and
items published after the cutoff are not evidence for manuscript claims.

Screening was performed by one reviewer at title/abstract or official-page
level, with full text inspected for close temporal and provenance candidates.
There was no independent duplicate screen, formal quality score, risk-of-bias
assessment, citation-network saturation test, or exhaustive export of noisy web
results. The CSV is the complete decision ledger for the bounded candidate set
that changed or tested the manuscript's terminology and novelty statement; it
is not a claim about the size of the wider literature.

## Claim discipline

The review supports four rules used in the manuscript:

1. Do not claim that this paper coins *governance drift*. AWS Control Tower and
   2026 research use the phrase explicitly.
2. Label Marella and the arXiv works as preprints; do not imply peer review.
3. Distinguish neighboring decision objects affirmatively before stating the
   remaining gap.
4. Phrase negative novelty only as an observation about the screened set. Do
   not assert that no work exists anywhere.

Under these rules, the surviving claim is: among the screened sources, none
natively combines all three model operands with the manuscript's
deployment-level identity, activation, evidence, temporal-coherence, and
class-resolved verdict semantics.

## Reproduction and validation

Run:

```sh
python3 scripts/run_scoping_review.py
```

The script performs no network calls and writes no files. It validates the CSV
schema, record identifiers, decision labels, locators, and dates, then prints
counts derived directly from the ledger. This avoids hand-maintained or
invented aggregate counts.
