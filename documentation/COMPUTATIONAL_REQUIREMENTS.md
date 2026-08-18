# Computational Requirements

**Package version:** 2.0.0  
**Prepared:** 2026-08-16  
**Validation status:** PASS -- GPT-5.5-only clean-room run

## Tested environment

- Windows 11, 64 bit
- R 4.5.2
- 32 GB RAM
- CPU-only execution; no GPU required
- C++ compiler compatible with R 4.5 and `rstan`
- Cairo-capable graphics device

Package versions are locked in `renv.lock`; the accepted session is written to
`logs/master_sessionInfo.txt`. `code/00_setup.R` tests the installed `rstan`
stack and recompiles the locked Stan stack only if the compatibility test
fails.

## Resource policy

The master sets `MAKEFLAGS=-j1` and `STAN_NUM_THREADS=1`. Analysis scripts also
limit data-table and text-analysis threads where supported. This favors bounded
memory use and stable reproduction over maximum throughput.

Allow at least 5 GB free disk space. The 200 MB immutable database is copied to
`outputs/work/`; Stan objects, tables, figures, and QA files add temporary
storage. Reserve about 90 minutes for first setup without a compatible binary
cache. The accepted analysis-only clean-room master took 11.9 minutes. Accepted
step times are recorded in `logs/master_run_times.csv` and the clean-room
record.

## Network and provider access

Network access is needed only to install missing R packages during the first
`renv` restore. The analysis master calls neither external data services nor an
LLM endpoint. Once dependencies are installed, the 15-stage master runs
offline. It reconstructs the calibration input from the frozen GPT-5.5
original-minute panel before estimation.

## Expected platform differences

PDF container metadata and font embedding can differ by graphics device, and
the last digits of stochastic posterior summaries can differ across compatible
platforms. The verification script therefore uses documented MCMC tolerances
for posterior quantities, tight tolerances for deterministic estimates, exact
normalized-text checks for all active tables, and existence/nonzero-size checks
for the active figures. A failed verification row must be investigated rather
than overwritten.
