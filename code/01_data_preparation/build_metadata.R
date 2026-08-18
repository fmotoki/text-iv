suppressPackageStartupMessages({
  library(DBI)
  library(RSQLite)
  library(data.table)
  library(haven)
  library(digest)
})

script_arg <- grep("^--file=", commandArgs(trailingOnly = FALSE), value = TRUE)
script_path <- normalizePath(sub("^--file=", "", script_arg[[1L]]), winslash = "/")
root <- normalizePath(file.path(dirname(script_path), "..", ".."), winslash = "/")
setwd(root)

metadata_dir <- file.path(root, "data", "metadata")
dir.create(metadata_dir, recursive = TRUE, showWarnings = FALSE)
database_path <- file.path(root, "data", "generated", "prompt_iv_replication_source.sqlite")
lookup <- function(values, key, default) {
  if (key %in% names(values)) unname(values[[key]]) else default
}

table_descriptions <- c(
  agent_results_latest = "Frozen latest successful synthetic-forecaster result for each selected task.",
  agent_tasks = "Task design, treatment arm, prior, prompt version, and source-text linkage for selected forecaster runs.",
  blinded_pairwise_scores = "Blinded pairwise tone-ranking scores used in the dose validation.",
  blinded_pairwise_tasks = "Anonymized pairwise tone-ranking tasks.",
  blinded_semantic_scores = "Blinded semantic hawkishness scores for generated and original texts.",
  blinded_semantic_tasks = "Anonymized semantic-scoring tasks and assigned dose labels.",
  corpus_texts = "Frozen five-draw six-arm counterfactual text corpus, including the full generated text.",
  external_bcb_sgs = "Public Banco Central do Brasil SGS time series used in external validation.",
  external_focus_statistics = "Public aggregate and Top-5 Focus statistics from BCB Olinda endpoints.",
  external_source_manifest = "Endpoint-level provenance and retrieval status for public external data.",
  metadata = "Replication database build metadata.",
  realized_dose_scores = "Lexicon, blinded-semantic, and blinded-pairwise dose measures by meeting, draw, and text arm.",
  referee_embedding_manifest = "Embedding model and document-segmentation settings.",
  referee_embedding_pair_metrics = "Document, sentence, and token-distribution preservation metrics.",
  referee_human_audit_key = "Blinded linkage key retained for audit traceability; no human respondent data.",
  referee_human_audit_ratings = "Human factual-audit ratings table; intentionally empty because ratings were not collected.",
  referee_human_audit_tasks = "Thirty blinded text pairs prepared for a future human factual audit.",
  referee_text_embeddings = "Frozen sentence/document embeddings for the 25-regeneration hawkish and dovish rewrite corpus.",
  rewrite_results_latest = "Thin completion and retry inventory for the 17,750 rewrite tasks.",
  runs = "Design metadata and expected dimensions for the six frozen collection runs.",
  tone_scores = "Lexicon tone scores and token counts for original and counterfactual texts."
)

column_descriptions <- c(
  run_id = "Stable collection-run identifier.",
  task_id = "Stable task identifier within a run.",
  meeting_number = "Sequential Copom meeting number.",
  iteration = "Repeated full-report regeneration index; not a controlled seed draw.",
  corpus_iteration = "Independent source-text regeneration draw.",
  pair_fe = "Meeting-by-regeneration fixed-effect identifier.",
  pair_id = "Matched comparison or fixed-effect identifier.",
  source_role = "Textual treatment arm or source-text role.",
  text_role = "Original or generated dose-response text arm.",
  assigned_dose = "Ordered treatment dose: -2 high dove through +2 high hawk; original is not assigned a dose.",
  score = "Hawk-minus-dove lexicon share on the stored scale.",
  tone_pp = "Tone score in percentage points.",
  status = "Task, endpoint, or result status.",
  model = "Model identifier recorded at collection.",
  reasoning_effort = "Provider reasoning-effort setting requested at collection.",
  prompt_version = "Versioned prompt-family identifier.",
  previous_expectation = "Pre-treatment prior supplied to the synthetic forecaster, in percentage points.",
  new_expectation = "Synthetic twelve-month-ahead inflation forecast level, in percentage points.",
  forecast_revision = "New expectation minus the supplied prior, in percentage points.",
  perceived_tone = "Tone perceived by the synthetic forecaster.",
  initial_expectation = "Initial forecast state used to start a recursive path.",
  fixed_prior = "Pre-treatment prior held common across paired textual arms.",
  text_hash = "SHA-256 content identifier for a text.",
  source_text_hash = "SHA-256 identifier for the supplied source text.",
  raw_json = "Original structured task or response payload retained for audit.",
  raw_response_hash = "SHA-256 identifier for the raw model response.",
  source_url = "Public source endpoint used for collection.",
  fetched_at = "UTC retrieval timestamp.",
  date = "Observation date.",
  value = "Series value as published by the source.",
  mean = "Published or estimated arithmetic mean.",
  median = "Published median.",
  sd = "Published or estimated standard deviation.",
  word_count = "Whitespace-token word count.",
  hawk_count = "Count of hawkish lexicon matches.",
  dove_count = "Count of dovish lexicon matches.",
  lexicon_score = "Hawk-minus-dove word share.",
  semantic_score = "Blinded semantic hawkishness score.",
  pairwise_score = "Blinded pairwise-ranking score.",
  embedding_blob = "Serialized numeric embedding vector stored as a SQLite binary object.",
  document_cosine_similarity = "Cosine similarity between a rewrite and its original minute.",
  migrated_at = "UTC timestamp at which a source result was inserted into SQLite."
)

