# Coauthor handoff

Updated November 9, 2026. Read paths below relative to the `replication/` repository root unless a sibling directory is explicitly named.

## Start here

> **The BCB workbook of historical IBC-Br releases is already downloaded and included in the data ZIP.**
>
> File: `data/raw/bcb/20261109_rebuild/responses/IBC-Br_todas_publicacoes.xlsx`.
>
> **The next step is to extract the values recorded for each release and check coverage for meetings 203 onward.** Downloading the same SGS series again will not recover earlier versions of the numbers. Use older official releases only where the workbook does not cover the required dates. See item 4 below.

The prospective Terra implementation and public BCB download are available. Full Terra collection, statistical estimation and a new paper-results release have not been completed. This is a takeover document, not an AEA deposit certification.

The repository is `https://github.com/fmotoki/text-iv`, on branch `codex/terra56-20261101T225332Z`. Use `git rev-parse HEAD` to identify the version containing this handoff. The older `main` branch and the version 2.0.0 citation describe inherited material.

First obtain the code branch and the separate BCB snapshot described below. Then resolve the activity-information policy with the authors before starting expensive collection. Passing synthetic tests does not resolve that research decision.

The paper-facing design uses meetings 203 through 281, inclusive, with 79 meetings, 25 main draws, five dose draws, five rewrite blocks, and anchored/unanchored profiles. The requested model is `gpt-5.6-terra` with high reasoning and Standard service. Common-prior responses and arm-specific recursive paths remain distinct designs.

The required data coverage starts at meeting 203, held on November 30, 2016. Earlier meetings are outside the requested sample. Check that every meeting from 203 through 281 has the required data and that each activity figure had been published by that meeting. Earlier monthly observations are needed only where they supply the latest available figure or a required lag or growth calculation for an included meeting. There is no requirement to repair the entire historical release calendar.

The new cutoff diagnostic fits an unrestricted calibration on meetings 203–271 only. It compares five meetings before the February 16, 2026 model cutoff with five afterward, using same-date Focus twelve-month IPCA expectations. Its benchmark is previous-meeting Focus. It measures descriptive agreement, not realized inflation forecast accuracy or a causal effect on human beliefs. The main calibration still uses the separate full-79 sample.

## What is complete

| Item | Evidence and limit |
| --- | --- |
| Collection, calibration, cutoff and reporting source | Implemented, with bounded fixture checks. Full production integration remains unrun. |
| Original minute texts | The repository contains the historical text collection. Select exactly 203–281 through `_dates.csv`, rather than taking the first 79 filenames. |
| New public BCB snapshot | Download completed with exit 0. Ten SGS series, three Focus entities, release evidence and the revisions workbook are saved. |
| Download verification | The corrected snapshot passed 341 original response hashes and five normalized-file hashes. SGS definitions are verified for all ten series. IBC-Br publication-date coverage and selected Focus targets cover every meeting from 203 through 281. See [the coverage check](qa/bcb_rebuild_20261109/bcb_validation.json) and [meeting-level dates and lags](qa/bcb_rebuild_20261109/meeting_bcb_coverage.csv). |
| Frozen-cutoff fixture | Five cases passed, including held-out-target perturbation, zero benchmark error, missing neutral and changed source hash. These were synthetic checks. |
| Limited live model checks | The earlier implementation handoff reports four request families and five paid attempts. The planned 92-request smoke and full trajectories were not completed. |
| Existing results | GPT-5.5 source data, estimates, reference exhibits and the earlier recovery archive were preserved. They are not new Terra results. |

The final offline suite was rerun on November 9: 34 tests passed in 79.403 seconds, with zero skips and no model calls. The [current verification receipt](qa/handoff_verification_20261109.json) records the source hashes, data checks and scope limits. The November 8 receipt is retained as historical evidence.

