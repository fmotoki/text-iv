suppressPackageStartupMessages({
  library(DBI)
  library(RSQLite)
  library(data.table)
  library(haven)
  library(ivreg)
  library(sandwich)
  library(lmtest)
  library(jsonlite)
})

data.table::setDTthreads(1L)

root <- normalizePath(".", winslash = "/", mustWork = TRUE)
source(file.path(root, "code", "02_analysis", "reproducibility_helpers.R"), local = TRUE)
database_path <- file.path(root, "outputs", "work", "prompt_iv_replication.sqlite")
dataset_path <- file.path(root, "outputs", "work", "gpt55_calibration_data.dta")
table_dir <- file.path(root, "outputs", "tables")
dir.create(table_dir, recursive = TRUE, showWarnings = FALSE)

stamp <- "20260715"
seed <- 20260715L
bootstrap_draws <- 1999L
embedding_model <- "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
d5_source_run <- "v10_referee_d5_25draws_20260620"
dose_source_run <- "v10_referee_step3_neutral_dose_5draws_20260627"

calibration_path <- file.path(
  table_dir, paste0("referee_d6_hierarchical_dirichlet_", stamp, ".csv")
)
if (!file.exists(calibration_path)) {
  stop("Missing hierarchical-calibration output: ", calibration_path)
}
calibration <- fread(calibration_path)
anchored_weight <- calibration[parameter == "Anchored weight", mean]
unanchored_weight <- calibration[parameter == "Unanchored weight", mean]
if (length(anchored_weight) != 1L || length(unanchored_weight) != 1L ||
    any(!is.finite(c(anchored_weight, unanchored_weight))) ||
    abs(anchored_weight + unanchored_weight - 1) > 1e-10) {
  stop("Hierarchical posterior-mean calibration weights are invalid")
}

con <- dbConnect(SQLite(), database_path)
on.exit(dbDisconnect(con), add = TRUE)
dbExecute(con, "PRAGMA busy_timeout = 60000")

cluster_term <- function(model, term, cluster) {
  vc <- vcovCL(model, cluster = cluster, type = "HC1")
  estimate <- unname(coef(model)[[term]])
  standard_error <- sqrt(unname(vc[term, term]))
  degrees_freedom <- uniqueN(cluster) - 1L
  data.table(
    estimate = estimate,
    std_error = standard_error,
    statistic = estimate / standard_error,
    p_value = 2 * pt(
      abs(estimate / standard_error),
      df = degrees_freedom,
      lower.tail = FALSE
    )
  )
}

estimate_iv <- function(data, outcome, measure, instrument, label, outcome_label) {
  working <- data[, .(
    meeting_number,
    pair_id,
    outcome_value = get(outcome),
    measure_value = get(measure),
    instrument_value = get(instrument)
  )]
  working <- working[complete.cases(working)]
  working[, `:=`(
    outcome_within = outcome_value - mean(outcome_value),
    measure_within = measure_value - mean(measure_value),
    instrument_within = instrument_value - mean(instrument_value)
  ), by = pair_id]

  reduced_form <- lm(outcome_within ~ instrument_within, data = working)
  first_stage <- lm(measure_within ~ instrument_within, data = working)
  iv <- ivreg(
    outcome_within ~ measure_within | instrument_within,
    data = working
  )
  rf <- cluster_term(reduced_form, "instrument_within", working$meeting_number)
  fs <- cluster_term(first_stage, "instrument_within", working$meeting_number)
  iv_row <- cluster_term(iv, "measure_within", working$meeting_number)
  measure_sd <- sd(working$measure_value)

  data.table(
    sample = label,
    outcome = outcome_label,
    tone_measure = measure,
    reduced_form = rf$estimate,
    reduced_form_se = rf$std_error,
    first_stage = fs$estimate,
    first_stage_se = fs$std_error,
    first_stage_f = fs$statistic^2,
    iv_estimate = iv_row$estimate,
    iv_std_error = iv_row$std_error,
    iv_p_value = iv_row$p_value,
    measure_sd = measure_sd,
    iv_effect_per_measure_sd = iv_row$estimate * measure_sd,
    observations = nobs(iv),
    pair_fixed_effects = uniqueN(working$pair_id),
    meeting_clusters = uniqueN(working$meeting_number)
  )
}