connection <- dbConnect(SQLite(), database_path)
on.exit(dbDisconnect(connection), add = TRUE)
tables <- dbGetQuery(
  connection,
  "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
)$name

table_rows <- rbindlist(lapply(tables, function(table) {
  data.table(
    table = table,
    rows = dbGetQuery(connection, sprintf('SELECT COUNT(*) AS n FROM "%s"', table))$n,
    description = lookup(table_descriptions, table, "Frozen replication database table.")
  )
}))
fwrite(table_rows, file.path(metadata_dir, "database_tables.csv"))

dictionary <- rbindlist(lapply(tables, function(current_table) {
  info <- as.data.table(dbGetQuery(
    connection,
    sprintf('PRAGMA table_info("%s")', current_table)
  ))
  row_count <- table_rows[table == current_table, rows][[1L]]
  info[, .(
    table = current_table,
    table_rows = row_count,
    variable = name,
    sqlite_type = type,
    not_null = as.logical(notnull),
    primary_key = as.logical(pk),
    description = vapply(name, function(variable) {
      lookup(
        column_descriptions,
        variable,
        paste0("Field retained from production table ", current_table, ".")
      )
    }, character(1L))
  )]
}))
fwrite(dictionary, file.path(metadata_dir, "database_data_dictionary.csv"))

fwrite(
  as.data.table(dbReadTable(connection, "external_source_manifest")),
  file.path(metadata_dir, "external_source_manifest.csv")
)
fwrite(
  as.data.table(dbGetQuery(
    connection,
    paste(
      "SELECT run_id, first_meeting, last_meeting, rewrite_tasks_total,",
      "agent_tasks_total, corpus_rows, tone_rows, metadata_json FROM runs ORDER BY run_id"
    )
  )),
  file.path(metadata_dir, "collection_run_inventory.csv")
)

analysis_descriptions <- c(
  inflacao12m = "Median Focus IPCA expectation twelve months ahead, percent.",
  date_daily = "Copom meeting date.",
  reuniao = "Sequential Copom meeting number, 203 through 273.",
  ibcbr_agro = "Agriculture component of IBC-Br used as a calibration fundamental.",
  ibcbr_industry = "Industry component of IBC-Br used as a calibration fundamental.",
  ibcbr_service = "Services component of IBC-Br used as a calibration fundamental.",
  forecast_anchored_gpt55 = "GPT-5.5 anchored-profile forecast after reading the original minute.",
  forecast_unanchored_gpt55 = "GPT-5.5 unanchored-profile forecast after reading the original minute.",
  score_orig = "Original-minute hawkish-minus-dovish lexicon score in percentage points.",
  wc_orig = "Original-minute word count.",
  l_inflacao12m = "One-meeting lag of the twelve-month-ahead Focus IPCA expectation."
)

dta <- read_dta(file.path(root, "outputs", "work", "gpt55_calibration_data.dta"))
dta_dictionary <- rbindlist(lapply(names(dta), function(variable) {
  label <- attr(dta[[variable]], "label")
  data.table(
    file = "outputs/work/gpt55_calibration_data.dta",
    rows = nrow(dta),
    variable = variable,
    storage_class = class(dta[[variable]])[[1L]],
    source_label = if (is.null(label)) "" else as.character(label),
    description = lookup(analysis_descriptions, variable, "Variable in the generated GPT-5.5 calibration dataset.")
  )
}))

