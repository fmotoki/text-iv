# Collection transparency layer

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
