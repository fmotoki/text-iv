# Common-Prior Prompt-IV Collection Protocol

**Version:** `forecaster_common_prior_v1`  
**Frozen:** 2026-07-12  
**Author-side production database:** `data/interim/prompt_iv-vrangel.sqlite` (not distributed)  
**Distributed frozen snapshot:** `data/generated/prompt_iv_replication_source.sqlite`  
**Schema version:** 3

## 1. Design decision

The primary re-collection separates the current communication effect from the simulated
forecaster's accumulated history. For meeting `t`, forecaster type `a`, source-text regeneration index `r`,
and textual arm `z`, every arm starts from the same pre-treatment forecast
`P_minus[t,a]`. The outcome is the contemporaneous revision

`revision[t,a,r,z] = P_plus[t,a,r,z] - P_minus[t,a]`.

`P_minus[t,a]` is constructed along the chronological original-minute path. Treatment
responses are then kept on separate nonrecursive paths: hawk, dove, neutral, low-dose, and high-dose texts never
feed a later task's state. Recursion is therefore used only to construct the untreated
state variable, not to estimate a treatment effect.

The regeneration index `r` does not define a longitudinal state in this design. In
particular, regeneration 1 at meeting `t` is not linked to regeneration 1 at meeting
`t-1`. Conditional on meeting and forecaster type, all regenerations and textual arms
receive the same `P_minus[t,a]`. Anchored and unanchored forecasters follow distinct
original-minute paths and can therefore receive different numerical priors at the same
meeting.

The 7,100 completed responses from the earlier recursive hawk/dove design are retained in
SQLite and labeled conceptually as `persistent_path`. They are not deleted and are not the
primary common-prior outcome.

## 2. Reused textual material

| Source run | Available material | Status in new collection |
|---|---:|---|
| `v10_referee_d5_25draws_20260620` | 71 meetings x 25 regenerations x original/hawk/dove | Reuse frozen texts; no generator calls |
| `v10_referee_step3_neutral_dose_5draws_20260627` | 71 x 5 x original/neutral/hawk_low/hawk_high/dove_low/dove_high | Reuse frozen texts; no generator calls |
| Earlier agent run | 7,100 recursive hawk/dove responses | Preserve as secondary analysis only |

The V10 texts passed the existing numeric-preservation and blinded semantic workflows.
Reusing them isolates the forecaster redesign from the generator. It also means that any
remaining generator/classifier-model dependence must be reported as a limitation and
addressed in a later cross-model robustness exercise.

## 3. Frozen forecaster prompt

The system prompt is assembled from the common frame below and exactly one profile block.
`<PRIOR>` and `<TARGET>` are formatted with two decimal places. Source code is in
`code/collection/src/prompt_iv/agents/prompts.py`.

### Common frame

```text
Voce e um economista responsavel por projecoes de inflacao em uma instituicao financeira brasileira.
Voce participa regularmente da Pesquisa Focus e informa a projecao do IPCA acumulado nos proximos 12 meses.

ESTADO PRE-TRATAMENTO FIXADO PELO EXPERIMENTO
- Sua projecao imediatamente antes de ler a comunicacao atual e <PRIOR>%.
- A meta oficial de inflacao relevante e <TARGET>% ao ano.
- A projecao anterior foi definida antes da comunicacao atual e deve ser aceita como dada.

PERFIL DE CRENCA
<PROFILE_BLOCK>

REGRAS DE INFORMACAO
1. Use exclusivamente a projecao anterior, a meta e o texto fornecidos nesta solicitacao.
2. Nao use memoria sobre a reuniao, a ata, a Pesquisa Focus ou resultados historicos reais.
3. Se reconhecer o episodio, ignore qualquer informacao lembrada que nao esteja no texto fornecido.
4. Nao suponha que o texto pertence a um tratamento, cenario ou versao especifica.
5. Atualize a projecao somente na medida em que a comunicacao fornecida alterar sua avaliacao da inflacao nos proximos 12 meses.
6. Se o texto nao justificar uma revisao, mantenha a projecao anterior.

TAREFA
Depois de ler a comunicacao, informe sua nova projecao de IPCA acumulado nos proximos 12 meses.
Use um numero entre 0 e 30, em pontos percentuais. Informe somente a projecao, sem justificativa,
rotulos ou avaliacoes adicionais.

Responda apenas com JSON valido, sem markdown e sem campos adicionais:
{
  "nova_expectativa_inflacao": number
}
```

