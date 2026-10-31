# Replication package

## Can Words Move Inflation Expectations? A Counterfactual-Text Laboratory

**Authors:** Fernando Barros Jr., Fabio Y. S. Motoki, Valdemar Pinho Neto, and Victor Rangel  
**Package version:** 2.0.0  
**Prepared:** 2026-08-16  
**Repository identifier:** assigned by the repository at deposit  
**Contact:** victorrsr@al.insper.edu.br

This is the isolated replication package for the current paper. The complete
reported specification uses GPT-5.5 for the original-minute calibration paths,
counterfactual rewrites, and synthetic-forecaster experiments. It does not read
from the parent research workspace, require a provider credential, or invoke a
remote model during exact replication.

The package follows the AEA Data and Code Availability guidance: a single
master script runs the reported analysis; every frozen input has a SHA-256
checksum; every active exhibit is mapped to its producing program; public and
author-generated data are documented separately; R dependencies are locked;
and automatic gates check the paper's main numerical claims.

## One-command reproduction

Start in the package root, the directory containing this README.

```powershell
Rscript code/00_setup.R
Rscript code/00_master.R
```

`00_setup.R` restores the versions in `renv.lock`, tests the installed `rstan`
stack, and recompiles the locked Stan stack only if the compatibility test
fails. `00_master.R` verifies the frozen inputs, creates an expendable working
copy of the SQLite database, constructs the GPT-5.5 calibration dataset, runs
all analysis and reporting stages, and applies the final verification gates.
It never alters `data/generated/prompt_iv_replication_source.sqlite`.

After an interrupted run, resume completed stages with:

```powershell
Rscript code/00_master.R --resume=true
```

A successful run leaves `status = pass` in all 86 rows of
`results/verification_report.csv`. Step logs and elapsed times are written
under `logs/`.

## Scope of reproducibility

The default reproduction begins from frozen generated texts, blinded scores,
embeddings, and synthetic-forecaster responses. This is the correct starting
point for exact replication because a future remote-model response is not
guaranteed to be byte-identical after provider updates.

The package has two reproducibility layers:

1. **Exact analysis reproduction.** `code/00_master.R` produces all 28 active
   LaTeX tables and 10 active PDF figures in
   `data/metadata/exhibit_manifest.csv`, plus their machine-readable inputs.
2. **Collection transparency.** `code/collection/` contains the versioned
   prompts, task constructors, quality gates, database schema, and runner code.
   Collection is not called by the master; its example configuration is a
   dry-run and needs explicit opt-in before any remote request.

The package does not claim byte-exact recollection from a future model
endpoint. The published results are exactly reproducible from the distributed
frozen data.

### Priors, arms, and regenerations

Use meeting index $t$, forecaster profile $i\in\{A,U\}$, textual arm $j$, and
regeneration index $s$. For each meeting and profile, the original-minute run
constructs a profile-specific pre-treatment prior $P_t^i$ along the
chronological path of published, unmodified minutes. Meeting 203 begins at
3.8 percent; thereafter the prior for profile $i$ is that profile's forecast
after the preceding original minute.

Before any counterfactual at meeting $t$ is evaluated, $P_t^i$ is frozen. The
same value is supplied to every arm $j$ and every regeneration $s$ in the
meeting-profile cell. The experimental revision is

$$
\Delta E_{tjs}^{i}=E_{tjs}^{i}-P_t^i.
$$

“Common prior” therefore means common across arms and regenerations within a
meeting and profile. It does not mean $P_t^A=P_t^U$. Nor does a shared value of
$s$ connect two meetings: regeneration 1 at meeting $t$ is not compared with
regeneration 1 at meeting $t-1$. The two profile revisions are combined with
estimated predictive calibration coefficients, not averaged. Intercept and
meeting controls are fixed within counterfactual comparisons and cancel.

The separately labelled arm-specific recursive exercise answers a different
question. In that exercise, each arm-regeneration-profile combination is a
trajectory, and its forecast at meeting $t-1$ is supplied as its prior at
meeting $t$. The analysis scripts verify both the common-prior invariant and
the recursive transitions before estimation. See the two collection protocols
under `documentation/`.

### GPT-5.5 calibration

