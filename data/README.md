# Data map

Raw inputs stay under `data/raw/`. `raw/copom_minutes/` contains the historical minute-text collection and `_dates.csv`; the current design selects exactly meetings 203 through 281. The presence of additional historical texts does not expand that sample.

`raw/bcb/20261109_rebuild/` is the corrected snapshot, rebuilt on November 9, 2026 from the retained BCB responses. It contains the same 10 SGS series and three Focus series, corrected publication-date links, the BCB revisions workbook and request checksums. All ten SGS definitions are verified. The meeting coverage check passed for all 79 meetings, from 203 through 281; three same-day releases are flagged separately. This snapshot is excluded from Git and must be transferred separately. Original-publication values have not yet been constructed. The earlier `20261108_267de531` snapshot is retained locally as the source of the rebuild and is not the current handoff dataset.

`analysis/`, `generated/`, `raw/calibration_controls/` and the existing `metadata/` tables preserve the earlier GPT-5.5 release. Their 71-meeting dictionaries, source database and exhibit mapping do not describe new Terra results. The existing `data_sources.csv`, `analysis_data_dictionary.csv` and `database_data_dictionary.csv` remain useful historical records.

New derived Terra data and results belong to the selected run under the sibling `replication_artifacts/runs/terra56/` directory. `code/00_master.R` validates the selected frozen database and copies it into that run's `outputs/work/` before analysis. It does not authorize overwriting the older package-level results.

Do not edit frozen inputs in place. See [the coauthor handoff](../documentation/COAUTHOR_HANDOFF.md) for data transfer, current file schemas, remaining evidence checks and the eventual deposit layout.