baseline <- as.data.table(read_dta(dataset_path))
mixture_model <- lm(
  inflacao12m ~ forecast_anchored_gpt55 + forecast_unanchored_gpt55 +
    wc_orig + ibcbr_agro + ibcbr_service + ibcbr_industry,
  data = baseline
)
mixture_weights <- coef(mixture_model)

# Main 25-draw panel and embedding matrix.
main_agents <- as.data.table(dbGetQuery(
  con,
  paste(
    "SELECT meeting_number, corpus_iteration AS iteration, source_role,",
    "forecaster_type, forecast_revision",
    "FROM agent_results_latest",
    "WHERE run_id = 'common_prior_main_25draws_v1_20260712'",
    "AND status = 'completed'"
  )
))
main_outcomes <- dcast(
  main_agents,
  meeting_number + iteration + source_role ~ forecaster_type,
  value.var = "forecast_revision"
)
main_outcomes[, `:=`(
  current_unrestricted =
    unname(mixture_weights[["forecast_anchored_gpt55"]]) * anchored +
      unname(mixture_weights[["forecast_unanchored_gpt55"]]) * unanchored,
  hierarchical_simplex =
    anchored_weight * anchored + unanchored_weight * unanchored,
  equal_weight = 0.5 * anchored + 0.5 * unanchored,
  hawk = as.integer(source_role == "hawk"),
  pair_id = sprintf("%d::%d", meeting_number, iteration)
)]

main_tone <- as.data.table(dbGetQuery(
  con,
  paste(
    "SELECT meeting_number, iteration, role, 100.0 * score AS lexicon_tone_pp",
    "FROM tone_scores",
    sprintf("WHERE run_id = '%s'", d5_source_run),
    "AND role IN ('hawk_text', 'dove_text')"
  )
))
main_tone[, source_role := fifelse(role == "hawk_text", "hawk", "dove")]
main_tone[, role := NULL]

embedding_rows <- as.data.table(dbGetQuery(
  con,
  paste(
    "SELECT meeting_number, iteration, source_role, text_hash,",
    "sentence_total_count, sentence_sample_count, subword_count, embedding_blob",
    "FROM referee_text_embeddings",
    sprintf("WHERE run_id = '%s'", d5_source_run),
    "AND source_role IN ('hawk', 'dove') AND model_id = ?"
  ),
  params = list(embedding_model)
))
if (nrow(embedding_rows) != 3550L) {
  stop(sprintf("Expected 3,550 D5 treatment embeddings, found %d", nrow(embedding_rows)))
}

main_panel <- merge(
  merge(
    main_outcomes,
    main_tone,
    by = c("meeting_number", "iteration", "source_role")
  ),
  embedding_rows,
  by = c("meeting_number", "iteration", "source_role")
)
setorder(main_panel, meeting_number, iteration, source_role)
embedding_matrix <- t(vapply(
  main_panel$embedding_blob,
  function(blob) readBin(
    blob,
    what = "numeric",
    n = 384L,
    size = 4L,
    endian = "little"
  ),
  numeric(384L)
))
main_panel[, embedding_blob := NULL]

# Leave-one-meeting-out hawk-minus-dove direction.
main_panel[, embedding_row := seq_len(.N)]
hawk_rows <- main_panel[source_role == "hawk"]
dove_rows <- main_panel[source_role == "dove"]
setkey(hawk_rows, meeting_number, iteration)
setkey(dove_rows, meeting_number, iteration)
if (!identical(
  hawk_rows[, .(meeting_number, iteration)],
  dove_rows[, .(meeting_number, iteration)]
)) {
  stop("Hawk and dove embedding pairs are not aligned")
}
pair_directions <-
  embedding_matrix[hawk_rows$embedding_row, , drop = FALSE] -
  embedding_matrix[dove_rows$embedding_row, , drop = FALSE]
