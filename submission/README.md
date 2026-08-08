# Submission builds

`main-anonymous.tex` produces an identity- and repository-redacted review
build while retaining the scientific content. Compile it from the repository
root so existing relative inputs resolve:

```bash
pdflatex -jobname=governance-drift-anonymous submission/main-anonymous.tex
bibtex governance-drift-anonymous
pdflatex -jobname=governance-drift-anonymous submission/main-anonymous.tex
pdflatex -jobname=governance-drift-anonymous submission/main-anonymous.tex
```

This is a venue-neutral anonymous technical-report layout. Before submission,
replace only the outer class/formatting layer with the selected venue's
official template, honor its page and artifact rules, and retain the
identified `main.tex` build as the archival version. Do not submit the custom
layout where a venue mandates `IEEEtran`, `acmart`, `llncs`, or another class.

## Sanitized double-blind artifact

`scripts/build_anonymous_artifact.py` builds a deterministic, text-only review
ZIP from an explicit source/data allowlist. It excludes `.git`, generated PDFs,
raw release packages, local runtime/cache trees, pilots, transient logs and
build by-products, secrets, bytecode, and non-auditable binary containers.
Identity, capture-host paths and hostname, project archive identifiers, and the
identified public repository are replaced in UTF-8 text. The builder
regenerates the trace campaign's transport manifest, adds a top-level canonical
manifest, and verifies every entry, hash, mode, timestamp, path, and
forbidden-token rule before admitting the ZIP.

While results are changing, test only outside the repository:

```bash
artifact_preview="$(mktemp -d)"
python3 scripts/build_anonymous_artifact.py \
  --output "$artifact_preview/governance-drift-anonymous-artifact.zip"
python3 scripts/build_anonymous_artifact.py \
  --verify-only "$artifact_preview/governance-drift-anonymous-artifact.zip"
```

After results are frozen and the Git tree is clean, create the venue artifact
explicitly:

```bash
python3 scripts/build_anonymous_artifact.py --final \
  --output submission/governance-drift-anonymous-artifact.zip
```

Writing under `submission/` or `release/` requires `--final`; a final build
refuses a dirty tree. Existing outputs are not overwritten unless `--force` is
also supplied. The identified artifact remains the source of record because
sanitization necessarily changes byte-level hashes.
