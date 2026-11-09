# Data map

Raw inputs stay under `data/raw/`. `raw/copom_minutes/` contains the historical minute-text collection and `_dates.csv`; the current design selects exactly meetings 203 through 281. The presence of additional historical texts does not expand that sample.

`raw/bcb/20261108_267de531/` contains the November 8, 2026 public-data download. `sgs.csv` contains 10 economic series, `focus.csv` contains three expectations series, and the remaining files retain release evidence, the BCB revisions workbook and request checksums. This snapshot is excluded from Git and must be transferred separately. Its source manifest records incomplete catalogue metadata. Original-publication values have not yet been constructed, and the snapshot is not approved for analysis.

`analysis/`, `generated/`, `raw/calibration_controls/` and the existing `metadata/` tables preserve the earlier GPT-5.5 release. Their 71-meeting dictionaries, source database and exhibit mapping do not describe new Terra results. The existing `data_sources.csv`, `analysis_data_dictionary.csv` and `database_data_dictionary.csv` remain useful historical records.

New derived Terra data and results belong to the selected run under the sibling `replication_artifacts/runs/terra56/` directory. `code/00_master.R` validates the selected frozen database and copies it into that run's `outputs/work/` before analysis. It does not authorize overwriting the older package-level results.

Do not edit frozen inputs in place. See [the coauthor handoff](../documentation/COAUTHOR_HANDOFF.md) for data transfer, current file schemas, remaining evidence checks and the eventual deposit layout.