meeting_direction_sums <- rowsum(
  pair_directions,
  group = hawk_rows$meeting_number,
  reorder = TRUE
)
global_direction <- colSums(pair_directions)
direction_by_meeting <- t(vapply(
  rownames(meeting_direction_sums),
  function(meeting) {
    direction <- global_direction - meeting_direction_sums[meeting, ]
    direction / sqrt(sum(direction^2))
  },
  numeric(ncol(embedding_matrix))
))
direction_meetings <- as.integer(rownames(direction_by_meeting))
directions_for_rows <- direction_by_meeting[
  match(main_panel$meeting_number, direction_meetings),
  ,
  drop = FALSE
]
main_panel[, embedding_tone_loo := rowSums(embedding_matrix * directions_for_rows)]
if (main_panel[, mean(embedding_tone_loo[source_role == "hawk"]) -
    mean(embedding_tone_loo[source_role == "dove"])] < 0) {
  main_panel[, embedding_tone_loo := -embedding_tone_loo]
}

# Residualize lexicon tone against document-embedding PCs after pair demeaning.
embedding_pca <- prcomp(
  embedding_matrix,
  center = TRUE,
  scale. = FALSE,
  rank. = 50L
)
explained <- embedding_pca$sdev^2 / sum(embedding_pca$sdev^2)
pc_count_candidate <- which(cumsum(explained) >= 0.90)[1L]
pc_count <- if (is.na(pc_count_candidate)) 50L else min(50L, max(10L, pc_count_candidate))
pc_columns <- paste0("embedding_pc_", seq_len(pc_count))
main_panel[, (pc_columns) := as.data.table(embedding_pca$x[, seq_len(pc_count), drop = FALSE])]
main_panel[, `:=`(
  lexicon_tone_within = lexicon_tone_pp - mean(lexicon_tone_pp),
  hawk_within = hawk - mean(hawk)
), by = pair_id]
pc_within_columns <- paste0(pc_columns, "_within")
for (index in seq_along(pc_columns)) {
  source_column <- pc_columns[[index]]
  within_column <- pc_within_columns[[index]]
  main_panel[, (within_column) := get(source_column) - mean(get(source_column)), by = pair_id]
}

raw_embedding_formula <- reformulate(pc_within_columns, response = "lexicon_tone_within")
raw_embedding_fit <- lm(raw_embedding_formula, data = main_panel)
main_panel[, lexicon_t_perp_raw_embedding := residuals(raw_embedding_fit)]

content_columns <- paste0(pc_columns, "_z_orthogonal")
for (index in seq_along(pc_within_columns)) {
  pc_data <- data.table(
    pc = main_panel[[pc_within_columns[[index]]]],
    z = main_panel$hawk_within
  )
  content_columns[[index]] <- paste0(pc_columns[[index]], "_z_orthogonal")
  main_panel[, (content_columns[[index]]) := residuals(lm(pc ~ z, data = pc_data))]
}
content_formula <- reformulate(content_columns, response = "lexicon_tone_within")
content_fit <- lm(content_formula, data = main_panel)
main_panel[, lexicon_t_perp_content_embedding := residuals(content_fit)]

main_iv_rows <- rbindlist(lapply(
  c("current_unrestricted", "hierarchical_simplex"),
  function(outcome) {
    rbindlist(lapply(
      c(
        "lexicon_tone_pp", "embedding_tone_loo",
        "lexicon_t_perp_raw_embedding", "lexicon_t_perp_content_embedding"
      ),
      function(measure) estimate_iv(
        main_panel,
        outcome = outcome,
        measure = measure,
        instrument = "hawk",
        label = "D5 common-prior 25-draw binary design",
        outcome_label = outcome
      )
    ))
  }
))

# Ordered dose panel with corrected blinded semantic and pairwise scores.
dose_agents <- as.data.table(dbGetQuery(
  con,
  paste(
    "SELECT meeting_number, corpus_iteration AS iteration, source_role,",
    "forecaster_type, forecast_revision",
    "FROM agent_results_latest",
    "WHERE run_id = 'common_prior_dose_6arm_5draws_v1_20260713'",
    "AND status = 'completed'"
  )
))
dose_outcomes <- dcast(
  dose_agents,
  meeting_number + iteration + source_role ~ forecaster_type,
  value.var = "forecast_revision"
)
dose_scores <- as.data.table(dbGetQuery(
  con,
  paste(
    "SELECT meeting_number, iteration, text_role AS source_role, assigned_dose,",
    "100.0 * lexicon_score AS lexicon_tone_pp, semantic_score, pairwise_score",
    "FROM realized_dose_scores",
    sprintf("WHERE run_id = '%s'", dose_source_run)
  )
))
dose_panel <- merge(
  dose_outcomes,
  dose_scores,
  by = c("meeting_number", "iteration", "source_role")
)
dose_panel[, `:=`(
  current_unrestricted =
    unname(mixture_weights[["forecast_anchored_gpt55"]]) * anchored +
      unname(mixture_weights[["forecast_unanchored_gpt55"]]) * unanchored,
  hierarchical_simplex =
    anchored_weight * anchored + unanchored_weight * unanchored,
  pair_id = sprintf("%d::%d", meeting_number, iteration)
)]
dose_generated <- dose_panel[source_role != "original"]
if (nrow(dose_generated) != 1775L || anyNA(dose_generated$semantic_score)) {
  stop("Ordered dose panel is incomplete after semantic-score migration")
}