The full public-data run and follow-up checks exposed faults not reached by the initial smoke checks: metadata were missing from the catalogue, some note IDs were JSON floats, some titles had surrounding whitespace, and older titles omitted a reference year that was stated in the notice body. These are corrected. The downloader retains and checks the official SGS table when the catalogue lacks a definition, normalizes integral note IDs, strips title whitespace and reads explicit reference years from the linked notices. The rebuilt files passed the real-data coverage check as well as the offline fixtures.

## Code and data organization

| Location | Role | Transfer and handling |
| --- | --- | --- |
| `code/` | Native Python, R and PowerShell source, prompts and tests | Versioned in Git. |
| `data/raw/copom_minutes/` | Existing texts and the canonical meeting-date map | Texts and date map are included in the code handoff. Preserve their bytes. |
| `data/raw/bcb/20261109_rebuild/` | Corrected public BCB inputs | Excluded from Git. Transfer the whole directory, including `responses/` and `source_manifest.json`. The earlier snapshot is retained locally as evidence and need not accompany the handoff. |
| `data/raw/calibration_controls/`, `data/analysis/`, `data/generated/` | Inherited controls, analysis files and GPT-5.5 SQLite source | Historical inputs. Do not relabel them as Terra inputs. |
| `data/metadata/` | Earlier dictionaries, source inventory and exhibit mapping | These describe the inherited release and require a new version for Terra. |
| `outputs/`, `results/`, `reference/`, `logs/` | Earlier outputs and reproduction evidence | Preserve as historical records. New production writers use the selected run. |
| `../replication_artifacts/runs/terra56/<release_id>/` | Current run specifications, source snapshots and future derived outputs | Local working-run storage, not supplied by a Git clone. Do not assume the coauthor has it. |
| `archives/` | Verified recovery copy from before migration | Local recovery evidence, excluded from Git. Do not place it in the final deposit. |
| `../quality_reports/qa/out-of-sample_add-on/` | Detailed implementation, review, download and sync logs | Local audit records. A compact current receipt accompanies this handoff under `documentation/qa/`. |

The code/data separation is usable, and raw BCB inputs are now alongside the other raw inputs. The working layout is not yet a portable deposit. Absolute author-machine paths, the external run root, and source-freeze construction still need a controlled release procedure. Do not move historical files merely to make the tree look cleaner.

### Deliver the BCB snapshot

Copy `data/raw/bcb/20261109_rebuild/` into the same relative location in the coauthor's checkout. A Git clone alone does not provide it. Its total size is 34,567,711 bytes, excluding the parent directory's navigation README.

For a single-file transfer, use `data/raw/bcb/bcb_handoff_20261109.zip` (2,147,468 bytes). It contains all 347 files in the current snapshot. Extract it into `data/raw/bcb/`; the archive already contains the `20261109_rebuild` directory. Every archived file was checked against its source. The ZIP checksum is `e11080b2c91b2ecc8a967d8ebea971df5cfec0dcac20e169a9dea5acfb32064d`; see [the archive receipt](qa/bcb_rebuild_20261109/archive_verification.json).

The SHA-256 of `source_manifest.json` must be:

```text
17d75c234323f5fac1f1c9e697458817699911369dfa863203420c016a116931
```

On PowerShell, check it with:

```powershell
Get-FileHash -Algorithm SHA256 data/raw/bcb/20261109_rebuild/source_manifest.json
```

The manifest supplies checksums for the normalized files and original responses. Verify those after transfer as well. Do not rerun the downloader to overwrite this snapshot. A later download needs a new directory and records a different input vintage.

After transfer, rerun the bounded data check below, choosing a new empty output directory. It verifies the snapshot checksums, the ten series definitions and coverage for all 79 meetings. The optional `--previous` argument also compares numeric files with an older snapshot when that snapshot is available; the coauthor does not need the older download to check the transferred data.

```powershell
python -B code/04_verification/check_bcb_snapshot.py --snapshot data/raw/bcb/20261109_rebuild --dates data/raw/copom_minutes/_dates.csv --output documentation/qa/bcb_transfer_check
```

