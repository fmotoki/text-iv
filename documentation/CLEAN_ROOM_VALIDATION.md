# Clean-room validation

**Package version:** 2.0.0  
**Record date:** 2026-08-16  
**Status:** PASS -- completed GPT-5.5-only clean-room run

## Scope

This record documents an isolated execution of version 2.0.0. The source
package was copied to a new temporary directory outside the parent research
workspace. Writable work products, prior step markers, and prior generated
results were removed before execution; the run began from released code,
frozen inputs, reference exhibits, public documentation, and `renv.lock`.

Validation covers exact reproduction from the frozen GPT-5.5 data. It does not
invoke remote collection. The master reconstructs the 71-meeting calibration
input from the complete 142-forecast original-minute GPT-5.5 panel and then
runs the same-model-vintage counterfactual analysis.

## Environment and commands

- Operating system: Windows 11, 64 bit
- R: 4.5.2
- Dependency specification: `renv.lock`
- Execution policy: sequential stages, `MAKEFLAGS=-j1`,
  `STAN_NUM_THREADS=1`
- Setup command: `Rscript code/00_setup.R`
- Replication command: `Rscript code/00_master.R`

The accepted run reused a previously restored library matching `renv.lock`;
no parent-workspace file was used as an analysis input. A first machine without
a compatible cache should reserve approximately 90 minutes for restore and
execution.

## Acceptance results

| Gate | Result |
|---|---:|
| Frozen input checksums | 76 of 76 passed |
| Master stages | 15 of 15 completed |
| Artifact and numerical checks | 87 of 87 passed |
| Active LaTeX tables | 29 of 29 reproduced |
| Active PDF figures | 10 of 10 verified |
| Headline machine-readable result files | 3 of 3 reproduced |
| Full 15-stage master runtime | 11.9 minutes |

The three headline files are `results/verification_report.csv`,
`results/intext_results.csv`, and `results/intext_numbers.tex`. Table
acceptance uses exact normalized-text comparison with `reference/`. PDF bytes
may differ because graphics devices write creation metadata, so figure
acceptance verifies the active artifact path and nonzero size together with the
underlying numerical gates.

## Acceptance rule

The package is accepted only when all 76 rows of
`results/qa/input_verification.csv` and all 87 rows of
`results/verification_report.csv` have `status = pass`, all 15 stage markers
are present, and the active exhibit counts match
`data/metadata/exhibit_manifest.csv`.

Version 2.0.0 satisfies this rule. The public release contains no dependency on
the parent workspace, no provider credential, and no superseded calibration
input.
