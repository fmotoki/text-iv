# Data map

`raw/` contains the 71 public Copom minutes and the frozen non-LLM activity-control extract. `analysis/` contains the two rectangular files read directly by the R analysis. `generated/` contains the immutable compact SQLite database with the frozen GPT-5.5 panels. `metadata/` contains provenance, dictionaries, run inventories, checksums, and the exhibit crosswalk. The 71-meeting GPT-5.5 calibration file is generated at run time under `outputs/work/`; it is not a frozen input.

Do not edit frozen inputs in place. `code/00_master.R` copies the SQLite source to `outputs/work/` before any analysis script writes derived tables. `code/04_verification/verify_inputs.R` rejects a changed or incomplete input before estimation begins.

See `metadata/data_sources.csv`, `metadata/analysis_data_dictionary.csv`, and `metadata/database_data_dictionary.csv` for source and variable definitions.
