# Collection transparency layer

## Current prospective Terra route

Use `scripts/run_terra56_collection.ps1 -SpecificationPath <spec>` and
`../../documentation/terra56_collection_validation_protocol.md`. The launcher
prepares the selected run by default. Live requests need an explicit switch and
specification authorization. The exact model is GPT-5.6 Terra with high reasoning.
The user requested a smaller representative test instead of the planned full
92-request smoke. Production collection remains unrun.

The shared specification identifies code in `source_root` and new data/results
in a sibling `artifact_root`. Runtime writes stay inside `run_root`, except the
separately named immutable BCB acquisition snapshot. Cached real completion must
match prompt/source/request hashes and configuration. Dry runs are not real
results. DB-first dose runs are never refreshed from absent file results.

Raw blinded scoring uses a separate scorer run with a source link. Realized-dose
rows retain the dose source run and scorer ID. Embedding metadata is keyed by
run and model, preserving historical rows. The lexicon and embedding model are
separate from the migrated LLM. Codex JSON events retain usage and reject tool
use during forecasting. Requested model echoes are not backend verification.

## Historical release context

The material below describes the inherited frozen GPT-5.5 release. Its collection
history does not establish completion of the prospective Terra cohort.

This directory records the code that created the counterfactual texts, synthetic-forecaster tasks, blinded scoring tasks, embeddings, and public external-source extracts. It is provided for prompt and protocol audit, not as part of the default exact replication.

The frozen outputs used by the paper are distributed in `../../data/generated/prompt_iv_replication_source.sqlite`. Re-running a remote model can produce a different sample because provider models and sampling controls change over time. A new collection must therefore use a new run ID and must never overwrite the frozen database.

The example configuration is safe by default:

- `runner = "dry-run"`;
- `allow_real_llm = false`;
- `max_workers = 1`;
- numeric-preservation screening enabled.

No provider credential is included. Remote calls require an explicit opt-in in the calling environment. The prompts are versioned in `src/prompt_iv/generation/prompts.py` and `src/prompt_iv/agents/prompts.py`; run-level settings are stored in the SQLite `runs` table and exported to `../../data/metadata/collection_run_inventory.csv`.

`scripts/run_v10_focused_replication.ps1` is a documented reconstruction of
the missing base launcher referenced by the historical 25-regeneration
configuration wrapper. The original base script was unavailable. The
replacement defaults to plan-only behavior, requires `-AllowRemoteCalls` for
prospective collection, and rejects the frozen historical run ID before any
write in both plan-only and remote modes. The retained historical wrapper is
therefore an inspectable configuration record whose default invocation exits
without changing the frozen run. See
`../../documentation/run_v10_focused_replication_reconstruction.md` for the
evidence-to-behavior map and the reconstruction choices.

The paper's econometrics are not implemented in this Python layer. All reported estimates are produced by the R scripts in `../02_analysis/`.
