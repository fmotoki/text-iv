# Intermediate outputs

`tables/` contains machine-readable estimates retained for traceability from
reported LaTeX entries back to R output.

`code/00_master.R` creates `models/` and `work/` during execution. Their contents
are temporary and are not distributed: model objects are regenerated from the
frozen inputs, and the writable SQLite database is copied from the immutable
source under `data/generated/`.
