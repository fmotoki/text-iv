# Reconstruction of `run_v10_focused_replication.ps1`

## Status

The current Terra route is `run_terra56_collection.ps1`, documented in
[terra56_collection_validation_protocol.md](terra56_collection_validation_protocol.md).
It explicitly registers original, common-prior, and recursive designs under the
selected specification and separate artifact root. The historical reconstruction
below is not the Terra collection recipe. The retained 25-draw wrapper and old
protocol IDs have not been relabelled as Terra results.

The original base launcher referenced by
`code/collection/scripts/run_v10_referee_d5_25draws.ps1` was not present in
replication package 2.0.0. The replacement is a prospective-collection
reconstruction. It is not the unavailable production script and does not
establish historical control-flow identity or byte-identical recollection from
a changing remote endpoint.

Exact reproduction of the paper continues to start from
`data/generated/prompt_iv_replication_source.sqlite` and to run
`code/00_master.R`. The reconstructed launcher does not modify that database.

## Evidence-to-behavior map

| Reconstructed behavior | Package evidence |
| --- | --- |
| Accept the parameter interface used for the 25-regeneration run | `code/collection/scripts/run_v10_referee_d5_25draws.ps1` |
| Prepare 25 iterations, five blocks, hawk and dove tasks, and prompt version `calibrated_symmetric_rebalance_v10` | Historical wrapper and `prompt_iv.cli prepare-replication` |
| Use resumable file-based rewrite and agent outputs | `prompt_iv.generation.execution.execute_rewrite_manifest`, `prompt_iv.agents.execution.execute_agent_manifest`, and `prompt_iv.pipelines.replication` |
| Retry rewrites that fail the numeric-token multiset screen | `prompt_iv.generation.execution` and `prompt_iv.generation.numeric` |
| Assemble completed blocks and require both arms | `prompt_iv.generation.corpus` and `prompt_iv.cli assemble-counterfactuals` |
| Produce rewrite-quality and tone-score outputs | `prompt_iv.generation.quality`, `prompt_iv.pipelines.tone`, and their CLI commands |
| Build, execute, and assemble anchored and unanchored forecaster tasks | `prompt_iv.pipelines.replication`, `prompt_iv.agents.execution`, and the counterfactual-agent CLI commands |
| Preserve a run-specific SQLite snapshot | `documentation/sqlite_database.md` and `prompt_iv.cli sync-database` |
| Use agent filenames consumed by SQLite synchronization | `prompt_iv.storage.sqlite.sync_run` |
| Stop before downstream stages when any task is missing or has a non-completed status | JSONL executor status fields, corpus completeness checks, and resumable execution contracts |
| Default to no remote calls | `code/collection/README.md`, `config/pipeline.example.toml`, and the `allow_real_llm` guard in `prompt_iv.llm.runner` |
| Require a new run ID for every prospective action and protect the frozen run and database | `README.md`, `code/collection/README.md`, and `documentation/sqlite_database.md` |
| Record requested temperature without claiming it was applied by Codex CLI | `prompt_iv.llm.runner.CodexCliRunner` |

## Reconstruction choices

The retained code does not define the missing launcher's chunk loop. The
replacement maps `ChunkSize * MaxChunks` to the packaged CLI task limit. A zero
maximum leaves the CLI limit unset and consumes all pending tasks. Worker
parameters map directly to the CLI `--max-workers` options.

The replacement uses the retained file-based JSONL pipeline because it matches
the replication-run directory layout and supports resume by task ID. A final
run-specific SQLite snapshot can be built after collection. The script never
points collection at the frozen database.

The launcher ends after agent-output assembly and a run-specific database
snapshot. Statistical analysis remains in the R programs under
`code/02_analysis/`, as stated in `code/collection/README.md`.

The launcher treats only `status=completed` as complete. Numeric-screen
failure, parse failure, usage limits, and execution failure stop the run before
assembly. A task limit may end one resumable batch with tasks still pending,
but downstream stages remain blocked and the launcher does not announce full
completion. Resume compatibility includes the requested rewrite temperature
and all settings recorded in the collection plan. The agent manifest and
result filenames match the fixed names consumed by `sync-database`.

## Safety and use

Calling the script with a new run ID and without `-AllowRemoteCalls` prepares
the rewrite manifest and `collection_plan.json`, then exits. It issues no
remote request. The launcher rejects the frozen run ID before it invokes the
CLI or writes a plan, regardless of remote opt-in. A remote run requires both
`-AllowRemoteCalls` and a new run ID.

The historical parameter wrapper remains an inspectable record of the frozen
task configuration. Its default invocation stops at the immutable-run guard
and does not change the frozen run.
