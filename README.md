# Governance Drift

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)

Reproducibility package for the manuscript:

> **Governance Drift: Measuring Divergence Between Approved Intent and
> Operational Reality in Cloud-Native Systems**  
> Obede Bessa Rocha da Silva — Independent Researcher

The package contains the complete LaTeX source, a dependency-free closed-world
detector study, canonical outputs, and a Kubernetes/GitOps laboratory protocol.
Version 1.0.0 incorporates the full simulated Reviewer #2 revision.

## Evidence boundary

The executed study validates detector semantics and evidence-tier dependencies
inside a modeled world. It is not field evidence for the taxonomy. The `lab/`
directory specifies a real-infrastructure protocol but no cluster measurements
are reported or claimed.

## Repository map

| Path | Purpose |
|---|---|
| `main.tex`, `sections/`, `figs/`, `refs.bib` | Complete manuscript source |
| `code/detector_study.py` | Closed-world detector study over nine scenarios |
| `data/` | Canonical results and programmatically generated table |
| `lab/` | Kubernetes/GitOps field-execution protocol and scenario scripts |
| `reviewer2-response.md` | Adversarial review and point-by-point disposition |
| `scripts/verify_artifact.py` | Exact re-execution check |
| `output/pdf/` | Verified compiled manuscript |

## Reproduce the detector study

Python 3.10 or newer is required; the study uses only the standard library.

```bash
python3 code/detector_study.py
python3 scripts/verify_artifact.py
```

The verifier re-executes the study and requires byte-identical canonical CSV
and LaTeX outputs.

## Laboratory protocol

The `lab/README.md` describes the additional tools needed for a Kind/Kubernetes
execution. The laboratory remains an unexecuted protocol in this release; do
not interpret its presence as a reported experiment.

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
`.zenodo.json`. The DOI field will be added after the first Zenodo release is
published.

## License

Code, manuscript source, protocol text, data, and package metadata are released
under the MIT License. Third-party citations remain subject to their original
rights.
