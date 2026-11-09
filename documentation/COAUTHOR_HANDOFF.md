# Coauthor handoff

Updated November 8, 2026. Read paths below relative to the `replication/` repository root unless a sibling directory is explicitly named.

## Start here

The prospective Terra implementation and public BCB download are available. Full Terra collection, statistical estimation and a new paper-results release have not been completed. This is a takeover document, not an AEA deposit certification.

The repository is `https://github.com/fmotoki/text-iv`, on branch `codex/terra56-20261101T225332Z`. Use `git rev-parse HEAD` to identify the version containing this handoff. The older `main` branch and the version 2.0.0 citation describe inherited material.

First obtain the code branch and the separate BCB snapshot described below. Then resolve the activity-information policy with the authors before starting expensive collection. Passing synthetic tests does not resolve that research decision.

The paper-facing design uses meetings 203 through 281, inclusive, with 79 meetings, 25 main draws, five dose draws, five rewrite blocks, and anchored/unanchored profiles. The requested model is `gpt-5.6-terra` with high reasoning and Standard service. Common-prior responses and arm-specific recursive paths remain distinct designs.

The new cutoff diagnostic fits an unrestricted calibration on meetings 203–271 only. It compares five meetings before the February 16, 2026 model cutoff with five afterward, using same-date Focus twelve-month IPCA expectations. Its benchmark is previous-meeting Focus. It measures descriptive agreement, not realized inflation forecast accuracy or a causal effect on human beliefs. The main calibration still uses the separate full-79 sample.

## What is complete

| Item | Evidence and limit |
| --- | --- |
| Collection, calibration, cutoff and reporting source | Implemented, with bounded fixture checks. Full production integration remains unrun. |
| Original minute texts | The repository contains the historical text collection. Select exactly 203–281 through `_dates.csv`, rather than taking the first 79 filenames. |
| New public BCB snapshot | Download completed with exit 0. Ten SGS series, three Focus entities, release evidence and the revisions workbook are saved. |
| Download verification | 338 original response hashes and five normalized-file hashes passed. SGS, Focus and release keys are unique. Selected Focus targets exist on all 79 meeting dates. |
| Frozen-cutoff fixture | Five cases passed, including held-out-target perturbation, zero benchmark error, missing neutral and changed source hash. These were synthetic checks. |
| Limited live model checks | The earlier implementation handoff reports four request families and five paid attempts. The planned 92-request smoke and full trajectories were not completed. |
| Existing results | GPT-5.5 source data, estimates, reference exhibits and the earlier recovery archive were preserved. They are not new Terra results. |

The final offline suite was rerun on November 8: 34 tests passed in 34.111 seconds, with zero skips and no model calls. The [portable verification receipt](qa/handoff_verification_20261108.json) records the source hashes, download checks and scope limits.

The full public-data run exposed two faults not reached by the initial smoke checks. The old SGS metadata server returned HTTP 502, and some note IDs were JSON floats that produced invalid detail URLs. The downloader now uses the official catalogue, records missing descriptors explicitly, and converts validated integral note IDs to the required URL form. The offline importer rejects incomplete metadata before creating a database.

## Code and data organization

| Location | Role | Transfer and handling |
| --- | --- | --- |
| `code/` | Native Python, R and PowerShell source, prompts and tests | Versioned in Git. |
| `data/raw/copom_minutes/` | Existing texts and the canonical meeting-date map | Texts and date map are included in the code handoff. Preserve their bytes. |
| `data/raw/bcb/20261108_267de531/` | Newly downloaded public BCB inputs | Excluded from Git. Transfer the whole directory, including `responses/` and `source_manifest.json`. |
| `data/raw/calibration_controls/`, `data/analysis/`, `data/generated/` | Inherited controls, analysis files and GPT-5.5 SQLite source | Historical inputs. Do not relabel them as Terra inputs. |
| `data/metadata/` | Earlier dictionaries, source inventory and exhibit mapping | These describe the inherited release and require a new version for Terra. |
| `outputs/`, `results/`, `reference/`, `logs/` | Earlier outputs and reproduction evidence | Preserve as historical records. New production writers use the selected run. |
| `../replication_artifacts/runs/terra56/<release_id>/` | Current run specifications, source snapshots and future derived outputs | Local working-run storage, not supplied by a Git clone. Do not assume the coauthor has it. |
| `archives/` | Verified recovery copy from before migration | Local recovery evidence, excluded from Git. Do not place it in the final deposit. |
| `../quality_reports/qa/out-of-sample_add-on/` | Detailed implementation, review, download and sync logs | Local audit records. A compact current receipt accompanies this handoff under `documentation/qa/`. |