### Anchored profile block

```text
Voce atribui alta credibilidade a meta oficial como ancora de medio prazo. Na ausencia de evidencia clara no texto fornecido, voce evita revisoes grandes e mantem a projecao proxima do estado anterior e da meta. Essa descricao define apenas a precisao de sua crenca anterior; ela nao determina a direcao da revisao.
```

### Unanchored profile block

```text
Voce atribui menor credibilidade a meta oficial como ancora de medio prazo. Voce permite revisoes maiores quando o texto fornecido altera sua avaliacao dos riscos para a inflacao. Essa descricao define apenas a precisao de sua crenca anterior; ela nao determina a direcao da revisao.
```

### User message and schema

```text
TEXTO FORNECIDO:

<FULL_SOURCE_TEXT>
```

```json
{
  "type": "object",
  "properties": {
    "nova_expectativa_inflacao": {"type": "number", "minimum": 0, "maximum": 30}
  },
  "required": ["nova_expectativa_inflacao"],
  "additionalProperties": false
}
```

The prompt does not request a tone label or rationale. Common-prior calls receive the full
source text. This removes the former 15,000-character truncation, which affected 450
original, 947 hawk, and 974 dove cells in the 25-regeneration corpus.

| Frozen object | SHA-256 |
|---|---|
| Anchored prompt family (`PRIOR=0`, `TARGET=0`) | `61e60ed36b1e4e9fdabeab64899dd59f198e4c1687e30126eead6afb52907398` |
| Unanchored prompt family (`PRIOR=0`, `TARGET=0`) | `dc6b9e325f6116ef5ee7ebc47cb8dd52f15500d0b3cd616f18ff4417ebcd3b4e` |
| JSON response schema | `03344b1cc1af819a8636b3bd6d06753c2f3b2ff1e77537862abdcc75851498ed` |

Each realized call also stores its own system-prompt, user-message, source-text, schema, and
complete-request hashes in SQLite.

## 4. Model and execution configuration

| Field | Frozen value |
|---|---|
| Model | `gpt-5.5` |
| Reasoning effort | `high` |
| Runner | Codex CLI `exec` |
| Output enforcement | `--output-schema` with the schema above |
| Workspace exposure | Ephemeral empty working directory, `--ignore-rules` |
| Parallelism | 2 chronological original paths; 4 independent treatment workers |
| Requested temperature | `1.0` |
| Temperature actually passed by runner | No; Codex CLI exposes no supported option |
| Task-ID seed metadata | Deterministic ID stored in DB; not a sampling control |
| Task-ID seed passed as a sampling control | No; Codex CLI exposes no supported option |
| Effective sampling control | `codex_cli_default` |

Temperature is retained as a requested value and the deterministic task-ID seed is retained
as pairing metadata; neither is represented as an applied sampling control.
`temperature_applied=0` and `seed_applied=0` are explicit database fields.

## 5. Collection matrix

| Stage | Calculation | New calls |
|---|---:|---:|
| Original trajectory and pre-treatment states | 71 meetings x 2 forecaster types | **142** |
| Five-regeneration common-prior gate | 71 x 5 x 2 arms x 2 forecaster types | **1,420** |
| Remaining 20 main regenerations | 71 x 20 x 2 arms x 2 forecaster types | **5,680** |
| Six-arm dose response, five regenerations | 71 x 5 x 6 arms x 2 forecaster types | **4,260** |
| **Maximum production total** |  | **11,502** |

