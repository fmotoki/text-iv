# Prompt-IV SQLite database

This file documents the historical author-side collection workflow. Exact reproduction
of the paper does not run these commands; it starts from the immutable, curated snapshot
`data/generated/prompt_iv_replication_source.sqlite`. Within the released package, the
Python source referenced below is stored under `code/collection/src/`.

The project can materialize the text collection artifacts into a local SQLite
warehouse:

```powershell
$env:PYTHONPATH = "code/collection/src"
python -m prompt_iv.cli sync-database --output data/interim/prompt_iv.sqlite
```

To refresh only one replication run while collection is still running:

```powershell
$env:PYTHONPATH = "code/collection/src"
python -m prompt_iv.cli sync-database `
  --output data/interim/prompt_iv.sqlite `
  --run-dir data/interim/replications/v10_referee_step3_neutral_dose_5draws_20260627
```

Use `--replace` to rebuild the database file from scratch. The command is safe
to re-run: each selected run is deleted from the warehouse and re-ingested from
the current files. If a JSONL file is being appended during collection, malformed
partial lines are counted and skipped in that snapshot.

## DB-first collection

For new collection after the SQLite migration, resume rewrites directly from the
database instead of writing `rewrite_results.jsonl`:

```powershell
$env:PYTHONPATH = "code/collection/src"
python -m prompt_iv.cli execute-counterfactuals-db `
  --database data/interim/prompt_iv.sqlite `
  --run-id v10_referee_step3_neutral_dose_5draws_20260627 `
  --runner codex `
  --allow-real-llm `
  --model gpt-5.5 `
  --reasoning-effort high `
  --temperature 1 `
  --timeout-seconds 300 `
  --max-retries 2 `
  --max-workers 4 `
  --limit 40
```

The author-side step-3 wrapper used during collection is not part of the public analysis
release. The explicit CLI command above records the relevant runner options without relying
on that workspace-specific wrapper.

Once a run is resumed in DB-first mode, do not refresh that same run from the old
JSONL files unless you intentionally want to rebuild the database snapshot from
the pre-DB files. Direct DB events are not mirrored back to JSONL.

## Tables

- `minutes`: raw Copom minutes from `data/raw/copom_minutes/*.txt`.
- `runs`: one row per replication run with summary counts.
- `run_files`: files found for each run.
- `rewrite_tasks`: rewrite manifest tasks.
- `rewrite_result_events`: every rewrite result JSONL event, including retries
  or interrupted attempts.
- `rewrite_results_latest`: latest event per rewrite `task_id`.
- `agent_tasks`: synthetic forecaster manifest tasks.
- `agent_result_events`: every synthetic forecaster result JSONL event.
- `agent_results_latest`: latest event per agent `task_id`.
- `corpus_texts`: assembled meeting-level original/counterfactual texts,
  unpivoted by role.
- `tone_scores`: lexicon tone scores, unpivoted by role.

## Useful queries

Current collection progress:

```sql
SELECT
  run_id,
  rewrite_completed_latest,
  rewrite_tasks_total,
  ROUND(100.0 * rewrite_completed_latest / rewrite_tasks_total, 2) AS pct
FROM runs
WHERE run_id = 'v10_referee_step3_neutral_dose_5draws_20260627';
```

Progress by stance:

```sql
SELECT stance, status, COUNT(*) AS n
FROM rewrite_results_latest
WHERE run_id = 'v10_referee_step3_neutral_dose_5draws_20260627'
GROUP BY stance, status
ORDER BY stance, status;
```

D5 completed counts:

```sql
SELECT run_id, rewrite_completed_latest, agent_completed_latest
FROM runs
WHERE run_id = 'v10_referee_d5_25draws_20260620';
```

Tone score means by role for a completed run:

```sql
SELECT role, COUNT(*) AS n, AVG(score) AS mean_score
FROM tone_scores
WHERE run_id = 'v10_referee_d5_25draws_20260620'
GROUP BY role
ORDER BY role;
```