The code/data separation is usable, and raw BCB inputs are now alongside the other raw inputs. The working layout is not yet a portable deposit. Absolute author-machine paths, the external run root, and source-freeze construction still need a controlled release procedure. Do not move historical files merely to make the tree look cleaner.

### Deliver the BCB snapshot

Copy `data/raw/bcb/20261108_267de531/` into the same relative location in the coauthor's checkout. A Git clone alone does not provide it. Its total size is 34,507,906 bytes, excluding the parent directory's navigation README.

The SHA-256 of `source_manifest.json` must be:

```text
4d312c5277e67d7ea4616b33183263a7d8b3079a10549411e3b0047b9ff6811c
```

On PowerShell, check it with:

```powershell
Get-FileHash -Algorithm SHA256 data/raw/bcb/20261108_267de531/source_manifest.json
```

The manifest supplies checksums for the normalized files and original responses. Verify those after transfer as well. Do not rerun the downloader to overwrite this snapshot. A later download needs a new directory and records a different input vintage.

## Dataset inventory and provenance

| File in the new snapshot | Contents | Coverage or limitation |
| --- | --- | --- |
| `sgs.csv` | 7,715 rows across ten official SGS series | Values, reference dates, series codes, request URLs and retrieval times. Reference dates are not publication dates. |
| `focus.csv` | 49,380 observations | Aggregate IPCA twelve-month expectations: 2,718. Top-5: 8,133. Selic: 38,529. |
| `ibc_release_calendar.csv` | 652 series-by-release records | 311 actual, 102 announcements and 239 scheduled records. These are not counts of unique publication events. |
| `ibc_series_enriched.csv` | 640 IBC-Br observations with associated evidence fields | Current retrieved values. They are not verified original-publication values. |
| `ibc_vintage_observations.csv` | Header only, zero verified rows | Historical value extraction and validation remain to be done. |
| `responses/IBC-Br_todas_publicacoes.xlsx` | Official historical-revisions workbook | Downloaded and ZIP integrity checked. Its contents have not been converted into verified release vintages. |
| `responses/` and `source_manifest.json` | Original API responses, source addresses, parameters and hashes | 338 requests retained. The manifest status is `acquired_current_values_metadata_incomplete`. |

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

Do not use `code/replication_spec.json` to start production now. It records the downloaded raw-manifest hash but is explicitly marked `PENDING_SOURCE_FREEZE`; remote model calls, full production and reference acceptance remain disabled. The preserved acquisition identity is `terra56_bcb_20261108_267de531`, with original fingerprint `8a7651e5e17f0cf433f255971b21bcefc52593b931fb18064a7e90fed9620cd8`. Its original effective specification and source snapshot remain under the sibling run root. Later documentation and portability edits were not substituted into that frozen acquisition record.

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
| 1 | Authors | Decide how to handle sector controls before their first public release in April 2025. The current full-sample strict policy cannot treat backfilled values as information then available. Record the decision; do not silently drop meetings or replace controls. |
| 2 | Data lead | Verify the missing catalogue descriptions for SGS 7478, 13522 and 433 from authoritative evidence. Keep declared units distinct from verified units, and produce a new versioned readiness record. |
| 3 | Data lead | Resolve missing reference months in 198 series-by-release rows: 96 actual, 100 announcements and two scheduled. Use dated source evidence rather than guessing the reference year. |
| 4 | Data lead | Extract and validate original-publication values from the revisions workbook and any necessary official archives. `ibc_vintage_observations.csv` currently has zero rows. Current revised values are not a fallback. |
| 5 | Authors and data lead | Supply justified meeting-information cutoffs and same-day ordering. The configured cutoff file is absent. Record either source evidence or an explicit author convention. |
| 6 | Code lead | Build the portable specification/source-freeze step, pin the completed raw snapshot and all inputs, and create a new run. Reconcile current software versions and test a fresh R/Python installation. |
| 7 | Authors and collection lead | Approve model access and a production budget. Complete genuine original/common/recursive paths, scoring and embeddings, with provenance and exact cohort audits. Do not promote dry-run or synthetic outputs. |
| 8 | Analysis lead | Export the complete candidate and run all analysis/reporting stages. Preserve uncertainty methods and sample specifications. Inspect unfavorable results as carefully as favorable ones. |
| 9 | Authors | Reconcile paper claims, numerical references, exhibit mapping and data citations. Keep synthetic behavior separate from human causal interpretation. Cross-model/human validation remains a substantive limitation where absent. |
| 10 | Release lead | Curate the deposit, confirm dataset rights/attribution, finalize a Python lock and platform instructions, perform a fresh-machine run, and record runtime/peak resources. Assign a new release version and deposit identifier only afterward. |

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

The coauthor can begin with source review, local environment setup, the raw-data transfer and the first five evidence/decision tasks. Full collection and deposit acceptance should wait for their explicit completion.
