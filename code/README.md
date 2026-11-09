# Code map

Start with the [coauthor handoff](../documentation/COAUTHOR_HANDOFF.md). Public-data acquisition dependencies are declared in the `acquisition` extra of `collection/pyproject.toml`. The checked-in specification records a local acquisition run, not a portable or authorized production configuration.

Use an explicit frozen `REPLICATION_SPEC_PATH`. The current Terra code and its
limited-QA boundary are documented in `../documentation/terra56_collection_validation_protocol.md`.
Code is versioned in Git. New artifacts are written to the selected run in the
sibling artifact directory. The full collector and master remain unrun.

- `01_data_preparation/` acquires/imports frozen inputs and constructs the model-neutral calibration panel. Candidate export is exclusive and the warehouse is read-only during export.
- `02_analysis/` contains all econometric work reported in the paper, including the forecast-only leave-one-meeting-out and expanding-window validation in `run_referee_d8_oos_validation.R`. These programs are R scripts and use standard packages such as `fixest`, `ivreg`, `sandwich`, `clusterSEs`, `boot`, `glmnet`, `rstan`, `stm`, and `quanteda`.
- `03_reporting/` converts machine-readable estimates to the submitted LaTeX tables and PDF figures.
- `04_verification/` checks exact selected cohorts, frozen69 calculations, plotting metadata, accepted numerical baselines, and exhibit completeness. Missing or unaccepted Terra references are NOT RUN.
- `collection/` documents the LLM collection layer and is not called by the exact analysis replication.

Source lookup uses `source_root`. Artifact writes and cleanup use `run_root`.
Shared immutable inputs have explicit paths and hashes. The effective specification
and source snapshot record the selected machine paths and code identity.