The five-regeneration gate and remaining regenerations belong to one prespecified
25-regeneration task run. The first execution is explicitly filtered to regenerations
1--5; continuation resumes the same run.
This prevents task definitions from changing after the pilot. The bounded smoke test adds at
most 28 diagnostic calls and is kept under separate run IDs.

The frozen production run IDs are `common_prior_original_v1_20260712` for the original-path
states and `common_prior_main_25draws_v1_20260712` for the 25-regeneration main plan. Operationally,
the gate is filtered explicitly to iterations 1--5, so a partial resume cannot spill into
iterations 6--25.

## 6. SQLite contract

No collection manifest, response JSONL, or generated text file is created. Preparation adds
rows to `agent_tasks`; every completed, failed, parse-failed, or usage-limited response is
immediately appended to `agent_result_events`; `agent_results_latest` is the current task
state.

| Field group | Main columns |
|---|---|
| Experimental identity | `run_id`, `task_id`, `design`, `prompt_version`, `pair_id` |
| Source identity | `source_run_id`, `source_role`, `meeting_number`, `corpus_iteration`, `source_text_hash` |
| Common state | `prior_source`, `fixed_prior`, `inflation_target`, `forecaster_type` |
| Requested execution | `requested_model`, `requested_reasoning_effort`, `requested_temperature`, `agent_seed` |
| Realized outcome | `previous_expectation`, `new_expectation`, `forecast_revision`, `status` |
| Prompt provenance | `system_prompt_hash`, `user_prompt_hash`, `response_schema_hash`, `request_input_hash` |
| Input integrity | `source_text_char_count`, `supplied_text_char_count`, `text_truncated` |
| Runner capability | `temperature_applied`, `seed_applied`, `response_schema_applied`, `sampling_control` |

Run registration and task insertion are idempotent. Reusing a run ID with different metadata
or a task ID with different contents raises an error. Results are event-sourced, so retries
do not erase earlier failures.

## 7. Mechanical gates

The audit command requires all of the following before a run is marked `ready`:

1. every planned task has a completed latest result;
2. all roles within a pair share the same prior, task-ID seed metadata, and prompt version;
3. each fixed prior equals the corresponding pre-treatment state from the original path;
4. all source hashes match the frozen corpus rows;
5. `forecast_revision = new_expectation - previous_expectation` exactly;
6. no tone output is stored;
7. the response schema was applied;
8. full-text lengths and separate prompt/schema hashes are present;
9. the original path is chronologically continuous within each forecaster type.

The smoke test is mechanical, not inferential. It spans meetings 203 and 204, source-text regenerations
1 and 6, all three main-corpus roles, and both forecaster types. The frozen run IDs are
`common_prior_original_smoke_20260712` and `common_prior_treatment_smoke_20260712`.

## 8. No-tuning rule and appendix use

The smoke test may fix only execution, parsing, storage, or clearly unintended wording bugs.
It must not be used to tune the prompt toward a desired coefficient. Any substantive prompt
change creates a new version and invalidates responses collected under the earlier version.

For the paper appendix, report the estimand, full prompt blocks, response schema, model/runner
capabilities, arm matrix, source run IDs, full-text rule, hashes, mechanical gates, and the
fact that the old persistent-path outcomes remain secondary. Report the absence of effective
temperature/seed controls explicitly rather than describing the requested values as applied.

## 9. Smoke-test record

**Executed:** 2026-07-12  
**Codex CLI:** `0.130.0-alpha.5`  
**Elapsed time:** 204.2 seconds  
**Mechanical decision:** PASS

| Run | Tasks | Completed | Failed/parse-failed | Audit ready |
|---|---:|---:|---:|---|
| `common_prior_original_smoke_20260712` | 4 | 4 | 0 | `true` |
| `common_prior_treatment_smoke_20260712` | 24 | 24 | 0 | `true` |

All audit violation counts were zero: chronological transitions, task design, pair roles,
paired priors/seeds, source hashes, source-prior links, revision identities, unexpected tone
fields, response-schema enforcement, and request provenance. The schema was applied to all 28
responses; requested temperature and task-ID seed metadata were applied as sampling controls
to none, as expected from the CLI capability
audit. No input was truncated. The longest smoke input contained 15,514 characters.