dose_iv_rows <- rbindlist(lapply(
  c("current_unrestricted", "hierarchical_simplex"),
  function(outcome) {
    rbindlist(lapply(
      c("lexicon_tone_pp", "semantic_score", "pairwise_score"),
      function(measure) estimate_iv(
        dose_generated,
        outcome = outcome,
        measure = measure,
        instrument = "assigned_dose",
        label = "Common-prior five-level assigned-dose design",
        outcome_label = outcome
      )
    ))
  }
))
iv_results <- rbindlist(list(main_iv_rows, dose_iv_rows), use.names = TRUE)

# Cluster-robust content-preservation summaries from SQLite pair metrics.
embedding_metrics <- as.data.table(dbGetQuery(
  con,
  paste(
    "SELECT run_id, meeting_number, iteration, source_role,",
    "document_cosine_similarity,",
    "0.5 * (rewrite_to_original_sentence_similarity +",
    "original_to_rewrite_sentence_similarity) AS symmetric_sentence_similarity,",
    "sentence_similarity_p05, sentence_share_below_080,",
    "subword_jensen_shannon, subword_count_ratio, sentence_count_ratio",
    "FROM referee_embedding_pair_metrics WHERE model_id = ?"
  ),
  params = list(embedding_model)
))
metric_columns <- c(
  "document_cosine_similarity", "symmetric_sentence_similarity",
  "sentence_similarity_p05", "sentence_share_below_080",
  "subword_jensen_shannon", "subword_count_ratio", "sentence_count_ratio"
)
embedding_groups <- unique(embedding_metrics[, .(run_id, source_role)])
embedding_summary <- rbindlist(lapply(
  seq_len(nrow(embedding_groups)),
  function(group_index) {
    group_row <- embedding_groups[group_index]
    group_data <- embedding_metrics[
      run_id == group_row$run_id & source_role == group_row$source_role
    ]
    rbindlist(lapply(metric_columns, function(metric) {
      model <- lm(reformulate("1", response = metric), data = group_data)
      vc <- vcovCL(model, cluster = group_data$meeting_number, type = "HC1")
      estimate <- unname(coef(model)[[1L]])
      standard_error <- sqrt(unname(vc[1L, 1L]))
      data.table(
        run_id = group_row$run_id,
        source_role = group_row$source_role,
        metric = metric,
        estimate = estimate,
        cluster_se = standard_error,
        ci_low = estimate - 1.96 * standard_error,
        ci_high = estimate + 1.96 * standard_error,
        observations = nobs(model),
        meeting_clusters = uniqueN(group_data$meeting_number)
      )
    }))
  }
))

weighted_correlation <- function(left, right, weights) {
  total <- sum(weights)
  mean_left <- sum(weights * left) / total
  mean_right <- sum(weights * right) / total
  covariance <- sum(weights * (left - mean_left) * (right - mean_right))
  covariance / sqrt(
    sum(weights * (left - mean_left)^2) *
      sum(weights * (right - mean_right)^2)
  )
}

