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