## Dataset inventory and provenance

| File in the new snapshot | Contents | Coverage or limitation |
| --- | --- | --- |
| `sgs.csv` | 7,715 rows across ten official SGS series | Values, reference dates, series codes, request URLs and retrieval times. Reference dates are not publication dates. |
| `focus.csv` | 49,380 observations | Aggregate IPCA twelve-month expectations: 2,718. Top-5: 8,133. Selic: 38,529. |
| `ibc_release_calendar.csv` | 652 series-by-release records | 313 actual, 100 announcements and 239 scheduled records. Every actual release has an identified reference month. These are not counts of unique publication events. |
| `ibc_series_enriched.csv` | 640 IBC-Br observations with associated evidence fields | Current retrieved values. They are not verified original-publication values. |
| `ibc_vintage_observations.csv` | Header only, zero verified rows | Historical value extraction and validation remain to be done. |
| `responses/IBC-Br_todas_publicacoes.xlsx` | Official historical-revisions workbook | Downloaded and ZIP integrity checked. Its contents have not been converted into verified release vintages. |
| `responses/` and `source_manifest.json` | Original API responses, source addresses, parameters and hashes | 338 retained responses plus three newly downloaded official metadata pages. The manifest status is `acquired_current_values_vintages_unverified`: metadata are verified, but values before later revisions are not. |