At run time, `code/01_data_preparation/build_gpt55_calibration_data.R`
constructs `outputs/work/gpt55_calibration_data.dta`. It reads the complete
142-observation original-minute panel (71 meetings by two profiles) from run
`common_prior_original_v1_20260712`, requires model `gpt-5.5`, reasoning effort
`high`, completed status, and the expected prompt/version fields, and verifies
the forecast-revision identity and profile-specific chronological continuity.

Those original-minute forecast levels are joined to the public aggregate Focus
twelve-month-ahead IPCA median, original-minute tone and word count, and the
frozen meeting-level activity controls. The calibration coefficients are then
estimated once on the 71 meetings and held fixed in the text experiment. Thus
calibration and experimental outcomes use the same model vintage and profile
definitions while remaining separate statistical stages.

## Computational requirements

The validated environment is Windows 11 (64 bit), R 4.5.2, 32 GB RAM, a C++
toolchain compatible with R 4.5 and `rstan`, a Cairo-capable graphics device,
and at least 5 GB free disk space. No GPU is required. Internet access is needed
only for the first dependency restore.

The scripts request single-threaded build, Stan, data-table, and text-analysis
behavior where supported to bound memory use. Reserve about 90 minutes for a
first setup without a compatible package cache; later analysis-only runs are
substantially faster. Exact accepted timings are recorded in
`logs/master_run_times.csv` and summarized in
`documentation/CLEAN_ROOM_VALIDATION.md`.

**Last clean-room validation (2026-08-16): PASS.** The accepted run passed all
76 frozen-input checks and completed all 15 master stages. The current manifest
contains 28 tables, 10 figures, and 86 artifact and numerical gates. Run the
clean-room validation again before deposit.

## Directory map

| Path | Role |
|---|---|
| `code/00_setup.R` | Restores the locked R environment. |
| `code/00_master.R` | Runs the complete exact replication. |
| `code/01_data_preparation/` | Constructs the run-time GPT-5.5 calibration input and package metadata. |
| `code/02_analysis/` | Reported econometric and validation analysis. |
| `code/03_reporting/` | Publication-table and figure construction. |
| `code/04_verification/` | Input, result, and package-manifest checks. |
| `code/collection/` | Auditable LLM collection layer; excluded from the master. |
| `data/raw/` | Public Copom minutes and frozen non-LLM calibration controls. |
| `data/analysis/` | Meeting/cell-level analysis inputs. |
| `data/generated/` | Immutable SQLite database of frozen generated data. |
| `data/metadata/` | Provenance, dictionaries, checksums, inventories, and exhibit map. |
| `outputs/` | Machine-readable intermediate estimates and temporary work files. |
| `results/` | Reproduced exhibits, numerical macros, and QA reports. |
| `reference/` | Frozen author-side copies of the current exhibits. |
| `documentation/` | Public protocols, data statement, and validation records. |
| `logs/` | Per-step logs, timings, and session information. |

## Primary analysis inputs

- `data/generated/prompt_iv_replication_source.sqlite` (about 200 MB): frozen
  generated texts, 15,762 completed selected synthetic-forecaster results,
  scores, embeddings, public external-series extracts, run metadata, and audit
  inventories.
- `data/raw/calibration_controls/copom_meeting_activity_controls.csv`
  (71 meetings by five fields): meeting/date keys and frozen agriculture,
  industry, and services activity-index levels used only as calibration
  controls.
- `data/analysis/fiscal_sample_flags_by_meeting_20260630.csv` (71 meetings by
  three fields): RTN-window and CDS-series-availability flags; no CDS level is
  redistributed.
- `data/analysis/realized_dose_dataset.csv` (2,130 text cells by 29 fields):
  lexicon, blinded-semantic, and pairwise dose measures.
- `data/raw/copom_minutes/` (71 files): public minutes for meetings 203--273.

The calibration `.dta` is a generated work file, not a distributed input.
Detailed field definitions are in the metadata dictionaries. The database
table `referee_human_audit_ratings` has zero rows by design: a blinded packet
was prepared, but no human ratings were collected, and no human-audit estimate
is imputed.

## Data provenance and access

Only public data and author-generated synthetic data are used; there is no
confidential institution-level Focus microdata. Source URLs, retrieval fields,
coverage, redistribution status, citations, and included-file mappings are in
`data/metadata/data_sources.csv` and
`data/metadata/external_source_manifest.csv`.

