# Data Availability Statement

## Current source status

The [Terra implementation](terra56_collection_validation_protocol.md) uses
meetings 203 through 281. The current BCB snapshot was rebuilt on November 9, 2026
at `data/raw/bcb/20261109_rebuild/`. That directory is excluded from Git and
must accompany a data transfer. It contains 7,715 SGS observations, 49,380
Focus observations, release evidence and the historical-revisions workbook.
See the [coauthor handoff](COAUTHOR_HANDOFF.md) for the file list, provenance,
checksums and remaining limitations.

Production Terra outcomes have not been collected or accepted. New derived
artifacts remain isolated in the sibling run directory. All ten SGS definitions
and publication-date coverage for all 79 meetings passed verification. Original-release
values and meeting cutoff evidence still require completion. Sector public availability before April 2025
requires an author decision. Existing minute texts and GPT-5.5 results remain
unchanged. The historical claims below do not certify the new data or code.

## Inherited GPT-5.5 release

The following statement describes the preserved version 2.0.0 release. Its input
and clean-room claims do not apply to the prospective Terra results.

All data and code required to reproduce the reported tables, figures, and
numerical results are included in this package. The exact replication starts
from public source data and frozen author-generated outputs; it requires no
credential, paid API call, or access to the parent research workspace.

The package contains 71 public Copom minutes, public aggregate Focus and
macroeconomic extracts, meeting/cell-level analysis files, and a compact SQLite
database containing the frozen GPT-5.5 counterfactual-text and
synthetic-forecaster experiments. The database retains prompt versions, model
and reasoning settings, source-text hashes, run identifiers, task/result audit
fields, blinded scores, and locally computed embeddings. Collection code and
prompts are supplied in `code/collection/`, but remote collection is not part
of the exact-reproduction master.

The calibration is also GPT-5.5-only. During replication, the master constructs
the 71-meeting calibration input from the 142 completed original-minute
forecasts in `common_prior_original_v1_20260712`, public aggregate Focus
twelve-month-ahead IPCA medians, original-minute tone/length, and the frozen
activity-control extract. The generated calibration file is written under
`outputs/work/` and is not a separate distributed input.

The redistributed public inputs come from Banco Central do Brasil and Tesouro
Nacional. Source URLs, recorded access fields, coverage, citations,
redistribution status, and included-file mappings are in
`data/metadata/data_sources.csv` and
`data/metadata/external_source_manifest.csv`. The 71-row activity-control
extract is attributed to BCB/Depec. Its source workbook identifies monthly
agriculture, industry, and services index levels, but does not preserve the
exact SGS identifiers, seasonal-adjustment flags, or original download date.
The package therefore distributes the exact immutable extract with its SHA-256
hash and discloses this source-vintage limitation in
`data/raw/calibration_controls/README.md`.

Historical World Government Bonds CDS levels and the Ipeadata/J.P. Morgan EMBI
series are not redistributed because sufficiently clear redistribution
permission was not identified. The fiscal sensitivity analysis uses only an
author-generated binary flag for whether the CDS page covered a meeting date;
no CDS levels are included. EMBI values are not read by the master. Access
information for the omitted sources remains in the source register.

No confidential institution-level Focus microdata are used. The
institution-level BCB endpoint was unavailable because of confidentiality
restrictions; only public aggregate and Top-5 endpoints enter the analysis. A
database table reserved for blinded human ratings has zero rows because no
human ratings were collected; the paper reports no completed human-audit
estimate.

Code is licensed under BSD 3-Clause. Author-generated data are licensed under
CC BY 4.0. Third-party data and documents remain subject to their providers'
rights and are not relicensed by the authors. The repository DOI and final
article citation will be added when assigned.
