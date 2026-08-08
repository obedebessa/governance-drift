# Primary repeated Kind/Flux/Kyverno campaign

This directory contains the canonical S1--S9 repeated campaign and its
steady-state benign controls. The reportable positive dataset is
`repeated_observations.*`: 20 injected episodes per scenario observed at 0.5-,
2-, and 10-second cadences, for 540 correlated cadence observations. The
evaluator detected 538/540; all 538 detected class sets were exact, and the
unconditional six-label Hamming loss was 0.000617. Median DDL by cadence was
0.48, 1.45, and 6.25 seconds. The 60 control observations represent 20 benign
windows viewed at three cadences and contain 543 evaluator polls with no
substantive or epistemic alarm.

The reportable files are:

- `repeated_observations.json` / `.csv`: canonical repeated positive records.
- `repeated_summary.json`: cadence, scenario, class-set, and control summaries.
- `control_observations.csv`: the 60 cadence-specific benign-window records.
- `uncertainty_summary.json`: Wilson and injection-cluster bootstrap results.
- `table_repeated.tex`, `table_cadence.tex`,
  `table_class_set_metrics.tex`, and `table_controls.tex`: generated manuscript
  tables.
- `platforms.json`: retained platform snapshots.

`observations.json`, `observations.csv`, and `table_lab.tex` are preserved only
as the original one-pass v1.1 campaign view. They predate full class-set
scoring and include legacy S6/latency labels; they are not a source for the
v1.6 numerical claims. Use the `repeated_*` files and generated repeated tables
for all current analyses.