external_descriptions <- c(
  reuniao = "Copom meeting number.",
  rtn_pub_window_5d = "Indicator for a Copom meeting within five days of an RTN release.",
  cds_series_available = "Binary availability flag for the historical public CDS page; no CDS level is redistributed."
)

csv_dictionary <- function(path, descriptions, default_description) {
  data <- fread(file.path(root, path), nrows = 100000L)
  rbindlist(lapply(names(data), function(variable) {
    data.table(
      file = gsub("\\\\", "/", path),
      rows = nrow(data),
      variable = variable,
      storage_class = class(data[[variable]])[[1L]],
      source_label = "",
      description = lookup(descriptions, variable, default_description)
    )
  }))
}

external_dictionary <- csv_dictionary(
  "data/analysis/fiscal_sample_flags_by_meeting_20260630.csv",
  c(analysis_descriptions, external_descriptions),
  "Meeting-level sample flag used in the fiscal-news sensitivity analysis."
)
dose_dictionary <- csv_dictionary(
  "data/analysis/realized_dose_dataset.csv",
  c(column_descriptions, analysis_descriptions),
  "Machine-readable dose-response field; suffixes identify comparisons with the neutral or original text."
)
fwrite(
  rbindlist(list(dta_dictionary, external_dictionary, dose_dictionary), fill = TRUE),
  file.path(metadata_dir, "analysis_data_dictionary.csv")
)