set.seed(seed)
correlation_rows <- list()
correlation_index <- 1L
for (sample_name in c("All six roles", "Five generated roles")) {
  correlation_data <- if (sample_name == "All six roles") {
    dose_panel
  } else {
    dose_generated
  }
  meetings <- sort(unique(correlation_data$meeting_number))
  for (pair in list(
    c("lexicon_tone_pp", "semantic_score"),
    c("lexicon_tone_pp", "pairwise_score"),
    c("semantic_score", "pairwise_score")
  )) {
    left <- pair[[1L]]
    right <- pair[[2L]]
    point <- cor(
      correlation_data[[left]],
      correlation_data[[right]],
      method = "spearman",
      use = "complete.obs"
    )
    ranks_left <- rank(correlation_data[[left]], ties.method = "average")
    ranks_right <- rank(correlation_data[[right]], ties.method = "average")
    draws <- numeric(bootstrap_draws)
    for (draw in seq_len(bootstrap_draws)) {
      sampled <- sample(meetings, length(meetings), replace = TRUE)
      meeting_weights <- tabulate(
        match(sampled, meetings),
        nbins = length(meetings)
      )
      row_weights <- meeting_weights[
        match(correlation_data$meeting_number, meetings)
      ]
      draws[[draw]] <- weighted_correlation(ranks_left, ranks_right, row_weights)
    }
    correlation_rows[[correlation_index]] <- data.table(
      sample = sample_name,
      measure_left = left,
      measure_right = right,
      spearman = point,
      cluster_bootstrap_ci_low = quantile(draws, 0.025),
      cluster_bootstrap_ci_high = quantile(draws, 0.975),
      observations = nrow(correlation_data),
      meeting_clusters = uniqueN(correlation_data$meeting_number),
      bootstrap_draws = bootstrap_draws
    )
    correlation_index <- correlation_index + 1L
  }
}
tone_correlations <- rbindlist(correlation_rows)

pca_diagnostics <- data.table(
  embedding_model = embedding_model,
  embedding_dimensions = ncol(embedding_matrix),
  pcs_used = pc_count,
  cumulative_variance_explained = sum(explained[seq_len(pc_count)]),
  raw_t_perp_first_stage_correlation = cor(
    main_panel$lexicon_t_perp_raw_embedding,
    main_panel$hawk
  ),
  content_t_perp_first_stage_correlation = cor(
    main_panel$lexicon_t_perp_content_embedding,
    main_panel$hawk
  )
)

dbWithTransaction(con, {
  dbWriteTable(
    con,
    "referee_tone_measure_iv",
    as.data.frame(iv_results),
    overwrite = TRUE
  )
  dbWriteTable(
    con,
    "referee_embedding_preservation_summary",
    as.data.frame(embedding_summary),
    overwrite = TRUE
  )
  dbWriteTable(
    con,
    "referee_tone_measure_correlations",
    as.data.frame(tone_correlations),
    overwrite = TRUE
  )
})

write_table <- function(data, name) {
  path <- file.path(table_dir, paste0(name, "_", stamp, ".csv"))
  fwrite(data, path)
  path
}

scatter_data <- dose_panel[, .(
  meeting_number, iteration, source_role, assigned_dose,
  lexicon_tone_pp, semantic_score, pairwise_score
)]
outputs <- list(
  iv = write_table(iv_results, "referee_d16_alternative_tone_iv"),
  preservation = write_table(
    embedding_summary,
    "referee_d16_embedding_preservation"
  ),
  correlations = write_table(
    tone_correlations,
    "referee_d16_tone_measure_correlations"
  ),
  scatter = write_table(scatter_data, "referee_d16_tone_scatter_data"),
  pca = write_table(pca_diagnostics, "referee_d16_embedding_pca_diagnostics")
)

summary_path <- file.path(
  table_dir,
  paste0("referee_d16_embedding_tone_", stamp, ".json")
)
write_json(
  release_metadata(list(
    generated_at = format(Sys.time(), tz = "UTC", usetz = TRUE),
    implementation = list(
      econometrics_language = "R",
      packages = c("ivreg", "sandwich", "lmtest", "data.table"),
      embedding_model = embedding_model,
      embedding_source = "SQLite BLOBs produced by fastembed 0.8.0",
      cluster_level = "meeting"
    ),
    pca_diagnostics = pca_diagnostics,
    iv_results = iv_results,
    preservation_summary = embedding_summary,
    tone_measure_correlations = tone_correlations,
    outputs = outputs
  ), root, stamp),
  summary_path,
  auto_unbox = TRUE,
  pretty = TRUE,
  digits = 16
)

cat(sprintf("Completed D16 embedding and alternative-tone analysis: %s\n", summary_path))
