# Replication code

## Can Words Move Inflation Expectations? A Counterfactual-Text Laboratory

Authors are Fernando Barros Jr., Fabio Y. S. Motoki, Valdemar Pinho Neto, and Victor Rangel. Contact is victorrsr@al.insper.edu.br.

## Coauthor starting point

Read the [coauthor handoff](documentation/COAUTHOR_HANDOFF.md) for the current code/data map, completed checks, setup commands, ordered remaining work and AEA deposit checklist. The BCB download is complete at `data/raw/bcb/20261108_267de531/`, but that folder is excluded from Git and must accompany a transfer to another machine. The handoff distinguishes downloaded data from analysis-ready inputs. The prospective Terra package is not yet deposit-ready.

Use this Markdown README for current status. `README.pdf`, the version 2.0.0 citation, and the historical clean-room record have not been regenerated as a new Terra release.

The current branch implements a prospective GPT-5.6 Terra migration and a frozen-calibration Focus agreement diagnostic. It has limited QA, not a new production result release. Full recollection, statistical regeneration, reference acceptance, and clean-room validation have not run.

The inherited GPT-5.5 inputs, results, metadata, references, and historical clean-room record remain in their existing locations. A verified recovery copy was created before source edits. Current code requires a new selected Terra run and cannot silently treat older outputs as new-model evidence.

## Code and data versions

Git versions source, prompts, configuration, and documentation in this directory. Pre-task downloader edits have a separate baseline commit. Run manifests identify the exact source commit and file hashes used by each data/result version.

Raw inputs stay together under `data/raw/`. Supporting BCB snapshots use `data/raw/bcb/<download_stamp>/`, and existing minute texts remain read-only under `data/raw/copom_minutes/`. BCB snapshots are excluded from Git and identified by their download manifests. Derived data, results, models, logs, and run manifests remain in `../replication_artifacts/runs/terra56/<release_id>/`. Recovery archives remain under `archives/`, excluded from Git.

`code/replication_spec.json` is the cross-language specification. It identifies both roots, model/effort, sample, run IDs, raw snapshot, source/input hashes, and release status. Runtime scripts require an explicit specification through `REPLICATION_SPEC_PATH` or the documented CLI argument. Each run keeps a matching `effective_spec.json`. Missing or changed identities fail before collection, output writes, or master cleanup.

## Fixed research design

The model is exactly `gpt-5.6-terra` at high reasoning. Meetings 203 through 281 form the 79-meeting sample. There are 25 main draws, five six-arm dose draws, five rewrite blocks, two forecaster profiles, and separate common-prior and arm-specific recursive designs. Planned LLM work totals 51,508 first attempts before retries. These dimensions are not completion counts.

`code/01_data_preparation/build_calibration_data.R` writes the model-neutral `outputs/work/calibration_data.dta` inside the selected run. The main unrestricted calibration uses all 79 meetings. The separate diagnostic fits once on 203 through 271 and evaluates 272 through 276 and 277 through 281, five meetings per window. It measures agreement with same-date median Focus IPCA twelve-month-ahead expectations. Only the composite reports an RMSE ratio, divided by previous-meeting Focus persistence RMSE. No five-meeting significance test is used. Framing draws are averaged within meetings before equal meeting weights. See the [protocol](documentation/terra56_collection_validation_protocol.md).

## Execution boundary

The active launcher is `code/collection/scripts/run_terra56_collection.ps1 -SpecificationPath <spec>`. It defaults to preparation. Remote calls require `-AllowRemoteCalls` and explicit authorization in a frozen specification. The analysis master is offline. It refuses incomplete inputs, mixed cohorts, and incompatible resume markers. Candidate export refuses an existing target.

The handoff is under `../quality_reports/qa/out-of-sample_add-on/20261101T225332Z_g0_5b2042/`. It records actual checks, independent reviews, source identities, a path map, recovery evidence, the token/weekly-allowance estimate, and disabled future commands. The user replaced the planned 92-request smoke with a smaller representative test. Its actual request count and coverage are recorded there. No full production collector or master is launched by this task.

Strict input readiness is separate from code QA. The three sector activity series were not public before April 11, 2025. Current revised values do not establish historical release vintages. Missing disclosure, vintage, same-day cutoff, or public-availability evidence blocks the strict full-sample panel. The audit keeps every intended meeting/series key. Replacing controls, shortening the sample, or using current-vintage sensitivities requires an author decision. Fiscal and mature-horizon checks retain their documented available support.

Terra numerical references are unaccepted. Missing production outcomes or a baseline yield NOT RUN or an input failure. Historical successful gate counts and GPT-5.5 coefficients are not Terra acceptance rules.

## Source map and dependencies

- `code/00_master.R` validates selected inputs and runs offline stages.
- `code/01_data_preparation/` acquires or imports frozen public inputs, constructs calibration data, and builds metadata/candidate exports.
- `code/02_analysis/` preserves existing estimators and adds the frozen69 diagnostic.
- `code/03_reporting/` writes tables, figures, and plotting data inside the run.
- `code/04_verification/` checks identities, metrics, references, and manifests.
- `code/collection/` contains prompt families and guarded collection commands.
- `documentation/` contains the current protocol and labelled historical records.

R dependencies remain locked in `renv.lock`. The new figures use `cowplot`, already in that lockfile. Collection uses the declared Python dependencies and existing Codex CLI. Lexicon scoring and the separately identified MiniLM embeddings remain distinct from LLM requests. Requested temperature and seed are recorded without claiming unsupported sampling controls were applied. A response model echo is not independent backend verification. Future remote responses need not be byte-identical.

Code is BSD 3-Clause (`LICENSE`). Author-generated data are CC BY 4.0 (`LICENSE_DATA`). Third-party material remains subject to provider rights. No confidential institution-level Focus records or fabricated human ratings are used. A final deposit requires reviewed new results, accepted references and numerical baselines, complete provenance, and a new clean-room validation. `documentation/CLEAN_ROOM_VALIDATION.md` describes the inherited release only.