sources <- data.table(
  source_id = c(
    "BCB_COPOM_MINUTES", "BCB_FOCUS", "BCB_SGS",
    "BCB_DEPEC_ACTIVITY_CONTROLS", "WGB_CDS", "IPEADATA_EMBI",
    "TESOURO_DIRETO", "TESOURO_RTN",
    "AUTHOR_LLM_TEXTS", "AUTHOR_LLM_FORECASTS"
  ),
  data_component = c(
    "Original policy text", "Survey expectations", "Macroeconomic series",
    "Calibration activity controls", "Sovereign-risk control",
    "Sovereign-risk control", "Real-rate proxy",
    "Fiscal-news dates", "Counterfactual policy texts", "Synthetic outcomes"
  ),
  provider = c(
    "Banco Central do Brasil", "Banco Central do Brasil", "Banco Central do Brasil",
    "Banco Central do Brasil/Depec", "World Government Bonds", "Ipea",
    "Tesouro Nacional", "Tesouro Nacional",
    "Authors and GPT-5.5", "Authors and GPT-5.5"
  ),
  title = c(
    "Minutes of Copom meetings", "Focus aggregate and Top-5 expectation statistics",
    "SGS time series", "Frozen monthly activity-component index extract",
    "Brazil five-year CDS historical data",
    "Ipeadata EMBI Brazil series", "Tesouro Direto offered-bond rates",
    "Resultado do Tesouro Nacional release pages",
    "Prompt-IV frozen counterfactual corpus",
    "Frozen synthetic-forecaster task and result panels"
  ),
  url = c(
    "https://www.bcb.gov.br/publicacoes/atascopom",
    "https://olinda.bcb.gov.br/olinda/servico/Expectativas/versao/v1/odata",
    "https://api.bcb.gov.br/dados/serie/bcdata.sgs.433/dados",
    "not_preserved_in_source_workbook",
    "https://www.worldgovernmentbonds.com/cds-historical-data/brazil/5-years/",
    "http://www.ipeadata.gov.br/api/odata4/Metadados('JPM366_EMBI366')",
    "https://www.tesourotransparente.gov.br/ckan/dataset/taxas-dos-titulos-ofertados-pelo-tesouro-direto",
    "https://www.tesourotransparente.gov.br/publicacoes/boletim-resultado-do-tesouro-nacional-rtn/",
    "not_applicable", "not_applicable"
  ),
  accessed = c(
    "2026-07-15", "2026-07-15", "2026-07-15",
    "not preserved; workbook modified 2025-12-03", "2026-06-30",
    "2026-06-30", "2026-06-30", "2026-06-30", "2026-06-20", "2026-07-14"
  ),
  coverage = c(
    "Meetings 203-273", "Meeting-aligned dates in the analysis sample",
    "Series and dates documented in external_source_manifest.csv",
    "Meetings 203-273; agriculture, industry, and services index levels",
    "67 of 71 meetings", "71 meetings", "71 meetings", "42 publications",
    "17750 rewrite tasks", "15762 selected completed tasks"
  ),
  included_file = c(
    "data/raw/copom_minutes/", "data/generated/prompt_iv_replication_source.sqlite",
    "data/generated/prompt_iv_replication_source.sqlite",
    "data/raw/calibration_controls/copom_meeting_activity_controls.csv",
    "data/analysis/fiscal_sample_flags_by_meeting_20260630.csv (coverage flag only; no CDS values)",
    "not distributed; not read by the active master script",
    "not distributed; not read by the active master script",
    "data/analysis/fiscal_sample_flags_by_meeting_20260630.csv",
    "data/generated/prompt_iv_replication_source.sqlite",
    "data/generated/prompt_iv_replication_source.sqlite"
  ),
  access_conditions = c(
    "Public website; no authentication", "Public API; no authentication",
    "Public API; no authentication",
    "Frozen extract; exact series IDs, adjustment flags, and access date were not preserved",
    "Values not redistributed; binary coverage metadata only",
    "Values not redistributed; source series is inactive", "Public CKAN dataset; no included values",
    "Public website; derived publication-window flag included",
    "Included in package; regeneration requires provider access",
    "Included in package; regeneration requires provider access"
  ),
  license_note = c(
    rep("ODbL 1.0; attribution and share-alike terms retained", 3L),
    "Third-party BCB/Depec-derived extract; attribution and provider rights retained",
    "No redistribution license identified; source values excluded",
    "No redistribution license for the third-party J.P. Morgan series identified; values excluded",
    rep("ODbL 1.0; attribution and share-alike terms retained", 2L),
    rep("Author-generated data released under LICENSE_DATA", 2L)
  ),
  redistribution_status = c(
    rep("included_under_ODbL", 3L),
    "included_frozen_extract_with_provenance_limit",
    "values_excluded_rights_unconfirmed",
    "values_excluded_rights_unconfirmed",
    rep("included_under_ODbL", 2L),
    rep("included_author_generated", 2L)
  ),
  citation = c(
    "Banco Central do Brasil. Minutes of Copom meetings.",
    "Banco Central do Brasil. Expectativas do Mercado (Focus), Olinda API.",
    "Banco Central do Brasil. Sistema Gerenciador de Series Temporais.",
    "Banco Central do Brasil/Depec. Monthly activity-component index extract.",
    "World Government Bonds. Brazil 5 Years CDS historical data.",
    "Ipea. Ipeadata EMBI Brazil series JPM366_EMBI366.",
    "Tesouro Nacional. Taxas dos Titulos Ofertados pelo Tesouro Direto.",
    "Tesouro Nacional. Resultado do Tesouro Nacional.",
    "Barros Jr.; Motoki; Pinho Neto; and Rangel. Prompt-IV counterfactual corpus.",
    "Barros Jr.; Motoki; Pinho Neto; and Rangel. Prompt-IV synthetic-forecaster panels."
  )
)
fwrite(sources, file.path(metadata_dir, "data_sources.csv"), quote = TRUE)

input_paths <- c(
  "data/generated/prompt_iv_replication_source.sqlite",
  "data/analysis/fiscal_sample_flags_by_meeting_20260630.csv",
  "data/analysis/realized_dose_dataset.csv",
  gsub("\\\\", "/", list.files("data/raw/calibration_controls", full.names = TRUE)),
  gsub("\\\\", "/", list.files("data/raw/copom_minutes", full.names = TRUE)),
  character()
)
input_rows <- rbindlist(lapply(input_paths, function(path) {
  data.table(
    path = path,
    size_bytes = file.info(path)$size,
    sha256 = digest(path, algo = "sha256", file = TRUE, serialize = FALSE)
  )
}))
fwrite(input_rows, file.path(metadata_dir, "input_checksums.csv"))

reference_paths <- gsub("\\\\", "/", list.files("reference", recursive = TRUE, full.names = TRUE))
reference_rows <- rbindlist(lapply(reference_paths, function(path) {
  data.table(
    path = path,
    size_bytes = file.info(path)$size,
    sha256 = digest(path, algo = "sha256", file = TRUE, serialize = FALSE)
  )
}))
fwrite(reference_rows, file.path(metadata_dir, "reference_checksums.csv"))

cat(sprintf(
  "Metadata built: %d database fields, %d frozen inputs, %d reference artifacts\n",
  nrow(dictionary), nrow(input_rows), nrow(reference_rows)
))
