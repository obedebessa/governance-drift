# Compound-class extension and extended benign controls

This directory contains five injected episodes each of S10--S12, observed at
0.5-, 2-, and 10-second cadences, plus six separate 300-second benign windows.
All 45/45 correlated observations eventually reached the exact provisional
class set; 35/45 were complete at the first honest alert. For S10, policy
emerged after authorization in ten observations, so only 5/15 first alerts
were complete. Its median DDL, exact-set completion latency (ESC), and
diagnostic gap were 1.544, 7.615, and 5.984 seconds. S11 and S12 were complete
at first alert in 15/15 observations each. The six benign windows retained
4,680 polls over 1,800 seconds with zero substantive alarms and zero epistemic
warnings.

- `repeated_observations.json` / `.csv`: cadence-specific compound records.
- `repeated_summary.json`: per-scenario repeated results.
- `extension_summary.json`: validated DDL/ESC/diagnostic-gap and soak summary.
- `control_observations.csv`: six extended benign-window records.
- `table_compound.tex`, `table_repeated.tex`, `table_cadence.tex`,
  `table_class_set_metrics.tex`, and `table_controls.tex`: generated tables.

Repeated per-cadence views are correlated measurements of five injected
episodes per scenario, not 45 independent experiments. ESC is first exact-set
completion under the sequential-snapshot laboratory assumption, not
watermark-qualified Stable-VCL.