For each Copom meeting, use the latest IBC-Br figure that BCB had already published. BCB usually releases the index about 45 days after the end of the reference month: June activity, for example, is usually reported in mid-August. BCB published the June 2020 figure on August 14, 2020, so that figure was unavailable at earlier meetings. The downloader collects actual publication dates from BCB notices. Use those dates to select the available figure; do not assume the lag is exactly 45 days in every month. See [BCB's explanation of the publication lag](https://www.bcb.gov.br/detalhenoticia/21028/noticia).

The overall IBC-Br was publicly available before 2025. BCB began publishing separate IBC-Br indices for agriculture, industry and services in April 2025, including estimates for earlier years. Those earlier estimates cannot be treated as figures publicly available at earlier meetings. This distinction concerns publication of the sector indices, not access through the API. See BCB's announcement: https://www.bcb.gov.br/detalhenoticia/20618/nota.

The November 9 rebuild corrected title whitespace and recovered reference years explicitly stated in the BCB notices. It also downloaded the three missing series descriptions from BCB's official SGS pages. The numeric SGS and Focus files are byte-for-byte identical to the original download. Publication-date coverage passed for all 79 study meetings. Meetings 214, 279 and 281 also had same-day releases: the coverage table lists the prior-date figure and flags the same-day notice for a decision-time check. It does not assume a release was available before the meeting simply because both occurred on the same date.

Some rows outside the meeting-level selections remain unmatched: 327 are sector-index observations for periods before those indices were first published, and four are the aggregate indices for March and April 2022, covered by an exceptional joint BCB notice. None creates a missing prior-date match in the 79-meeting coverage check. Original-release values remain a separate task: today's value for June 2020 may differ from the value BCB published in August 2020.

The SGS inventory is:

| Code | Series | Last reference period in this download |
| --- | --- | --- |
| 7478 | IPCA-15 | October 2026 |
| 24363 / 24364 | IBC-Br without / with seasonal adjustment | August 2026 |
| 29602 / 29604 / 29606 | Seasonally adjusted agriculture / industry / services IBC-Br | August 2026 |
| 10813 | BRL per USD buying rate | November 6, 2026 |
| 432 | Selic target | November 8, 2026 |
| 13522 / 433 | Twelve-month accumulated / monthly IPCA | September 2026 |

All numeric acquisitions start in January 2016. The selected same-date inflation target uses `ExpectativasMercadoInflacao12Meses`, IPCA, `Suavizada=N`, `baseCalculo=0`. Top-5 calculation types remain separate. Selic records retain their meeting identifier. No confidential institution-level expectations were downloaded.

Current CSV schemas are defined in `download_minutes.py` by `FOCUS_COLUMNS`, `RELEASE_COLUMNS` and the SGS writer. The main keys are series/date for SGS; entity/indicator/date/reference period/meeting/calculation type/smoothing/base for Focus; and series/reference month/release ID for release records. An empty reference-month field means evidence was insufficient, not that the event can be aligned by a fixed lag.

### Source citations and rights

- Banco Central do Brasil. 2026. *Sistema Gerenciador de Series Temporais*, the ten series listed above. Acquired November 8, 2026 through `https://api.bcb.gov.br/dados/serie/bcdata.sgs.<code>/dados`. The manifest retains each exact dated query. Catalogue metadata came from the [BCB open-data catalogue](https://dadosabertos.bcb.gov.br/).
- Banco Central do Brasil. 2026. *Expectativas de Mercado*. Acquired November 8, 2026. [Dataset catalogue](https://dadosabertos.bcb.gov.br/dataset/expectativas-mercado), [OData service](https://olinda.bcb.gov.br/olinda/servico/Expectativas/versao/v1/odata/). The selected entities and filters are recorded above and in the manifest.
- Banco Central do Brasil. 2026. *IBC-Br publication evidence and historical revisions*. Acquired November 8, 2026. [IBC-Br page](https://www.bcb.gov.br/estatisticas/ibc-br), [historical notes](https://www.bcb.gov.br/notasporano), [revisions workbook](https://www.bcb.gov.br/content/indeco/ibcbr/IBC-Br_todas_publicacoes.xlsx).
- Banco Central do Brasil. *Atas do Copom*. Existing minute files use the official `/api/servico/sitebcb/copom/atas` and `/atas_detalhes` acquisition route. Their original acquisition dates were not re-established in this handoff. Recover them from the earlier provenance before the final deposit.

`LICENSE` declares BSD 3-Clause for author code. `LICENSE_DATA` declares CC BY 4.0 for author-generated data to the extent the authors hold rights, and identifies BCB/Tesouro inputs separately under ODbL. These are the existing repository declarations, not a new source-by-source legal clearance. Confirm attribution and redistribution rights for each final included dataset, including material not covered by the current catalogue. No new licensing decision was made here.

The inherited notice excludes CDS levels and EMBI values where redistribution permission was unresolved. The fiscal-support flag is a separate author-generated input. Preserve that distinction. Update final manuscript data citations, the source register and `CITATION.cff` together. The existing citation's August 2026 date and version 2.0.0 must not be presented as a newly validated Terra deposit.

## Software, hardware and randomness

The current checks used Windows 11, Python 3.13.5 and R 4.5.3 on an Intel Core Ultra 7 165H, with 16 cores, 22 logical processors and 31.43 GiB RAM. This describes the machine used; peak RAM and the minimum required hardware for full Terra estimation have not been measured.

The full R dependency list is in `DESCRIPTION`. `renv.lock` contains 166 package records and was created for R 4.5.2. Selected locked versions are `fixest` 0.13.2, `ivreg` 0.6-7, `rstan` 2.32.7, `stm` 1.3.8, `data.table` 1.18.2.1 and `RSQLite` 2.4.6. The new bounded R checks used installed global libraries, not a fresh restoration of that entire lockfile. `00_setup.R` restores dependencies and probes the Stan stack; full setup may require a compatible C++ compiler.

Python dependencies are declared in `code/collection/pyproject.toml`. The acquisition extra now includes `requests`, `beautifulsoup4` and `PyMuPDF`, which the downloader directly imports. The successful download used versions 2.34.2, 4.12.3 and 1.27.2.2, respectively. Core observed versions were pandas 2.2.3 and NumPy 2.1.3. Python dependencies still use version ranges, not a complete exact lock. `fastembed` and `tiktoken` were not installed in the checked environment. Install their declared extras before stages that need them.

The package declares Python 3.10 or later, but this check exercised 3.13.5. Collection launchers use PowerShell and accept executable-path arguments. Full execution on a different operating system has not been validated.

The successful BCB download took roughly four minutes and produced about 34.5 MB. This excludes the two failed attempts. The inspected local `data/` tree was about 268 MiB and historical `outputs/` about 344 MiB; these are not estimates of future Terra storage. Full collection, full estimation runtime and peak storage remain unmeasured. The older 11.9-minute clean-room timing in `COMPUTATIONAL_REQUIREMENTS.md` applies only to GPT-5.5.

The planned complete model collection has 51,508 first attempts before retries. The five earlier paid smoke attempts do not establish a reliable production cost or weekly-quota estimate. Obtain an explicit budget and exact-model availability before enabling that route.

Preserve seeds and stochastic settings in the analysis source, record the actual session and package versions, and retain all LLM prompts, responses, request identities and retries. The Codex runner records requested temperature/seeds without claiming those controls were applied when unsupported. A requested-model echo is not independent backend-version evidence. Exact replication should ultimately use the frozen collected responses, rather than require a paid recollection.

## Commands and stage order

On a fresh working copy, with Python and Rscript available:

```powershell
git clone --branch codex/terra56-20261101T225332Z https://github.com/fmotoki/text-iv.git text-iv
Set-Location text-iv
python -m pip install -e "code/collection[acquisition,dev]"
Rscript --vanilla code/00_setup.R
```

These are setup instructions, not a claim that a fresh installation was tested. `00_setup.R` writes package-local setup logs and should be run in the coauthor's working copy, not over protected historical results. Use the locally installed executable paths if the commands are not on PATH. The full collection additionally needs its declared optional dependencies and authorized access to the exact model through the existing runner.

The bounded offline suite uses disposable fixtures and no paid model calls:

```powershell
$env:RSCRIPT_EXECUTABLE = (Get-Command Rscript).Source
$env:PYTHONPATH = Join-Path (Get-Location) 'code/collection/src'
$env:PYTHONDONTWRITEBYTECODE = '1'
python -B -m unittest discover -s code/collection/tests -p 'test_terra56_*.py' -v
```

The R tests now resolve `RSCRIPT_EXECUTABLE` or Rscript on PATH instead of assuming the author's home directory. A suite with skipped R checks is incomplete; inspect the reported count and skips. To repeat the separate five-case cutoff fixture, reserve a new empty `QA_RECORD_DIR`, set that environment variable, and run `Rscript --vanilla code/04_verification/check_knowledge_cutoff.R`. It retains labelled synthetic TEMP outputs; preserve the report and clean only those owned fixtures afterward.

Do not use `code/replication_spec.json` to start production now. It points to the corrected snapshot and its manifest hash, but remains marked `PENDING_SOURCE_FREEZE`; remote model calls, full production and reference acceptance remain disabled. The completed data-only rebuild is recorded under `../replication_artifacts/runs/terra56/terra56_bcb_20261109_rebuild/`, including its effective specification and the source bytes used for acquisition. Its fingerprint is `7219e367ce78d0ee517bacc7ba04c1d16a3860e5858b7be1378aaa0c27a787c5`. Later handoff edits are not substituted into that historical run record. A production run needs a separate specification after the remaining research decisions are settled.

After the blockers below are resolved, the production order is:

1. Build a new coherent local specification, unique run identities, source snapshot and input hashes. A portable scripted procedure for this remains to be completed. One base-location choice should replace author-machine paths, without editing many analysis scripts.
2. Construct verified BCB controls with `code/01_data_preparation/build_bcb_inputs.R`. Bootstrap the selected database and import the pinned BCB snapshot using the offline importer. Do not override a failed readiness status.
3. Use `code/collection/scripts/run_terra56_collection.ps1` for preparation, then its explicitly authorized collection route. It orders main/dose rewrites, original priors, common-prior forecasts, recursive forecasts, scoring and embeddings. Keep its source/prior run links intact.
4. Audit complete cohorts and export a new candidate with `code/01_data_preparation/build_replication_database.py`. Existing frozen targets must not be overwritten.
5. Run the offline master with the approved `REPLICATION_SPEC_PATH`. It builds calibration, the cutoff diagnostic, the remaining analyses, reporting, metadata and verification in order. Its source lists the authoritative stage sequence.
6. Review new outputs independently, reconcile the manuscript, accept references explicitly, and rerun a clean deposit copy. Retain full logs, sessions, hashes and the final artifact inventory.

## Programs and output families

The following is a source-to-output map, not a claim that the new outputs exist. Machine-readable estimates go to `RUN_ROOT/outputs/`; publication tables, figures and checks go to `RUN_ROOT/results/`.

| Program under `code/` | Responsibility or output family |
| --- | --- |
| `01_data_preparation/download_minutes.py` | Original public texts or BCB snapshot, selected explicitly by mode |
| `01_data_preparation/build_bcb_inputs.R` | Meeting-level release/vintage availability audits and eligible activity controls |
| `collection/scripts/collect_referee_completion_bcb.R` | Offline import and same-date Focus target audit |
| `01_data_preparation/build_replication_database.py` | Read-only selected-cohort candidate export |
| `01_data_preparation/build_calibration_data.R` | Model-neutral `calibration_data.dta` and construction audit |
| `02_analysis/analyze_common_prior_full.R` | Main calibration, common-prior effects, inference and dose outputs |
| `02_analysis/analyze_knowledge_cutoff.R` | Training rows, frozen coefficients, predictions, metrics and immediate framing contrasts; `rr_knowledge_cutoff_*` tables |
| `02_analysis/analyze_arm_specific_prior_dose.R` | Recursive path and dose results, separate from immediate contrasts |
| `02_analysis/run_referee_identification_calibration.R` | Identification and calibration sensitivities |
| `02_analysis/run_referee_d6_bayesian_calibration.R` | Constrained Bayesian calibration and posterior objects |
| `02_analysis/run_referee_d8_oos_validation.R` | Existing LOO/expanding-window exercises; `rr_llm_*` tables |
| `02_analysis/run_referee_external_validation.R` | External target/control comparisons and availability checks |
| `02_analysis/run_referee_d10_sunspot.R` / `run_referee_d11_posterior_tournament.R` | Variance/path diagnostics and posterior tournament |
| `02_analysis/run_referee_d15_topic_preservation.R` / `run_referee_d16_embedding_tone.R` | Topic preservation and embedding-based tone checks |
| `03_reporting/build_referee_integration_tables.R` | Integration of machine-readable results into LaTeX tables |
| `03_reporting/build_baseline_figures.R` | Publication figures and underlying plotting data, including cutoff figures |
| `01_data_preparation/build_metadata.R` and `04_verification/` | Provenance, dictionaries, input/output checks, numerical/reference validation and package inventory |

The final manuscript table/figure numbers and every in-text number still need a current crosswalk. `data/metadata/exhibit_manifest.csv` is the inherited mapping. Neither that mapping nor existing PDFs establish that the new manuscript results were reproduced. The manuscript itself was not audited in this handoff task.

## Remaining work, in order

| Priority | Owner | Required action and completion evidence |
| --- | --- | --- |
| 1 | Authors | Choose activity controls using information available at each meeting from 203 onward. IBC-Br is usually published about 45 days after the reference month ends: June activity is normally disclosed in mid-August, so the June figure is normally unavailable at June or July meetings. Match figures to meetings using BCB's actual publication dates. The overall IBC-Br was already public before 2025; the separate sector indices were first published in April 2025. For earlier meetings, choose indicators that were public at the time; alternatively, use the sector indices in a separate analysis of later meetings. Explain the choice and any change in the sample. |
| 2 | Completed | All ten SGS series definitions are verified in the corrected snapshot. The downloader now uses the official SGS table when the catalogue lacks a description. Original pages and their hashes accompany the data. |

3. **Complete the timing check for IBC-Br releases on meeting days. Owners: authors and data lead.** Publication-date coverage has already been checked for meetings 203 through 281: each meeting has a matched earlier IBC-Br release, and the [coverage CSV](qa/bcb_rebuild_20261109/meeting_bcb_coverage.csv) records the reference month, publication date and lag. What remains is to handle the additional releases on the dates of meetings 214, 279 and 281. Establish whether each was published before or after the policy decision, using official times where available. A figure released after the decision was not available for that decision. If only dates are known, the authors must choose and document a consistent rule for same-day releases. Save the information deadline for every meeting from 203 through 281, its time zone and the source or agreed rule in `meeting_information_cutoffs.csv` under the selected run's `data/analysis/` directory. That file does not yet exist. Use it to select the appropriate release for each meeting; the earlier date-matching work does not need to be repeated.

4. **Extract historical IBC-Br values from the workbook already downloaded. Owner: data lead.** BCB can revise earlier activity figures. The downloaded SGS series contain the values available when we downloaded them, which may differ from the values seen by someone attending an earlier Copom meeting. For meetings 203 through 281, recover the published values needed for the chosen activity controls, including any earlier months needed to calculate growth. The starting file is already in the supplied data ZIP: `data/raw/bcb/20261109_rebuild/responses/IBC-Br_todas_publicacoes.xlsx`. Extract its recorded release versions and check whether they cover the publications needed for the study. Obtain older official releases only where the workbook does not cover those dates. Downloading the same SGS series again will not recover earlier versions of its values. Save each value with its series code, the month it measures, its publication date and its supporting source in `ibc_vintage_observations.csv` inside a new BCB snapshot. That file currently has no observations. List any values that cannot be recovered so the authors can decide how to handle the affected control; do not quietly replace them with today's revised values.

5. **Make the code runnable on the coauthor's computer and record exactly which inputs it uses. Owner: code lead.** Several settings still contain the original author's absolute file paths. Replace these with one documented choice of project location, and prepare a fresh output directory for the new analysis. Create the run specification with the selected meetings, model settings, input filenames and output locations. Save a copy of the code used and checksums that identify the exact input files, including the corrected BCB snapshot. This allows a later reader to establish which code and data produced a result. Install the declared R and Python packages in a fresh environment, record their versions and run the short verification commands in this handoff. The coauthor should be able to repeat that setup from the repository and the supplied data ZIP without editing paths throughout the scripts. Keep the earlier GPT-5.5 results in their existing directories.

6. **Collect the model responses required for the new experiment. Owners: authors and collection lead.** The full experiment using `gpt-5.6-terra`, high reasoning and Standard service has not been run. Confirm access to that exact model and agree a spending limit before starting the approximately 51,508 planned first requests, plus any retries. Use `code/collection/scripts/run_terra56_collection.ps1` to collect the rewritten Copom texts and inflation forecasts for meetings 203 through 281. Run all configured text and forecasting conditions, with 25 repeated draws for the main experiment, five for the dose-response experiment and five sets of rewritten texts. Keep forecasts that start from the same inflation expectation separate from forecasts in which each text condition develops its own sequence of updated expectations. Complete the tone and content checks and the numerical text representations used for text comparisons. Retain the prompts, model settings, request dates, responses, retries and rejection reasons. Check that every required meeting and experimental condition has the required responses; the existing artificial test responses are not observations for the paper.

7. **Run the statistical analysis and generate the tables and figures. Owner: analysis lead.** Once the new model responses and meeting-level economic controls are complete, use `code/01_data_preparation/build_replication_database.py` to create the analysis database for the new experiment. Check for missing meetings, duplicate records, unsuccessful requests and incorrect links between forecasts, texts and economic data. Then run `code/00_master.R` with the new run specification to construct the calibration data, estimate the planned models and generate the reporting outputs. Retain the specified samples, standard errors and resampling settings, and document any necessary change. The main analysis uses all 79 meetings. A separate exercise checks forecast agreement around the model's February 16, 2026 knowledge cutoff: estimate the relationship between model and Focus forecasts using meetings 203 through 271, then compare the five meetings immediately before and the five immediately after that date. Save the estimates, uncertainty measures, sample sizes and execution logs alongside the tables and figures, including results that do not support the expected pattern.

8. **Update the paper to match the completed analysis. Owner: authors.** Check every reported coefficient, percentage, sample size, table and figure against the new outputs. Create a list linking each paper table or figure to its output file and the script that generates it, so another researcher can trace the reported results. Cite the economic datasets and document the model, prompts and collection dates. Explain what the experiment establishes about the model's inflation forecasts. A change in those forecasts does not by itself establish a causal effect on households' or professional forecasters' expectations. State explicitly whether the results have been checked against human responses or other models, and describe the limits where those checks have not been done. Revise the interpretation if the completed analysis changes the evidence supporting it.

9. **Assemble and test the package to be deposited with the paper. Owner: release lead.** Include the code, required public economic data, saved model responses, final analysis inputs and instructions for reproducing every reported result. A researcher reproducing the paper should be able to use the saved model responses without paying to collect them again. Check permission to redistribute each dataset and include its required attribution. Supply installation instructions and files recording exact R and Python package versions, the order of execution and the expected outputs. Run the package from a clean copy on another computer or in a fresh environment; compare its tables, figures and numerical results with the accepted outputs. Record the software versions, hardware, elapsed time, memory use and any manual steps. Resolve discrepancies before assigning the final release version, depositing the package and adding the repository's permanent identifier to the paper and citation file.

## AEA preparation checklist

This handoff follows the coverage requested by the [AEA preparation guidance](https://aeadataeditor.github.io/aea-de-guidance/preparing-for-data-deposit.html) and the [social-science README template](https://social-science-data-editors.github.io/template_README/), consulted November 8, 2026. It distinguishes documented items from remaining work.

| Area | Current state |
| --- | --- |
| Provenance, access and rights | Sources, files, access route and repository licenses documented. Source-level gaps remain above. |
| Dataset inventory and dictionaries | Current raw files and schemas listed. New analysis dictionaries await complete outputs. |
| Software and computational requirements | Declared dependencies, observed versions and hardware recorded. Full environment lock and production resource measurements remain pending. |
| Controlled randomness | Source settings and runner limitations identified. Final sessions and collected response archive remain pending. |
| Native source and execution order | Programs and stage sequence supplied. Portable configuration and complete production inputs remain blockers. |
| Exhibit mapping and citations | Output families mapped. Final paper-number crosswalk and manuscript citations not yet verified. |
| Re-execution | Limited offline and download checks passed. No new full clean-room result exists. |
| Deposit assembly | Not prepared or uploaded. Git synchronization alone does not publish the data or certify reproducibility. |

For the eventual deposit, keep code and data in separate subdirectories under one documented package root. Include required frozen model outputs so exact replication does not depend on remote model access. Exclude credentials, caches, installed environments, backup archives, failed snapshots and superseded working runs. Curate the actual file count before applying the AEA repository's file-count/ZIP guidance. The current whole working tree is not the deposit selection.

The coauthor can begin with source review, local environment setup and the corrected raw-data transfer. Metadata verification and coverage from meeting 203 onward are complete. The remaining tasks concern the choice of activity controls, historical values before later revisions, meeting timing, full collection and final replication. Full collection and deposit acceptance should wait for the relevant decisions and inputs.
