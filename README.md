# Governance Drift

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![DOI](https://zenodo.org/badge/DOI/10.5281/zenodo.21843860.svg)](https://doi.org/10.5281/zenodo.21843860)

Reproducibility package for the manuscript:

> **Governance Drift: Measuring Divergence Between Approved Intent and
> Operational Reality in Cloud-Native Systems**  
> Obede Bessa Rocha da Silva — Independent Researcher

The package contains the complete LaTeX source, a dependency-free closed-world
detector study, canonical outputs, and an executed Kubernetes/GitOps
laboratory. Version 1.2.0 adds a repeated nine-scenario experiment at three
evaluator cadences, benign live-stack controls, and separate actuation,
detection, end-to-end, and evidence clocks.

## Evidence boundary

The closed-world study validates detector semantics and evidence-tier
dependencies. The bounded live laboratory executes 20 repetitions of each of
nine scenarios at 0.5-, 2-, and 10-second cadences on a pinned
Kind/Flux/Kyverno stack. It recorded 538 detections in 540 observations; every
detected verdict belonged to its expected class set. Twenty benign windows
spanning six change families produced zero alarms in 543 polls. These data
establish bounded realizability and within-laboratory behavior—not natural
occurrence, field prevalence, production reliability, or universal
false-alarm and latency distributions.

## Repository map

| Path | Purpose |
|---|---|
| `main.tex`, `sections/`, `figs/`, `refs.bib` | Complete manuscript source |
| `code/detector_study.py` | Closed-world detector study over nine scenarios |
| `data/` | Canonical results and programmatically generated table |
| `lab/` | Executed Kubernetes/GitOps lab, scenario scripts, evaluator, and results |
| `scripts/verify_lab_results.py` | Integrity check for frozen live-lab outputs |
| `reviewer2-response.md` | Adversarial review and point-by-point disposition |
| `scripts/verify_artifact.py` | Exact re-execution check |
| `output/pdf/` | Verified compiled manuscript |
| `originais/archive_3/` | Unmodified incoming PDF, source ZIP, and original review memo (local only) |
| `build/latex/` | Local LaTeX intermediates (local only) |
| `qa/renders/` | Page renders and contact sheets used for visual QA (local only) |

## Reproduce the detector study

Python 3.10 or newer is required; the study uses only the standard library.

```bash
python3 code/detector_study.py
python3 scripts/verify_artifact.py
```

The verifier re-executes the study and requires byte-identical canonical CSV
and LaTeX outputs.

## Reproduce or inspect the live laboratory

The `lab/README.md` lists prerequisites and exact commands. The frozen results
can be verified without recreating a cluster:

```bash
python3 scripts/verify_lab_results.py
```

With Docker, Kind, and `kubectl` available, rebuild and execute the live stack:

```bash
lab/bootstrap.sh
python3 lab/run_repeated_experiment.py
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
`.zenodo.json`. Version 1.0.0 remains permanently archived at
<https://doi.org/10.5281/zenodo.21841459>; version 1.1.0 is archived at
<https://doi.org/10.5281/zenodo.21842722>; version 1.2.0 is archived at
<https://doi.org/10.5281/zenodo.21843860>. The concept DOI
<https://doi.org/10.5281/zenodo.21841458> resolves to the latest version.

## License

Code, manuscript source, protocol text, data, and package metadata are released
under the MIT License. Third-party citations remain subject to their original
rights.