Distributed third-party data come from Banco Central do Brasil Copom minutes,
Focus/Olinda aggregate and Top-5 statistics, BCB SGS series, and Tesouro
Nacional RTN publication dates. Historical World Government Bonds CDS values
and the Ipeadata/J.P. Morgan EMBI series are not redistributed because clear
redistribution permission was not identified. The active fiscal table uses
only a binary CDS-coverage flag; EMBI values are not read by the master.

The three activity-control columns were extracted without transformation from
an author-maintained workbook. The retained workbook metadata identifies
BCB/Depec and monthly index levels but does not preserve SGS identifiers,
seasonal-adjustment flags, or the original download date. The public package
therefore freezes and checksums the exact 71-row extract and discloses this
provenance limit instead of asserting an unrecoverable series vintage. Details
and hashes are in `data/raw/calibration_controls/README.md`.

The main frozen GPT-5.5 run identifiers are:

| Purpose | Run ID | Tasks/results |
|---|---|---:|
| 25-regeneration rewrite corpus | `v10_referee_d5_25draws_20260620` | 17,750 rewrites |
| Original path and calibration forecasts | `common_prior_original_v1_20260712` | 142 forecasts |
| Common-prior binary Prompt-IV | `common_prior_main_25draws_v1_20260712` | 7,100 forecasts |
| Common-prior six-arm dose response | `common_prior_dose_6arm_5draws_v1_20260713` | 4,260 forecasts |
| Arm-specific recursive paths | `arm_specific_prior_dose_6arm_5draws_v1_20260714` | 4,260 forecasts |

## Analysis workflow

The 15 master stages are:

1. verify the 76 frozen inputs;
2. construct and validate the GPT-5.5 calibration dataset;
3. estimate the common-prior Prompt-IV and dose/holdout/fiscal checks;
4. run forecast-only out-of-sample validation;
5. estimate arm-specific recursive paths;
6. run identification, placebo, regime, and wild-cluster checks;
7. estimate Bayesian and empirical-Bayes calibration alternatives;
8. run external, Top-5, dispersion, moment, and orthogonality checks;
9. run the sunspot/perceived-law diagnostic;
10. estimate the proper-prior posterior model tournaments;
11. evaluate structural-topic preservation;
12. evaluate embeddings and alternative tone instruments;
13. build integrated tables and figures;
14. build baseline figures; and
15. verify all results and exhibits.

The exact exhibit-to-program map is
`data/metadata/exhibit_manifest.csv`. Intermediate estimates remain under
`outputs/tables/`, so each publication entry can be traced to a
machine-readable result.

## Randomness and verification

All local simulation, bootstrap, topic-model, and Bayesian steps set explicit
seeds. The main seed is `20260715`; the common-prior bootstrap seed is
`20260714`. Deterministic targets use tight tolerances; stochastic posterior
summaries use documented MCMC tolerances.

The final gate verifies the headline Prompt-IV estimate and standard error,
reduced form and first stage, regeneration-specific signs, ordered dose means,
strict held-out estimate, calibration and forecast diagnostics, placebos,
wild-cluster inference, regime/sunspot/embedding results, exact normalized text
for all 28 tables, and existence/nonzero size for all 10 figures. It also writes
`results/intext_results.csv` and `results/intext_numbers.tex` as the prose-value
source of truth.

## Collection-code boundary and security

Exact reproduction needs neither Python nor provider access after the R
environment is restored. Collection code is retained only to audit the frozen
inputs. Remote calls cost money, can change over time, and are never made by
the master. No credential is stored in the package; the example configuration
uses `runner = "dry-run"`, `allow_real_llm = false`, and one worker. Any new
collection must receive a new run ID and must not overwrite the frozen database.

## Licenses and deposit checklist

Code is BSD 3-Clause (`LICENSE`). Author-generated data are CC BY 4.0
(`LICENSE_DATA`). Third-party documents/data remain subject to provider rights
and are not relicensed by the authors.

Before deposit, the corresponding author should upload this directory rather
than its parent workspace, rerun the master in the uploaded copy, confirm all
verification rows pass, add the assigned repository identifier and final
article citation, and retain the top-level README files, licenses, lockfile,
and checksum manifests. For GitHub, the SQLite database must use Git LFS as
specified in `.gitattributes`; the repository mirror is not a substitute for a
durable archive DOI.
