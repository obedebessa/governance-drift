# Executed campaign source snapshot

The three Python files in this directory are immutable, byte-exact snapshots
of the runner, contract tests, and analyzer captured for the executed
cross-stack campaign. Their SHA-256 values are retained under
`campaign-source` in `../manifest_checksums.csv`.

Those snapshots used the legacy field name `ddl_seconds` for the interval from
the operational-onset marker to the first honest non-consistent or undecidable
evaluation. That interval is not DDL under the manuscript's class-specific
onset definition. The publication analyzer therefore renames the derived field
to `operational_onset_to_first_honest_seconds` without changing any timestamp
or numerical value. The current repository runner and tests emit the corrected
name for future campaigns. The current analyzer is independently hashed under
`derived-output-source` in the checksum inventory.