The original-path states were:

| Meeting | Forecaster | Pre-text prior | Post-original forecast | Revision |
|---:|---|---:|---:|---:|
| 203 | Anchored | 3.80 | 4.20 | 0.40 |
| 204 | Anchored | 4.20 | 4.10 | -0.10 |
| 203 | Unanchored | 3.80 | 4.10 | 0.30 |
| 204 | Unanchored | 4.10 | 3.90 | -0.20 |

Treatment-output summaries, reported only as a non-inferential diagnostic, were:

| Role | N | Mean forecast | Mean revision | Min revision | Max revision |
|---|---:|---:|---:|---:|---:|
| Dove | 8 | 4.0125 | 0.0375 | -0.20 | 0.40 |
| Hawk | 8 | 4.0187 | 0.0438 | -0.20 | 0.50 |
| Original | 8 | 3.9937 | 0.0188 | -0.10 | 0.40 |

Across only eight pairs, the mean within-pair hawk-minus-dove revision was 0.00625 percentage
point and individual contrasts ranged from -0.40 to 0.70. This heterogeneity is recorded, not
used to tune the frozen prompt. The smoke sample was selected to exercise meetings, generator
batches, roles, forecaster types, full-text handling, and pairing; it has no power to establish
the first stage. That decision belongs to the prespecified 1,420-call five-regeneration gate.

Re-running the smoke command with the same IDs inserted zero tasks, skipped all 28 completed
tasks, attempted zero calls, and returned both audits as ready. The database reports schema
version 3, exactly 28 smoke events, no duplicate task events, and no corresponding run
directory or JSON/JSONL collection artifact.

## 10. Production five-regeneration gate

**Executed:** 2026-07-12  
**Prior run:** `common_prior_original_v1_20260712`  
**Treatment run:** `common_prior_main_25draws_v1_20260712`  
**Mechanical decision:** PASS  
**Econometric gate decision:** PASS

The original-path run contains 142 completed tasks, two chronological trajectories, and zero
transition, hash, schema, provenance, or truncation violations. The main run registered all
7,100 tasks for 25 regenerations before inspecting the gate. At the gate checkpoint, exactly
1,420 tasks from regenerations 1--5 had been executed, producing 710 matched hawk/dove pairs;
no task from regenerations 6--25 had yet been executed.

The Codex application was updated during collection. Nine transient failed events were
recorded, primarily Windows paging/memory errors and nonzero CLI exits during the update. All
nine tasks were retried from SQLite and completed. The final latest-result table contains
1,420 completed gate tasks and zero failures at that checkpoint. Collection was reduced from four to two workers
after the update to remove memory pressure. Event history was preserved rather than erased.

The prespecified checkpoint passed its mechanical completeness, pairing,
source-hash, full-text, first-stage-direction, and sign-stability gates. No
prompt, task definition, or sampling field was changed after inspection. The
checkpoint did not freeze a separate calibration vintage or publication
estimate: all current econometric results are produced from the completed
25-regeneration panel with the GPT-5.5 calibration constructed by the release
master.

The five-regeneration gate above is a frozen author-side collection checkpoint. The released
package reproduces the completed 25-regeneration analysis with
`code/02_analysis/analyze_common_prior_full.R`; its machine-readable outputs include
`outputs/tables/common_prior_main_models_20260714.csv` and
`outputs/tables/common_prior_main_by_draw_20260714.csv`.

## 11. Full 25-regeneration completion

After the gate passed, regenerations 6--25 were completed under the same frozen run ID and
prompt. The final database contains 7,100 completed forecaster responses
(`71 x 25 x 2 arms x 2 forecaster types`) and 3,550 paired composite observations. This
completion record supersedes the gate-checkpoint statement that later regenerations had not
yet been executed; the checkpoint remains part of the immutable collection audit rather than
a separate reported estimand.
