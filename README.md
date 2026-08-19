# Governance Drift

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Concept DOI](https://zenodo.org/badge/DOI/10.5281/zenodo.21841458.svg)](https://doi.org/10.5281/zenodo.21841458)

Reproducibility package for the manuscript:

> **Governance Drift: Measuring Divergence Between Approved Intent and
> Operational Reality in Cloud-Native Systems**  
> Obede Bessa Rocha da Silva — Independent Researcher

The package contains the complete LaTeX source, a dependency-free closed-world
detector study, canonical outputs, and an executed Kubernetes/GitOps
laboratory. Version 1.5.0 added complete class-set live evaluation,
exact-set/per-component/Hamming metrics, three compound closed-world
scenarios, immutable approval-snapshot history, authorization modes,
component evidence contracts, formal hypotheses, clustered uncertainty, a
separately implemented no-join baseline, a live compound-class-set extension,
six five-minute benign windows, and a tier-minimality result. Version 1.6.0
adds activation-safe basis selection, admissible temporal cuts, a B0--B4
ablation ladder, transition-inclusive controls, real-process trace auditing,
a two-hop TCP fault campaign, in-memory scaling, and a live multi-Deployment
adapter path, plus a bounded Argo CD/Gatekeeper cross-stack replication. The
release results, source, manuscript, and integrity metadata are frozen under
tag `v1.6.0` and archived at <https://doi.org/10.5281/zenodo.21847543>.

## Read and cite the paper

- [Read the manuscript (PDF)](output/pdf/governance-drift-v1.6.0.pdf)
- [Open the permanent Zenodo record](https://doi.org/10.5281/zenodo.21847543)
- Use GitHub's **Cite this repository** control for automatically generated
  citation formats. The preferred citation in `CITATION.cff` points to the
  manuscript; cite the software package separately only when referring to its
  code, data, or reproducibility materials.

**APA**

> Bessa Rocha da Silva, O. (2026). *Governance Drift: Measuring Divergence
> Between Approved Intent and Operational Reality in Cloud-Native Systems*
> (Version 1.6.0) [Preprint]. Zenodo.
> https://doi.org/10.5281/zenodo.21847543

**BibTeX**

```bibtex
@techreport{bessa_rocha_da_silva_governance_drift_2026,
  author  = {Obede Bessa Rocha da Silva},
  title   = {Governance Drift: Measuring Divergence Between Approved Intent and Operational Reality in Cloud-Native Systems},
  year    = {2026},
  month   = aug,
  version = {1.6.0},
  doi     = {10.5281/zenodo.21847543},
  url     = {https://doi.org/10.5281/zenodo.21847543},
  note    = {Preprint}
}
```

## Evidence boundary

The closed-world study validates detector semantics and evidence-tier
dependencies. The bounded live laboratory executes 20 repetitions of each of
nine scenarios at 0.5-, 2-, and 10-second cadences on a pinned
Kind/Flux/Kyverno stack. The primary live evaluator reports provisional class
sets under a sequential-snapshot laboratory assumption and keeps its priority
verdict only as an operational projection. It detected 538/540 cadence
observations; all 538 detected classifications matched exactly, with
global Hamming loss 0.000617. Twenty benign windows spanning six change
families produced zero substantive alarms and zero epistemic warnings in 543
polls. The secondary extension completed 45/45 exact provisional compound
class sets; six
separate 300-second benign windows then produced no substantive or epistemic
alarm in 4,680 polls. Transition-inclusive controls retained 777 polls over 20
benign changes: no policy, authorization, intent, or environment drift; seven
configuration-convergence polls, 36 fail-safe epistemic warning polls, and
60/60 trajectories ending with a provisionally consistent classification. A separate trace audit injected nine
episodes and used three isolated observer processes per episode: all 27
correlated trajectories reached the exact expected class set and two-poll
exact persistence across 261 raw polls, with zero adapter error. Cause-to-first
exact latency had median/P95 4.465/9.764 seconds; the corresponding two-poll
exact-persistence latency was 9.522/19.811 seconds. The latter is not
watermark-qualified persistence. A live fleet path completed 10- and
50-Deployment targets; across those completed targets it retained 1,200/1,200
exact per-unit and 240/240 exact nested decisions. At 50 Deployments it spanned
50--60 Pods and 100--120 containers. The
100-Deployment target hit a declared readiness stop and contributes no
timing sample. On a separate Kind cluster, the frozen Argo CD/Gatekeeper
campaign completed five repetitions each of S1, S3, and S4: all 15/15 projected
singleton classifications were exact over the declared evaluated components.
Median first-honest/first-substantive/exact-set-completion latencies were
0.207/0.646/1.677 seconds for S1, 0.157/1.659/1.659 for S3, and
0.215/0.215/0.650 for S4. All 15 baseline restorations succeeded, with zero
post-restoration differences, API-read errors, or final undecidable outcomes;
the stop rule was not triggered and cleanup was verified. Argo CD and
Gatekeeper provide the native configuration and policy paths; S4 authorization
reuses the shared digest adapter, while intent and environment are not
evaluated. The completed data
demonstrate bounded realizability and within-laboratory behavior—not natural
occurrence, field prevalence, production reliability, or universal
false-alarm and latency distributions.

## Repository map

| Path | Purpose |
|---|---|
| `main.tex`, `sections/`, `figs/`, `refs.bib` | Complete manuscript source |
| `code/detector_study.py` | Closed-world detector study over twelve scenarios, including three compound cases |
| `code/ablation_study.py` | B0--B4 cumulative semantic ablation and targeted probes |
| `data/` | Canonical results and programmatically generated table |
| `lab/` | Executed Kubernetes/GitOps lab, scenario scripts, evaluator, and results |
| `lab/results_transition/` | Transition-inclusive benign controls and raw per-process polls |
| `lab/results_trace/` | Final nine-episode, 27-process trace and integrity manifest |
| `lab/results_faults/` | Two-hop localhost transport-fault campaign |
| `lab/results_scaling/`, `lab/results_live_fleet/` | In-memory and live-object-path scaling campaigns |
| `lab/results_cross_stack/` | Bounded Argo CD/Gatekeeper replication, raw polls, locks, analyzer, and cleanup proof |
| `scripts/verify_lab_results.py` | Integrity check for frozen live-lab outputs |
| `scripts/analyze_lab_extension.py` | Validation and summary of the compound extension and six-window benign-control campaign |
| `reviewer2-response.md` | Adversarial review and point-by-point disposition |
| `review-v1.3-response.md` | Disposition of the v1.2-to-v1.3 strategic review |
| `review-v1.4-response.md` | Consolidated disposition of the three v1.3 reviews |
| `review-v1.5-response.md` | Disposition of the final strategic review and release checks |
| `review-v1.6-response.md` | Disposition of the v1.5 external review and v1.6 extensions |
| `scripts/verify_artifact.py` | Exact re-execution check |
| `THIRD_PARTY_NOTICES.md` | Provenance and licenses for preserved upstream manifests |
| `output/pdf/` | Verified compiled manuscript |
| `originais/archive_3/` | Unmodified incoming PDF, source ZIP, and original review memo (local only) |
| `build/latex/` | Local LaTeX intermediates (local only) |
| `qa/renders/` | Page renders and contact sheets used for visual QA (local only) |

## Reproduce the detector study

Python 3.10 or newer is required; the study uses only the standard library.

```bash
python3 code/detector_study.py
PYTHONPATH=lab:code python3 -m unittest discover -s lab -p 'test_*.py' -v
python3 scripts/analyze_lab_uncertainty.py
python3 scripts/analyze_lab_extension.py
python3 scripts/analyze_cross_stack.py
python3 scripts/verify_artifact.py
```

The verifier re-executes the study and requires byte-identical canonical CSV
and LaTeX outputs.

## Reproduce or inspect the live laboratory

The `lab/README.md` lists prerequisites and exact commands. The frozen results
can be verified without recreating a cluster:

```bash
python3 scripts/verify_lab_results.py
python3 scripts/analyze_cross_stack.py
```

With Docker, Kind, and `kubectl` available, rebuild and execute the live stack:

```bash
lab/bootstrap.sh
python3 lab/run_repeated_experiment.py
```

The isolated cross-stack campaign creates and removes only the Kind cluster
`govdrift-cross`:

```bash
PYTHONPATH=lab python3 -m unittest -v lab/test_cross_stack_adapter.py
python3 lab/run_cross_stack_experiment.py
python3 scripts/analyze_cross_stack.py
```

## Compile the manuscript

With TeX Live:

```bash
pdflatex main.tex
bibtex main
pdflatex main.tex
pdflatex main.tex
```

Tectonic is also supported:

```bash
tectonic -X compile main.tex
```

## Citation and release

Citation metadata is provided in `CITATION.cff`; Zenodo deposit metadata is in
`.zenodo.json`. Version 1.6.0 is archived at
<https://doi.org/10.5281/zenodo.21847543> and corresponds to tag `v1.6.0`.
Version
1.0.0 remains permanently archived at
<https://doi.org/10.5281/zenodo.21841459>; version 1.1.0 is archived at
<https://doi.org/10.5281/zenodo.21842722>; version 1.2.0 is archived at
<https://doi.org/10.5281/zenodo.21843860>. Version 1.5.0 is archived at
<https://doi.org/10.5281/zenodo.21845707>. The
concept DOI <https://doi.org/10.5281/zenodo.21841458> resolves to the latest
published version.

## License

Code, manuscript source, protocol text, data, and package metadata are released
under the MIT License. Preserved upstream manifests remain under their
respective licenses; see `THIRD_PARTY_NOTICES.md`. Third-party citations remain
subject to their original rights.
