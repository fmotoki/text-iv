# Code map

Run `00_setup.R` once and `00_master.R` for the complete replication. The numbered folders follow execution order.

- `01_data_preparation/` constructs the run-time GPT-5.5 calibration panel and contains author-side packaging utilities. The frozen SQLite database itself is immutable.
- `02_analysis/` contains all econometric work reported in the paper, including the forecast-only leave-one-meeting-out and expanding-window validation in `run_referee_d8_oos_validation.R`. These programs are R scripts and use standard packages such as `fixest`, `ivreg`, `sandwich`, `clusterSEs`, `boot`, `glmnet`, `rstan`, `stm`, and `quanteda`.
- `03_reporting/` converts machine-readable estimates to the submitted LaTeX tables and PDF figures.
- `04_verification/` verifies frozen inputs, headline numerical results, exact normalized-text matches for active tables, and exhibit completeness.
- `collection/` documents the LLM collection layer and is not called by the exact analysis replication.

Every script resolves paths from the replication root. No script requires the original parent project or a user-specific path.
