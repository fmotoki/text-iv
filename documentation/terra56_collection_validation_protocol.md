# Terra 5.6 collection and validation protocol

This protocol describes implemented prospective code. It is not a collection
record, an accepted numerical release, or a new clean-room validation.
The implementation handoff records the actual limited QA and live request count.

## Code and artifact versions

Git versions source code, prompts, configuration, and documentation under
`replication/`. New raw supporting data and run artifacts live in the sibling
`replication_artifacts/` directory. `code/replication_spec.json` identifies both
roots, the release ID, every collection ID, source/input hashes, and the selected
run. Each run retains its effective specification, source snapshot, Git commit,
model metadata, and output inventory. Existing GPT-5.5 results and reference
files remain in place. The verified pre-migration archive remains under
`replication/archives/`, excluded from Git.

An unfinished run may resume only with the same effective specification and
fingerprint. A changed substantive specification or input vintage needs a new
release ID. Source lookup always uses `source_root`. Runtime artifacts resolve
inside `run_root`. The new immutable BCB snapshot is a separate recorded input
under `artifact_root/data/raw/bcb/`.

## Fixed design

The LLM is exactly `gpt-5.6-terra` with high reasoning. Its knowledge cutoff is
February 16, 2026. The sample is meetings 203 through 281, exactly 79 meetings.
Both anchored and unanchored profiles are retained. The initial expectation is
3.80 percent. Rewrites preserve the existing Portuguese prompts and five blocks.
There are 25 main draws and five dose draws. The original dose arm is not rewritten.

The selected Codex service tier is `default`, the explicit Standard setting.
The user chose it to reduce subscription allowance use. It is pinned in the
specification and recorded in response metadata. A shell speed setting cannot
silently change it. Standard configuration was verified through read-only Codex
telemetry before the bounded live test.

| Object | Planned first attempts |
| --- | ---: |
| Main rewrites | 19,750 |
| Dose rewrites | 9,875 |
| Original-path forecasts | 158 |
| Main common-prior forecasts | 7,900 |
| Dose common-prior forecasts | 4,740 |
| Dose recursive forecasts | 4,740 |
| Dose semantic scores | 2,370 |
| Dose pairwise scores | 1,975 |
| Total | 51,508 |

These are design dimensions, not completed observations. Two numeric retries
can add up to 59,250 rewrite requests. Other retries must be recorded separately.
Local lexicon scoring and embeddings do not use LLM tokens. The embedding model
and segmentation settings remain separately identified in the specification.

The common prior at meeting t is `previous_expectation` from the matching
profile's original trajectory at t. All arms and draws share that prior within
meeting and profile. Recursive paths instead carry their own arm/draw/profile
forecast forward. Task keys, chronology, source hashes, model, prompt version,
and effective configuration are validated before reuse. Dry-run results do not
satisfy real completion.

## Acquisition and calibration prerequisites

Existing minute texts are read without recollection. The extended downloader
is the sole supporting-data acquisition authority. Analyses and database imports
read frozen files offline. Snapshot manifests retain URLs, filters, retrieval
times, original evidence, and hashes.

Activity alignment uses the latest actual eligible disclosure, its reference
month, and verified release-vintage value. A scheduled date is not an actual
release. An observation date is not a publication date. Same-day ordering needs
verified times or an explicitly documented author convention. Current revised
SGS values do not substitute for missing historical vintages.

The three seasonally adjusted sector series first became public on April 11,
2025. Their backfilled history does not establish public availability before
that date. The strict full-79, three-sector calibration therefore remains blocked
where release, vintage, cutoff, or public-availability evidence is missing.
The 237-row availability audit retains every intended meeting/series key.
No unsupported meeting is dropped and no alternative control is substituted.
Changing that research specification requires an author decision before a full run.
Fiscal sensitivities keep their documented historical support. Realized-inflation
checks use only mature horizons.

The main unrestricted calibration uses all 79 meetings. The separate Focus
agreement diagnostic fits the same level equation once on meetings 203 through
271, then predicts 272 through 281 without refitting. The evaluation windows
contain five meetings each. Outcomes are same-date unsmoothed, base-zero median
Focus twelve-month-ahead IPCA expectations. Errors are prediction minus Focus,
in percentage points. Only the frozen composite reports an RMSE ratio, divided
by previous-meeting Focus persistence RMSE. A zero denominator is undefined.
Raw profile forecasts retain separate RMSE, MAE, and mean-error metrics.

Immediate framing contrasts use matched neutral common-prior outcomes. Draws
are averaged within meetings before equal weighting of the five meetings per
window. The composite contrast uses only the frozen 69-meeting profile slopes.
No five-observation HAC, Diebold-Mariano, equivalence, or post-cutoff IV test is
used. This is descriptive Focus agreement, not realized-inflation accuracy or a
blanket real-time out-of-sample claim.

## Ordered execution

`code/collection/scripts/run_terra56_collection.ps1` is the active collection
launcher. It requires `-SpecificationPath`, defaults to deterministic preparation,
and requires both `-AllowRemoteCalls` and explicit specification authorization
for live execution. The implementation task uses a separate QA specification
for a smaller representative live test. The originally planned 92-request smoke
is deferred by user instruction.

The order is frozen acquisition, audited availability, offline import, text
collection, original priors, common-prior treatments, recursive treatments,
scoring and selected embeddings, audits, exclusive candidate export, offline
calibration and analyses, reporting, independent output review, reference
acceptance, and clean-room validation. Never file-sync a DB-first dose run after
execution, discover every run directory automatically, replace a database, or
promote new results into old publication paths.

The production command file is disabled by default. The master is offline and
rejects incomplete/mixed inputs, incompatible resume markers, and unauthorized
production execution. Numerical references remain unaccepted. A missing Terra
candidate or baseline produces NOT RUN or an input failure, never PASS.

Historical GPT-5.5 protocols and `CLEAN_ROOM_VALIDATION.md` retain their original
meaning. Their success records do not validate this prospective Terra release.
