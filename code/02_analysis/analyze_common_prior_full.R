suppressPackageStartupMessages({
  library(DBI)
  library(RSQLite)
  library(data.table)
  library(fixest)
  library(haven)
  library(ggplot2)
  library(scales)
  library(jsonlite)
  library(ivreg)
  library(sandwich)
  library(lmtest)
  library(car)
  library(ivmodel)
  library(boot)
  library(patchwork)
})

fixest::setFixest_nthreads(1L)
data.table::setDTthreads(1L)

parse_args <- function(defaults) {
  args <- commandArgs(trailingOnly = TRUE)
  out <- defaults
  i <- 1L
  while (i <= length(args)) {
    key <- args[[i]]
    if (!startsWith(key, "--")) {
      i <- i + 1L
      next
    }
    key <- sub("^--", "", key)
    if (i == length(args) || startsWith(args[[i + 1L]], "--")) {
      out[[key]] <- TRUE
      i <- i + 1L
    } else {
      out[[key]] <- args[[i + 1L]]
      i <- i + 2L
    }
  }
  out
}

args <- parse_args(list(
  root = getwd(),
  database = file.path("outputs", "work", "prompt_iv_replication.sqlite"),
  main_run_id = "common_prior_main_25draws_v1_20260712",
  dose_run_id = "common_prior_dose_6arm_5draws_v1_20260713",
  main_source_run_id = "v10_referee_d5_25draws_20260620",
  dose_source_run_id = "v10_referee_step3_neutral_dose_5draws_20260627",
  dataset = file.path("outputs", "work", "gpt55_calibration_data.dta"),
  external_controls = file.path(
    "data", "analysis", "fiscal_sample_flags_by_meeting_20260630.csv"
  ),
  bootstrap_reps = "999",
  bootstrap_seed = "20260714",
  stamp = "20260714"
))

root <- normalizePath(args$root, winslash = "/", mustWork = TRUE)
source(file.path(root, "code", "02_analysis", "reproducibility_helpers.R"), local = TRUE)
database_path <- normalizePath(file.path(root, args$database), winslash = "/", mustWork = TRUE)
dataset_path <- normalizePath(file.path(root, args$dataset), winslash = "/", mustWork = TRUE)
external_controls_path <- normalizePath(
  file.path(root, args$external_controls),
  winslash = "/",
  mustWork = TRUE
)
bootstrap_reps <- as.integer(args$bootstrap_reps)
bootstrap_seed <- as.integer(args$bootstrap_seed)
table_dir <- file.path(root, "outputs", "tables")
figure_dir <- file.path(root, "results", "figures")
qa_figure_dir <- file.path(root, "results", "qa")
paper_table_dir <- file.path(root, "results", "tables")
dir.create(table_dir, recursive = TRUE, showWarnings = FALSE)
dir.create(figure_dir, recursive = TRUE, showWarnings = FALSE)
dir.create(qa_figure_dir, recursive = TRUE, showWarnings = FALSE)
dir.create(paper_table_dir, recursive = TRUE, showWarnings = FALSE)

cat("Starting common-prior analysis with filtered SQLite reads.\n")
print(gc())

connection <- dbConnect(SQLite(), database_path)
on.exit(dbDisconnect(connection), add = TRUE)

read_agent_run <- function(run_id, source_run_id, tone_suffix = "") {
  sql <- paste(
    "SELECT r.run_id, r.task_id, r.meeting_number, r.corpus_iteration,",
    "r.forecaster_type, r.pair_id, r.source_role, r.previous_expectation,",
    "r.new_expectation, r.forecast_revision, a.fixed_prior, a.source_run_id,",
    "a.source_text_hash, 100.0 * ts.score AS tone_pp, ts.hawk_count,",
    "ts.dove_count, ts.word_count",
    "FROM agent_results_latest AS r",
    "JOIN agent_tasks AS a",
    "  ON a.run_id = r.run_id AND a.task_id = r.task_id",
    "JOIN tone_scores AS ts",
    "  ON ts.run_id = a.source_run_id",
    " AND ts.meeting_number = r.meeting_number",
    " AND ts.iteration = r.corpus_iteration",
    " AND ts.role = r.source_role || ?",
    "WHERE r.run_id = ?",
    "  AND r.status = 'completed'",
    "  AND a.source_run_id = ?"
  )
  as.data.table(dbGetQuery(
    connection,
    sql,
    params = list(tone_suffix, run_id, source_run_id)
  ))
}

validate_agent_panel <- function(data, expected_rows, expected_pairs, expected_roles,
                                 expected_draws, panel_name) {
  if (nrow(data) != expected_rows) {
    stop(sprintf("%s: expected %s rows, found %s", panel_name, expected_rows, nrow(data)))
  }
  if (uniqueN(data$pair_id) != expected_pairs) {
    stop(sprintf(
      "%s: expected %s pair fixed effects, found %s",
      panel_name, expected_pairs, uniqueN(data$pair_id)
    ))
  }
  if (uniqueN(data$meeting_number) != 71L) {
    stop(sprintf("%s: expected 71 meetings", panel_name))
  }
  if (!identical(sort(unique(data$corpus_iteration)), as.integer(expected_draws))) {
    stop(sprintf("%s: regeneration indices do not match the expected set", panel_name))
  }
  if (!setequal(unique(data$source_role), expected_roles)) {
    stop(sprintf("%s: source roles do not match the design", panel_name))
  }
  required <- c(
    "forecast_revision", "new_expectation", "previous_expectation",
    "fixed_prior", "tone_pp", "pair_id", "source_text_hash"
  )
  if (anyNA(data[, ..required])) {
    stop(sprintf("%s: missing values in required fields", panel_name))
  }
  if (max(abs(data$forecast_revision -
              (data$new_expectation - data$previous_expectation))) > 1e-12) {
    stop(sprintf("%s: forecast revision identity failed", panel_name))
  }
  if (max(abs(data$fixed_prior - data$previous_expectation)) > 1e-12) {
    stop(sprintf("%s: supplied prior differs from the stored fixed prior", panel_name))
  }
  pair_check <- data[, .(
    rows = .N,
    roles = uniqueN(source_role),
    priors = uniqueN(fixed_prior),
    meetings = uniqueN(meeting_number),
    draws = uniqueN(corpus_iteration),
    forecasters = uniqueN(forecaster_type)
  ), by = pair_id]
  expected_role_count <- length(expected_roles)
  if (any(pair_check$rows != expected_role_count) ||
      any(pair_check$roles != expected_role_count) ||
      any(pair_check$priors != 1L) ||
      any(pair_check$meetings != 1L) ||
      any(pair_check$draws != 1L) ||
      any(pair_check$forecasters != 1L)) {
    stop(sprintf("%s: pair-level balance or common-prior invariant failed", panel_name))
  }
  invisible(TRUE)
}

main <- read_agent_run(
  args$main_run_id,
  args$main_source_run_id,
  tone_suffix = "_text"
)
validate_agent_panel(
  main,
  expected_rows = 7100L,
  expected_pairs = 3550L,
  expected_roles = c("dove", "hawk"),
  expected_draws = 1:25,
  panel_name = "25-regeneration common-prior panel"
)
main[, hawk := as.integer(source_role == "hawk")]
main[, pair_id := factor(pair_id)]

dose <- read_agent_run(
  args$dose_run_id,
  args$dose_source_run_id,
  tone_suffix = ""
)
validate_agent_panel(
  dose,
  expected_rows = 4260L,
  expected_pairs = 710L,
  expected_roles = c(
    "dove_high", "dove_low", "original", "neutral", "hawk_low", "hawk_high"
  ),
  expected_draws = 1:5,
  panel_name = "six-arm common-prior panel"
)
dose[, pair_id := factor(pair_id)]

dose_score_path <- file.path(root, "data", "analysis", "realized_dose_dataset.csv")
dose_scores <- fread(dose_score_path, encoding = "UTF-8", showProgress = FALSE)
setnames(dose_scores, "text_role", "source_role")
if (nrow(dose_scores) != 2130L ||
    uniqueN(dose_scores[, .(meeting_number, iteration, source_role)]) != 2130L) {
  stop("Realized-dose score file is not a complete 71 x 5 x 6 panel")
}

source_cells <- unique(dose[, .(
  meeting_number,
  iteration = corpus_iteration,
  source_role,
  source_text_hash,
  db_lexicon_score = tone_pp / 100
)])
score_check <- merge(
  source_cells,
  dose_scores[, .(
    meeting_number,
    iteration,
    source_role,
    text_hash,
    assigned_dose,
    lexicon_score,
    semantic_score,
    pairwise_score
  )],
  by = c("meeting_number", "iteration", "source_role"),
  all = TRUE,
  sort = FALSE
)
if (nrow(score_check) != 2130L || anyNA(score_check$source_text_hash) ||
    anyNA(score_check$text_hash)) {
  stop("Dose-score merge is incomplete")
}
if (any(score_check$source_text_hash != score_check$text_hash)) {
  stop("Dose-score text hashes do not match the texts supplied to forecasters")
}
if (max(abs(score_check$db_lexicon_score - score_check$lexicon_score)) > 1e-12) {
  stop("Lexicon scores in SQLite and the blinded-score dataset disagree")
}

dose <- merge(
  dose,
  dose_scores[, .(
    meeting_number,
    corpus_iteration = iteration,
    source_role,
    assigned_dose,
    semantic_score,
    pairwise_score
  )],
  by = c("meeting_number", "corpus_iteration", "source_role"),
  all.x = TRUE,
  sort = FALSE
)
if (anyNA(dose[source_role != "original", assigned_dose]) ||
    anyNA(dose$semantic_score) || anyNA(dose$pairwise_score)) {
  stop("Dose panel is missing assigned-dose or blinded-tone scores")
}

mixture_columns <- c(
  "forecast_anchored_gpt55",
  "forecast_unanchored_gpt55",
  "wc_orig",
  "ibcbr_agro",
  "ibcbr_service",
  "ibcbr_industry"
)
baseline_data <- as.data.table(haven::read_dta(dataset_path))
baseline_data[, date_daily := as.Date(date_daily)]
baseline_working <- baseline_data[
  complete.cases(baseline_data[, c("inflacao12m", mixture_columns), with = FALSE])
]
mixture_model <- lm(
  inflacao12m ~ forecast_anchored_gpt55 + forecast_unanchored_gpt55 + wc_orig +
    ibcbr_agro + ibcbr_service + ibcbr_industry,
  data = baseline_working
)
mixture_weights <- coef(mixture_model)
anchored_weight <- unname(mixture_weights[["forecast_anchored_gpt55"]])
unanchored_weight <- unname(mixture_weights[["forecast_unanchored_gpt55"]])

pre_2024_meetings <- baseline_data[
  date_daily < as.Date("2024-01-01"),
  as.integer(reuniao)
]
heldout_meetings <- baseline_data[
  date_daily >= as.Date("2024-01-01"),
  as.integer(reuniao)
]
if (length(pre_2024_meetings) != 57L || length(heldout_meetings) != 14L) {
  stop("The expected 57/14 pre-2024 and held-out meeting split was not recovered")
}
mixture_model_pre_2024 <- lm(
  inflacao12m ~ forecast_anchored_gpt55 + forecast_unanchored_gpt55 + wc_orig +
    ibcbr_agro + ibcbr_service + ibcbr_industry,
  data = baseline_working[reuniao %in% pre_2024_meetings]
)
mixture_weights_pre_2024 <- coef(mixture_model_pre_2024)

external_controls <- fread(
  external_controls_path,
  encoding = "UTF-8",
  showProgress = FALSE
)
if (!all(c(
  "reuniao", "rtn_pub_window_5d", "cds_series_available"
) %in% names(external_controls))) {
  stop("Fiscal-sample file lacks RTN-window or CDS-availability fields")
}
no_rtn_meetings <- external_controls[
  rtn_pub_window_5d == 0,
  as.integer(reuniao)
]
cds_covered_meetings <- external_controls[
  cds_series_available == 1L,
  as.integer(reuniao)
]
if (length(no_rtn_meetings) != 66L || length(cds_covered_meetings) != 67L) {
  stop("The expected 66-meeting no-RTN and 67-meeting CDS samples were not recovered")
}

build_composite_panel <- function(data, id_columns, weights = mixture_weights) {
  cast_formula <- as.formula(sprintf(
    "%s ~ forecaster_type",
    paste(id_columns, collapse = " + ")
  ))
  wide <- dcast(
    data,
    cast_formula,
    value.var = "forecast_revision"
  )
  if (anyNA(wide$anchored) || anyNA(wide$unanchored)) {
    stop("Composite panel is missing an anchored or unanchored response")
  }
  wide[, forecast_revision :=
         unname(weights[["forecast_anchored_gpt55"]]) * anchored +
         unname(weights[["forecast_unanchored_gpt55"]]) * unanchored]
  wide[, pair_id := factor(paste(meeting_number, corpus_iteration, sep = "::"))]
  wide[, forecaster_type := "composite"]
  wide
}

main_composite <- build_composite_panel(
  main,
  c("meeting_number", "corpus_iteration", "source_role", "hawk", "tone_pp")
)
main_composite_pre_2024 <- build_composite_panel(
  main,
  c("meeting_number", "corpus_iteration", "source_role", "hawk", "tone_pp"),
  weights = mixture_weights_pre_2024
)
dose_composite <- build_composite_panel(
  dose,
  c(
    "meeting_number", "corpus_iteration", "source_role", "assigned_dose",
    "tone_pp", "semantic_score", "pairwise_score"
  )
)
if (nrow(main_composite) != 3550L || uniqueN(main_composite$pair_id) != 1775L) {
  stop("Main composite is not a complete 25-regeneration two-arm panel")
}
if (nrow(main_composite_pre_2024) != 3550L ||
    uniqueN(main_composite_pre_2024$pair_id) != 1775L) {
  stop("Pre-2024 calibrated composite is not a complete main panel")
}
if (nrow(dose_composite) != 2130L || uniqueN(dose_composite$pair_id) != 355L) {
  stop("Dose composite is not a complete five-regeneration six-arm panel")
}

rm(source_cells, score_check)
print(gc())
cat("Validated 7,100 main rows and 4,260 dose-response rows.\n")

coefficient_row <- function(model, term) {
  table <- coeftable(model)
  if (!term %in% rownames(table)) {
    stop(sprintf("Term '%s' not found. Available terms: %s", term,
                 paste(rownames(table), collapse = ", ")))
  }
  p_column <- grep("^Pr", colnames(table), value = TRUE)[1]
  data.table(
    estimate = unname(table[term, "Estimate"]),
    std_error = unname(table[term, "Std. Error"]),
    t_statistic = unname(table[term, "t value"]),
    p_value = unname(table[term, p_column])
  )
}

estimate_iv_system <- function(data, instrument, tone = "tone_pp",
                               sample_label, vcov_formula = ~meeting_number) {
  rf_formula <- as.formula(sprintf("forecast_revision ~ %s | pair_id", instrument))
  fs_formula <- as.formula(sprintf("%s ~ %s | pair_id", tone, instrument))
  iv_formula <- as.formula(sprintf(
    "forecast_revision ~ 1 | pair_id | %s ~ %s", tone, instrument
  ))
  reduced_form <- feols(
    rf_formula, data = data, vcov = vcov_formula, notes = FALSE
  )
  first_stage <- feols(
    fs_formula, data = data, vcov = vcov_formula, notes = FALSE
  )
  iv_model <- feols(
    iv_formula, data = data, vcov = vcov_formula, notes = FALSE
  )
  rf <- coefficient_row(reduced_form, instrument)
  fs <- coefficient_row(first_stage, instrument)
  iv_term <- paste0("fit_", tone)
  iv <- coefficient_row(iv_model, iv_term)
  result <- data.table(
    sample = sample_label,
    tone_measure = tone,
    instrument = instrument,
    reduced_form = rf$estimate,
    reduced_form_se = rf$std_error,
    reduced_form_p = rf$p_value,
    first_stage = fs$estimate,
    first_stage_se = fs$std_error,
    first_stage_p = fs$p_value,
    first_stage_f = fs$t_statistic^2,
    prompt_iv = iv$estimate,
    prompt_iv_se = iv$std_error,
    prompt_iv_p = iv$p_value,
    observations = nobs(iv_model),
    pair_fe_groups = uniqueN(data$pair_id),
    meeting_clusters = uniqueN(data$meeting_number),
    draws = uniqueN(data$corpus_iteration)
  )
  list(
    result = result,
    reduced_form = reduced_form,
    first_stage = first_stage,
    iv_model = iv_model
  )
}

main_samples <- list(
  "Composite" = main_composite,
  "All forecasters" = main,
  "Anchored" = main[forecaster_type == "anchored"],
  "Unanchored" = main[forecaster_type == "unanchored"]
)
main_systems <- lapply(names(main_samples), function(label) {
  estimate_iv_system(main_samples[[label]], "hawk", "tone_pp", label)
})
names(main_systems) <- names(main_samples)
main_results <- rbindlist(lapply(main_systems, function(x) x$result))

heldout_systems <- list(
  "Full sample, full calibration" = main_systems[["Composite"]],
  "Pre-2024, pre-2024 calibration" = estimate_iv_system(
    main_composite_pre_2024[meeting_number %in% pre_2024_meetings],
    "hawk",
    "tone_pp",
    "Pre-2024, pre-2024 calibration"
  ),
  "Held-out 2024-2025, pre-2024 calibration" = estimate_iv_system(
    main_composite_pre_2024[meeting_number %in% heldout_meetings],
    "hawk",
    "tone_pp",
    "Held-out 2024-2025, pre-2024 calibration"
  ),
  "Held-out 2024-2025, full calibration" = estimate_iv_system(
    main_composite[meeting_number %in% heldout_meetings],
    "hawk",
    "tone_pp",
    "Held-out 2024-2025, full calibration"
  )
)
heldout_results <- rbindlist(lapply(
  names(heldout_systems),
  function(label) copy(heldout_systems[[label]]$result)[, specification := label]
))
setcolorder(
  heldout_results,
  c("specification", setdiff(names(heldout_results), "specification"))
)

fiscal_systems <- list(
  "Full sample" = main_systems[["Composite"]],
  "No RTN window" = estimate_iv_system(
    main_composite[meeting_number %in% no_rtn_meetings],
    "hawk",
    "tone_pp",
    "No RTN window"
  ),
  "CDS-covered sample" = estimate_iv_system(
    main_composite[meeting_number %in% cds_covered_meetings],
    "hawk",
    "tone_pp",
    "CDS-covered sample"
  )
)
fiscal_results <- rbindlist(lapply(
  names(fiscal_systems),
  function(label) copy(fiscal_systems[[label]]$result)[, specification := label]
))
setcolorder(
  fiscal_results,
  c("specification", setdiff(names(fiscal_results), "specification"))
)

focus_working <- baseline_data[
  complete.cases(baseline_data[, .(inflacao12m, score_orig, l_inflacao12m)])
]
focus_model <- lm(
  inflacao12m ~ score_orig + l_inflacao12m,
  data = focus_working
)
focus_vcov_hac <- sandwich::NeweyWest(
  focus_model,
  lag = 4L,
  prewhite = FALSE,
  adjust = TRUE
)
focus_coeftable <- lmtest::coeftest(focus_model, vcov. = focus_vcov_hac)
focus_sr <- unname(coef(focus_model)[["score_orig"]])
focus_sr_se <- unname(focus_coeftable["score_orig", "Std. Error"])
focus_rho <- unname(coef(focus_model)[["l_inflacao12m"]])
focus_rho_se <- unname(focus_coeftable["l_inflacao12m", "Std. Error"])
main_composite_result <- main_systems[["Composite"]]$result
prompt_iv <- main_composite_result$prompt_iv
prompt_iv_se <- main_composite_result$prompt_iv_se

calibration_results <- data.table(
  estimand = c(
    "Immediate common-prior Prompt-IV",
    "Focus projection, short run",
    "Focus AR(1) persistence"
  ),
  estimate = c(
    prompt_iv,
    focus_sr,
    focus_rho
  ),
  std_error = c(
    prompt_iv_se,
    focus_sr_se,
    focus_rho_se
  )
)
calibration_results[, `:=`(
  ci_low = estimate - 1.96 * std_error,
  ci_high = estimate + 1.96 * std_error
)]

two_way_vcov_warnings <- character()
main_two_way_system <- withCallingHandlers(
  estimate_iv_system(
    main_composite,
    instrument = "hawk",
    tone = "tone_pp",
    sample_label = "Composite - two-way clustered",
    vcov_formula = ~meeting_number + corpus_iteration
  ),
  warning = function(warning_condition) {
    two_way_vcov_warnings <<- c(
      two_way_vcov_warnings,
      conditionMessage(warning_condition)
    )
    invokeRestart("muffleWarning")
  }
)
main_two_way <- main_two_way_system$result
main_two_way[, vcov_note := if (length(two_way_vcov_warnings)) {
  "fixest eigenvalue correction applied to a non-PSD two-way clustered VCOV"
} else {
  "No VCOV correction"
}]

main_by_draw <- rbindlist(lapply(sort(unique(main_composite$corpus_iteration)), function(draw_id) {
  draw_data <- main_composite[corpus_iteration == draw_id]
  estimate_iv_system(
    draw_data,
    instrument = "hawk",
    tone = "tone_pp",
    sample_label = sprintf("Regeneration %02d", draw_id)
  )$result[, draw := draw_id]
}))

main_draw_summary <- data.table(
  draws = nrow(main_by_draw),
  prompt_iv_mean = mean(main_by_draw$prompt_iv),
  prompt_iv_sd = sd(main_by_draw$prompt_iv),
  prompt_iv_min = min(main_by_draw$prompt_iv),
  prompt_iv_max = max(main_by_draw$prompt_iv),
  prompt_iv_positive_share = mean(main_by_draw$prompt_iv > 0),
  reduced_form_mean = mean(main_by_draw$reduced_form),
  reduced_form_sd = sd(main_by_draw$reduced_form),
  first_stage_mean = mean(main_by_draw$first_stage),
  first_stage_sd = sd(main_by_draw$first_stage)
)

main_forecaster_wide <- dcast(
  main,
  meeting_number + corpus_iteration + source_role + hawk + tone_pp ~ forecaster_type,
  value.var = "forecast_revision"
)
if (nrow(main_forecaster_wide) != 3550L ||
    anyNA(main_forecaster_wide$anchored) || anyNA(main_forecaster_wide$unanchored)) {
  stop("Main forecaster-heterogeneity panel is incomplete")
}
main_forecaster_wide[, forecast_revision := unanchored - anchored]
main_forecaster_wide[, pair_id := factor(paste(
  meeting_number,
  corpus_iteration,
  sep = "::"
))]
main_heterogeneity_system <- estimate_iv_system(
  main_forecaster_wide,
  instrument = "hawk",
  tone = "tone_pp",
  sample_label = "Unanchored minus anchored response"
)
main_heterogeneity_result <- main_heterogeneity_system$result
main_heterogeneity <- data.table(
  estimand = c(
    "Unanchored minus anchored reduced form",
    "Unanchored minus anchored Prompt-IV"
  ),
  estimate = c(
    main_heterogeneity_result$reduced_form,
    main_heterogeneity_result$prompt_iv
  ),
  std_error = c(
    main_heterogeneity_result$reduced_form_se,
    main_heterogeneity_result$prompt_iv_se
  ),
  p_value = c(
    main_heterogeneity_result$reduced_form_p,
    main_heterogeneity_result$prompt_iv_p
  ),
  observations = main_heterogeneity_result$observations,
  pair_fe_groups = main_heterogeneity_result$pair_fe_groups,
  meeting_clusters = main_heterogeneity_result$meeting_clusters,
  draws = main_heterogeneity_result$draws
)
main_heterogeneity[, t_statistic := estimate / std_error]
setcolorder(main_heterogeneity, c(
  "estimand", "estimate", "std_error", "t_statistic", "p_value",
  "observations", "pair_fe_groups", "meeting_clusters", "draws"
))

arm_dummy_names <- c("dove_high", "dove_low", "neutral", "hawk_low", "hawk_high")
for (arm in arm_dummy_names) {
  dose[, (arm) := as.integer(source_role == arm)]
  dose_composite[, (arm) := as.integer(source_role == arm)]
}

estimate_arm_model <- function(data, sample_label) {
  model <- feols(
    forecast_revision ~ dove_high + dove_low + neutral + hawk_low + hawk_high | pair_id,
    data = data,
    vcov = ~meeting_number,
    notes = FALSE
  )
  rows <- rbindlist(lapply(arm_dummy_names, function(term) {
    coefficient_row(model, term)[, `:=`(
      sample = sample_label,
      arm = term,
      observations = nobs(model),
      pair_fe_groups = uniqueN(data$pair_id),
      meeting_clusters = uniqueN(data$meeting_number),
      draws = uniqueN(data$corpus_iteration)
    )]
  }), fill = TRUE)
  setcolorder(rows, c(
    "sample", "arm", "estimate", "std_error", "t_statistic", "p_value",
    "observations", "pair_fe_groups", "meeting_clusters", "draws"
  ))
  list(model = model, result = rows)
}

dose_samples <- list(
  "Composite" = dose_composite,
  "All forecasters" = dose,
  "Anchored" = dose[forecaster_type == "anchored"],
  "Unanchored" = dose[forecaster_type == "unanchored"]
)
arm_models <- lapply(names(dose_samples), function(label) {
  estimate_arm_model(dose_samples[[label]], label)
})
names(arm_models) <- names(dose_samples)
arm_effects <- rbindlist(lapply(arm_models, function(x) x$result), fill = TRUE)

outcome_cells <- rbindlist(lapply(names(dose_samples), function(sample_label) {
  sample_data <- dose_samples[[sample_label]]
  rbindlist(lapply(c(
    "dove_high", "dove_low", "original", "neutral", "hawk_low", "hawk_high"
  ), function(arm) {
    cell_data <- sample_data[source_role == arm]
    model <- feols(
      forecast_revision ~ 1,
      data = cell_data,
      vcov = ~meeting_number,
      notes = FALSE
    )
    row <- coefficient_row(model, "(Intercept)")
    row[, `:=`(
      sample = sample_label,
      arm = arm,
      observations = nrow(cell_data)
    )]
    row
  }))
}))
setcolorder(outcome_cells, c(
  "sample", "arm", "estimate", "std_error", "t_statistic", "p_value", "observations"
))

build_wide_dose <- function(data) {
  wide <- dcast(
    data,
    pair_id + meeting_number + corpus_iteration + forecaster_type ~ source_role,
    value.var = "forecast_revision"
  )
  wide[, `High hawk - high dove` := hawk_high - dove_high]
  wide[, `Low hawk - low dove` := hawk_low - dove_low]
  wide[, `High hawk - low hawk` := hawk_high - hawk_low]
  wide[, `Low dove - high dove` := dove_low - dove_high]
  wide[, `Neutral - original` := neutral - original]
  wide[, `Neutral - low-dose midpoint` := neutral - (hawk_low + dove_low) / 2]
  wide[, `High spread - low spread` :=
         (hawk_high - dove_high) - (hawk_low - dove_low)]
  wide[, unanchored := as.integer(forecaster_type == "unanchored")]
  wide
}

wide_dose <- build_wide_dose(dose)
wide_dose_composite <- build_wide_dose(dose_composite)

contrast_names <- c(
  "High hawk - high dove",
  "Low hawk - low dove",
  "High hawk - low hawk",
  "Low dove - high dove",
  "Neutral - original",
  "Neutral - low-dose midpoint",
  "High spread - low spread"
)

estimate_contrasts <- function(data, sample_label) {
  rbindlist(lapply(contrast_names, function(contrast) {
    model_data <- data[, .(
      meeting_number,
      value = get(contrast)
    )]
    model <- feols(
      value ~ 1,
      data = model_data,
      vcov = ~meeting_number,
      notes = FALSE
    )
    row <- coefficient_row(model, "(Intercept)")
    row[, `:=`(
      sample = sample_label,
      contrast = contrast,
      pairs = nrow(model_data),
      meeting_clusters = uniqueN(model_data$meeting_number)
    )]
    row
  }))
}

dose_contrasts <- rbindlist(list(
  estimate_contrasts(wide_dose_composite, "Composite"),
  estimate_contrasts(wide_dose, "All forecasters"),
  estimate_contrasts(wide_dose[forecaster_type == "anchored"], "Anchored"),
  estimate_contrasts(wide_dose[forecaster_type == "unanchored"], "Unanchored")
))
setcolorder(dose_contrasts, c(
  "sample", "contrast", "estimate", "std_error", "t_statistic", "p_value",
  "pairs", "meeting_clusters"
))

dose_heterogeneity <- rbindlist(lapply(contrast_names, function(contrast) {
  model_data <- wide_dose[, .(
    meeting_number,
    unanchored,
    value = get(contrast)
  )]
  model <- feols(
    value ~ unanchored,
    data = model_data,
    vcov = ~meeting_number,
    notes = FALSE
  )
  row <- coefficient_row(model, "unanchored")
  row[, `:=`(
    contrast = contrast,
    comparison = "Unanchored minus anchored",
    pairs = nrow(model_data),
    meeting_clusters = uniqueN(model_data$meeting_number)
  )]
  row
}))
setcolorder(dose_heterogeneity, c(
  "contrast", "comparison", "estimate", "std_error", "t_statistic", "p_value",
  "pairs", "meeting_clusters"
))

generated_composite <- dose_composite[source_role != "original"]
generated_composite[, assigned_dose := as.numeric(assigned_dose)]
generated_composite[, dose_squared := assigned_dose^2]

low_data_composite <- dose_composite[source_role %in% c("dove_low", "hawk_low")]
low_data_composite[, z_hawk := as.integer(source_role == "hawk_low")]
high_data_composite <- dose_composite[source_role %in% c("dove_high", "hawk_high")]
high_data_composite[, z_hawk := as.integer(source_role == "hawk_high")]

iv_sensitivity_systems <- list(
  "Baseline stance (25 regenerations)" = main_systems[["Composite"]],
  "Low-intensity endpoints" = estimate_iv_system(
    low_data_composite, "z_hawk", "tone_pp", "Low-intensity endpoints"
  ),
  "High-intensity endpoints" = estimate_iv_system(
    high_data_composite, "z_hawk", "tone_pp", "High-intensity endpoints"
  ),
  "Ordered five-arm dose" = estimate_iv_system(
    generated_composite, "assigned_dose", "tone_pp", "Ordered five-arm dose"
  )
)
iv_sensitivity <- rbindlist(lapply(iv_sensitivity_systems, function(x) x$result))
iv_sensitivity[, specification := names(iv_sensitivity_systems)]
setcolorder(iv_sensitivity, c("specification", setdiff(names(iv_sensitivity), "specification")))

alternative_measure_systems <- lapply(
  c("tone_pp", "semantic_score", "pairwise_score"),
  function(measure) {
    estimate_iv_system(
      generated_composite,
      instrument = "assigned_dose",
      tone = measure,
      sample_label = "Ordered five-arm dose"
    )
  }
)
names(alternative_measure_systems) <- c("Lexicon", "Semantic blind", "Pairwise blind")
iv_alternative_measures <- rbindlist(lapply(
  names(alternative_measure_systems),
  function(label) alternative_measure_systems[[label]]$result[, measure_label := label]
))
setcolorder(iv_alternative_measures, c(
  "measure_label", setdiff(names(iv_alternative_measures), "measure_label")
))

quadratic_model <- feols(
  forecast_revision ~ assigned_dose + dose_squared | pair_id,
  data = generated_composite,
  vcov = ~meeting_number,
  notes = FALSE
)
quadratic_result <- rbindlist(list(
  coefficient_row(quadratic_model, "assigned_dose")[, term := "Linear dose"],
  coefficient_row(quadratic_model, "dose_squared")[, term := "Squared dose"]
))
quadratic_result[, `:=`(
  observations = nobs(quadratic_model),
  pair_fe_groups = uniqueN(generated_composite$pair_id),
  meeting_clusters = uniqueN(generated_composite$meeting_number)
)]

monotonicity <- data.table(
  statistic = c(
    "Weakly monotone five-arm outcome path",
    "High hawk above low hawk",
    "Low dove above high dove",
    "High hawk above high dove",
    "Low hawk above low dove",
    "Neutral above original"
  ),
  share = c(
    mean(with(wide_dose_composite,
              dove_high <= dove_low & dove_low <= neutral &
              neutral <= hawk_low & hawk_low <= hawk_high)),
    mean(wide_dose_composite$hawk_high > wide_dose_composite$hawk_low),
    mean(wide_dose_composite$dove_low > wide_dose_composite$dove_high),
    mean(wide_dose_composite$hawk_high > wide_dose_composite$dove_high),
    mean(wide_dose_composite$hawk_low > wide_dose_composite$dove_low),
    mean(wide_dose_composite$neutral > wide_dose_composite$original)
  ),
  pairs = nrow(wide_dose_composite)
)

text_cells <- unique(dose[, .(
  meeting_number,
  corpus_iteration,
  source_role,
  assigned_dose,
  tone_pp,
  semantic_score,
  pairwise_score
)])
if (nrow(text_cells) != 2130L) {
  stop("Expected 2,130 unique dose-response text cells")
}

tone_cells <- rbindlist(lapply(
  c("tone_pp", "semantic_score", "pairwise_score"),
  function(measure) {
    rbindlist(lapply(c(
      "dove_high", "dove_low", "original", "neutral", "hawk_low", "hawk_high"
    ), function(arm) {
      cell_data <- text_cells[source_role == arm]
      model_data <- cell_data[, .(
        meeting_number,
        value = get(measure)
      )]
      model <- feols(
        value ~ 1,
        data = model_data,
        vcov = ~meeting_number,
        notes = FALSE
      )
      row <- coefficient_row(model, "(Intercept)")
      row[, `:=`(
        measure = measure,
        arm = arm,
        observations = nrow(model_data)
      )]
      row
    }))
  }
))
setcolorder(tone_cells, c(
  "measure", "arm", "estimate", "std_error", "t_statistic", "p_value", "observations"
))

verify_ivreg <- function(data, tone, instrument, label) {
  variables <- as.data.frame(data[, c("forecast_revision", tone, instrument), with = FALSE])
  residualized <- as.data.frame(fixest::demean(variables, f = data$pair_id))
  names(residualized) <- c("y", "x", "z")
  model <- ivreg::ivreg(y ~ x | z, data = residualized)
  vcov_cluster <- sandwich::vcovCL(
    model,
    cluster = data$meeting_number,
    type = "HC1"
  )
  data.table(
    specification = label,
    estimate_ivreg = unname(coef(model)["x"]),
    se_ivreg_cluster = sqrt(unname(vcov_cluster["x", "x"]))
  )
}

ivreg_verification <- rbindlist(list(
  verify_ivreg(main_composite, "tone_pp", "hawk", "Baseline stance (25 regenerations)"),
  verify_ivreg(
    generated_composite,
    "tone_pp",
    "assigned_dose",
    "Ordered five-arm dose"
  )
))
ivreg_verification <- merge(
  ivreg_verification,
  iv_sensitivity[, .(specification, estimate_fixest = prompt_iv, se_fixest = prompt_iv_se)],
  by = "specification",
  all.x = TRUE
)
ivreg_verification[, coefficient_difference := estimate_ivreg - estimate_fixest]
if (max(abs(ivreg_verification$coefficient_difference)) > 1e-10) {
  stop("fixest and residualized ivreg coefficients do not agree")
}

main_iv_variables <- as.data.frame(
  main_composite[, c("forecast_revision", "tone_pp", "hawk"), with = FALSE]
)
main_iv_residualized <- as.data.frame(
  fixest::demean(main_iv_variables, f = main_composite$pair_id)
)
names(main_iv_residualized) <- c("y", "x", "z")
main_iv_residualized$meeting_number <- main_composite$meeting_number
ivmodel_object <- ivmodel::ivmodel(
  Y = main_iv_residualized$y,
  D = main_iv_residualized$x,
  Z = main_iv_residualized$z,
  intercept = FALSE,
  clusterID = main_iv_residualized$meeting_number
)
ivmodel_ci <- as.data.table(as.data.frame(confint(ivmodel_object)), keep.rownames = "method")
setnames(ivmodel_ci, c("2.5%", "97.5%"), c("ci_low", "ci_high"))
ar_test_zero <- ivmodel::AR.test(ivmodel_object, beta0 = 0)
clr_test_zero <- ivmodel::CLR(ivmodel_object, beta0 = 0)
if (is.null(ar_test_zero) || is.null(clr_test_zero) ||
    !is.finite(ar_test_zero$p.value) ||
    !is.finite(as.numeric(clr_test_zero$p.value))) {
  stop("ivmodel did not return finite AR and CLR p values at the null of zero")
}
ar_p_zero <- as.numeric(ar_test_zero$p.value)
clr_p_zero <- as.numeric(clr_test_zero$p.value)

cluster_boot_stat <- function(meeting_data, indices) {
  sampled_meetings <- meeting_data$meeting_number[indices]
  parts <- lapply(seq_along(sampled_meetings), function(copy_id) {
    part <- copy(main_composite[meeting_number == sampled_meetings[[copy_id]]])
    part[, boot_pair_id := factor(paste(pair_id, copy_id, sep = "::boot::"))]
    part[, boot_meeting := copy_id]
    part
  })
  boot_data <- rbindlist(parts)
  model <- tryCatch(
    feols(
      forecast_revision ~ 1 | boot_pair_id | tone_pp ~ hawk,
      data = boot_data,
      vcov = ~boot_meeting,
      notes = FALSE
    ),
    error = function(e) NULL
  )
  if (is.null(model)) return(NA_real_)
  unname(coef(model)[["fit_tone_pp"]])
}

set.seed(bootstrap_seed)
cluster_boot <- boot::boot(
  data = data.frame(meeting_number = sort(unique(main_composite$meeting_number))),
  statistic = cluster_boot_stat,
  R = bootstrap_reps,
  parallel = "no"
)
cluster_boot_draws <- data.table(
  replication = seq_len(nrow(cluster_boot$t)),
  prompt_iv = as.numeric(cluster_boot$t[, 1])
)
cluster_boot_valid <- cluster_boot_draws[is.finite(prompt_iv), prompt_iv]
if (length(cluster_boot_valid) < 0.95 * bootstrap_reps) {
  stop("More than five percent of common-prior cluster bootstrap draws failed")
}
ar_ci <- ivmodel_ci[method == "AR"]
clr_ci <- ivmodel_ci[method == "CLR"]
if (nrow(ar_ci) != 1L || nrow(clr_ci) != 1L) {
  stop("ivmodel did not return unique AR and CLR confidence intervals")
}
inference_results <- rbindlist(list(
  data.table(
    method = "Meeting-clustered 2SLS",
    estimate = prompt_iv,
    std_error = prompt_iv_se,
    p_value = main_results[sample == "Composite"]$prompt_iv_p,
    ci_low = prompt_iv - 1.96 * prompt_iv_se,
    ci_high = prompt_iv + 1.96 * prompt_iv_se
  ),
  data.table(
    method = "Anderson-Rubin",
    estimate = NA_real_,
    std_error = NA_real_,
    p_value = ar_p_zero,
    ci_low = ar_ci$ci_low,
    ci_high = ar_ci$ci_high
  ),
  data.table(
    method = "Conditional likelihood ratio",
    estimate = NA_real_,
    std_error = NA_real_,
    p_value = clr_p_zero,
    ci_low = clr_ci$ci_low,
    ci_high = clr_ci$ci_high
  ),
  data.table(
    method = "Ordinary meeting-cluster bootstrap",
    estimate = mean(cluster_boot_valid),
    std_error = sd(cluster_boot_valid),
    p_value = 2 * min(
      mean(cluster_boot_valid <= 0),
      mean(cluster_boot_valid >= 0)
    ),
    ci_low = unname(quantile(cluster_boot_valid, 0.025)),
    ci_high = unname(quantile(cluster_boot_valid, 0.975))
  )
), fill = TRUE)

write_csv <- function(data, filename) {
  path <- file.path(table_dir, filename)
  fwrite(data, path, na = "")
  path
}

output_files <- list(
  main_models_csv = write_csv(
    rbindlist(list(main_results, main_two_way), fill = TRUE),
    paste0("common_prior_main_models_", args$stamp, ".csv")
  ),
  main_by_draw_csv = write_csv(
    main_by_draw,
    paste0("common_prior_main_by_draw_", args$stamp, ".csv")
  ),
  main_heterogeneity_csv = write_csv(
    main_heterogeneity,
    paste0("common_prior_main_heterogeneity_", args$stamp, ".csv")
  ),
  arm_effects_csv = write_csv(
    arm_effects,
    paste0("common_prior_dose_arm_effects_", args$stamp, ".csv")
  ),
  outcome_cells_csv = write_csv(
    outcome_cells,
    paste0("common_prior_dose_outcome_cells_", args$stamp, ".csv")
  ),
  contrasts_csv = write_csv(
    dose_contrasts,
    paste0("common_prior_dose_contrasts_", args$stamp, ".csv")
  ),
  dose_heterogeneity_csv = write_csv(
    dose_heterogeneity,
    paste0("common_prior_dose_heterogeneity_", args$stamp, ".csv")
  ),
  iv_sensitivity_csv = write_csv(
    iv_sensitivity,
    paste0("common_prior_iv_sensitivity_", args$stamp, ".csv")
  ),
  iv_alternative_measures_csv = write_csv(
    iv_alternative_measures,
    paste0("common_prior_dose_iv_measures_", args$stamp, ".csv")
  ),
  tone_cells_csv = write_csv(
    tone_cells,
    paste0("common_prior_dose_tone_cells_", args$stamp, ".csv")
  ),
  quadratic_csv = write_csv(
    quadratic_result,
    paste0("common_prior_dose_quadratic_", args$stamp, ".csv")
  ),
  monotonicity_csv = write_csv(
    monotonicity,
    paste0("common_prior_dose_monotonicity_", args$stamp, ".csv")
  ),
  ivreg_verification_csv = write_csv(
    ivreg_verification,
    paste0("common_prior_ivreg_verification_", args$stamp, ".csv")
  ),
  heldout_csv = write_csv(
    heldout_results,
    paste0("common_prior_heldout_", args$stamp, ".csv")
  ),
  fiscal_csv = write_csv(
    fiscal_results,
    paste0("common_prior_fiscal_samples_", args$stamp, ".csv")
  ),
  calibration_csv = write_csv(
    calibration_results,
    paste0("common_prior_focus_calibration_", args$stamp, ".csv")
  ),
  inference_csv = write_csv(
    inference_results,
    paste0("common_prior_inference_", args$stamp, ".csv")
  ),
  ivmodel_ci_csv = write_csv(
    ivmodel_ci,
    paste0("common_prior_ivmodel_ci_", args$stamp, ".csv")
  ),
  cluster_bootstrap_csv = write_csv(
    cluster_boot_draws,
    paste0("common_prior_cluster_bootstrap_", args$stamp, ".csv")
  )
)

significance_stars <- function(p) {
  if (!is.finite(p)) return("")
  if (p < 0.01) return("***")
  if (p < 0.05) return("**")
  if (p < 0.10) return("*")
  ""
}

format_estimate <- function(estimate, p, digits = 4L) {
  paste0(formatC(estimate, format = "f", digits = digits), significance_stars(p))
}

format_se <- function(se, digits = 4L) {
  paste0("(", formatC(se, format = "f", digits = digits), ")")
}

get_row <- function(data, key_column, key_value) {
  row <- data[get(key_column) == key_value]
  if (nrow(row) != 1L) {
    stop(sprintf("Expected one row for %s = %s", key_column, key_value))
  }
  row
}

write_main_table <- function(path) {
  columns <- c("Composite", "Anchored", "Unanchored")
  rows <- lapply(columns, function(label) get_row(main_results, "sample", label))
  lines <- c(
    "\\begin{table}[htbp]",
    "\\color{revisionred}",
    "\\centering",
    "\\begin{threeparttable}",
    "\\caption{Immediate Prompt-IV effects under a common prior}",
    "\\label{tab:common-prior-main}",
    "\\begin{footnotesize}",
    "\\begin{tabular}{lccc}",
    "\\toprule",
    " & (1) & (2) & (3) \\\\",
    " & Composite & Anchored & Unanchored \\\\",
    "\\midrule",
    "\\multicolumn{4}{l}{\\textit{Panel A. Reduced form}} \\\\",
    paste0(
      "Hawkish prompt & ",
      paste(vapply(rows, function(x) format_estimate(x$reduced_form, x$reduced_form_p), ""),
            collapse = " & "), " \\\\"
    ),
    paste0(" & ", paste(vapply(rows, function(x) format_se(x$reduced_form_se), ""),
                          collapse = " & "), " \\\\"),
    "\\addlinespace",
    "\\multicolumn{4}{l}{\\textit{Panel B. Second stage}} \\\\",
    paste0(
      "Realized tone index & ",
      paste(vapply(rows, function(x) format_estimate(x$prompt_iv, x$prompt_iv_p), ""),
            collapse = " & "), " \\\\"
    ),
    paste0(" & ", paste(vapply(rows, function(x) format_se(x$prompt_iv_se), ""),
                          collapse = " & "), " \\\\"),
    "\\addlinespace",
    "\\multicolumn{4}{l}{\\textit{Panel C. First stage}} \\\\",
    paste0(
      "Hawkish prompt & ",
      paste(vapply(rows, function(x) format_estimate(x$first_stage, x$first_stage_p), ""),
            collapse = " & "), " \\\\"
    ),
    paste0(" & ", paste(vapply(rows, function(x) format_se(x$first_stage_se), ""),
                          collapse = " & "), " \\\\"),
    paste0("First-stage $F$ statistic & ",
           paste(vapply(rows, function(x) formatC(x$first_stage_f, format = "f", digits = 1), ""),
                 collapse = " & "), " \\\\"),
    "\\midrule",
    "Meeting-by-regeneration fixed effects & Yes & Yes & Yes \\\\",
    "Standard errors clustered by meeting & Yes & Yes & Yes \\\\",
    paste0("Observations & ", paste(vapply(rows, function(x) format(x$observations, big.mark = ","), ""),
                                     collapse = " & "), " \\\\"),
    paste0("Fixed-effect groups & ", paste(vapply(rows, function(x) format(x$pair_fe_groups, big.mark = ","), ""),
                                           collapse = " & "), " \\\\"),
    "Meeting clusters & 71 & 71 & 71 \\\\",
    "Regeneration replicates & 25 & 25 & 25 \\\\",
    "\\bottomrule",
    "\\end{tabular}",
    "\\begin{tablenotes}[flushleft]",
    "\\scriptsize",
    paste(
      "\\item \\textit{Notes:} The dependent variable is the revision in the",
      "synthetic forecaster's twelve-month-ahead inflation expectation, in percentage",
      "points. Column (1) applies the fixed full-sample calibration coefficients to the",
      "anchored and unanchored revisions, matching the professional-forecaster composite",
      "used in the paper; columns (2) and (3) report its agent components. Meeting",
      "controls and original-text length are held fixed across counterfactual arms and",
      "therefore cancel within meeting-by-regeneration pairs. The prior $P_t^i$ is",
      "profile-specific and is held common only across textual arms and regenerations",
      "within the same meeting-profile cell $(t,i)$; regeneration index $s$ does not",
      "link different meetings. The tone index is",
      "hawkish minus dovish lexicon counts per 100 words, and binary prompt assignment",
      "instruments realized tone. Standard errors are clustered by Copom meeting.",
      "* $p<0.10$, ** $p<0.05$, *** $p<0.01$."
    ),
    "\\end{tablenotes}",
    "\\end{footnotesize}",
    "\\end{threeparttable}",
    "\\end{table}"
  )
  writeLines(lines, path, useBytes = TRUE)
}

write_arm_table <- function(path) {
  columns <- c("Composite", "Anchored", "Unanchored")
  arm_labels <- c(
    dove_high = "High-intensity dove",
    dove_low = "Low-intensity dove",
    neutral = "Neutral rewrite",
    hawk_low = "Low-intensity hawk",
    hawk_high = "High-intensity hawk"
  )
  lines <- c(
    "\\begin{table}[htbp]",
    "\\color{revisionred}",
    "\\centering",
    "\\begin{threeparttable}",
    "\\caption{Common-prior forecast effects by assigned textual arm}",
    "\\label{tab:common-prior-dose-arms}",
    "\\begin{footnotesize}",
    "\\begin{tabular}{lccc}",
    "\\toprule",
    " & (1) & (2) & (3) \\\\",
    " & Composite & Anchored & Unanchored \\\\",
    "\\midrule"
  )
  for (arm in names(arm_labels)) {
    arm_name <- arm
    rows <- lapply(columns, function(label) {
      x <- arm_effects[sample == label & get("arm") == arm_name]
      if (nrow(x) != 1L) stop("Arm table lookup failed")
      x
    })
    lines <- c(
      lines,
      paste0(
        arm_labels[[arm]], " & ",
        paste(vapply(rows, function(x) format_estimate(x$estimate, x$p_value), ""),
              collapse = " & "), " \\\\"
      ),
      paste0(" & ", paste(vapply(rows, function(x) format_se(x$std_error), ""),
                            collapse = " & "), " \\\\")
    )
  }
  sample_rows <- lapply(columns, function(label) {
    arm_effects[sample == label][1]
  })
  lines <- c(
    lines,
    "\\midrule",
    "Original text (reference arm) & Yes & Yes & Yes \\\\",
    "Pair fixed effects & Yes & Yes & Yes \\\\",
    "Standard errors clustered by meeting & Yes & Yes & Yes \\\\",
    paste0("Observations & ",
           paste(vapply(sample_rows, function(x) format(x$observations, big.mark = ","), ""),
                 collapse = " & "), " \\\\"),
    paste0("Pair fixed-effect groups & ",
           paste(vapply(sample_rows, function(x) format(x$pair_fe_groups, big.mark = ","), ""),
                 collapse = " & "), " \\\\"),
    "Meeting clusters & 71 & 71 & 71 \\\\",
    "Regeneration replicates & 5 & 5 & 5 \\\\",
    "\\bottomrule",
    "\\end{tabular}",
    "\\begin{tablenotes}[flushleft]",
    "\\scriptsize",
    paste(
      "\\item \\textit{Notes:} Each coefficient is the within-pair difference in",
      "forecast revision between the indicated text and the unmodified original minute.",
      "Column (1) applies the paper's fixed full-sample calibration coefficients to the",
      "anchored and unanchored revisions; columns (2) and (3) report those components.",
      "The prior $P_t^i$ is profile-specific and is held common only across textual",
      "arms and regenerations within the same meeting-profile cell $(t,i)$; regeneration",
      "index $s$ does not link different meetings. Baseline meeting controls and",
      "original-text length are unchanged across",
      "arms and cancel within pair. Positive coefficients indicate a more hawkish revision",
      "than under the original text. Standard errors in parentheses are clustered by",
      "Copom meeting. * $p<0.10$, ** $p<0.05$, *** $p<0.01$."
    ),
    "\\end{tablenotes}",
    "\\end{footnotesize}",
    "\\end{threeparttable}",
    "\\end{table}"
  )
  writeLines(lines, path, useBytes = TRUE)
}

write_iv_sensitivity_table <- function(path) {
  specs <- c(
    "Baseline stance (25 regenerations)",
    "Low-intensity endpoints",
    "High-intensity endpoints",
    "Ordered five-arm dose"
  )
  rows <- lapply(specs, function(spec) get_row(iv_sensitivity, "specification", spec))
  lines <- c(
    "\\begin{table}[htbp]",
    "\\color{revisionred}",
    "\\centering",
    "\\begin{threeparttable}",
    "\\caption{Prompt-IV sensitivity across common-prior treatment intensities}",
    "\\label{tab:common-prior-iv-sensitivity}",
    "\\begin{scriptsize}",
    "\\setlength{\\tabcolsep}{4.2pt}",
    "\\begin{tabular}{lcccc}",
    "\\toprule",
    " & (1) & (2) & (3) & (4) \\\\",
    " & Baseline & Low intensity & High intensity & Ordered dose \\\\",
    "\\midrule",
    "\\multicolumn{5}{l}{\\textit{Panel A. Second stage}} \\\\",
    paste0(
      "Realized tone index & ",
      paste(vapply(rows, function(x) format_estimate(x$prompt_iv, x$prompt_iv_p), ""),
            collapse = " & "), " \\\\"
    ),
    paste0(" & ", paste(vapply(rows, function(x) format_se(x$prompt_iv_se), ""),
                          collapse = " & "), " \\\\"),
    "\\addlinespace",
    "\\multicolumn{5}{l}{\\textit{Panel B. Reduced form}} \\\\",
    paste0(
      "Assigned prompt or dose & ",
      paste(vapply(rows, function(x) format_estimate(x$reduced_form, x$reduced_form_p), ""),
            collapse = " & "), " \\\\"
    ),
    paste0(" & ", paste(vapply(rows, function(x) format_se(x$reduced_form_se), ""),
                          collapse = " & "), " \\\\"),
    "\\addlinespace",
    "\\multicolumn{5}{l}{\\textit{Panel C. First stage}} \\\\",
    paste0(
      "Assigned prompt or dose & ",
      paste(vapply(rows, function(x) format_estimate(x$first_stage, x$first_stage_p), ""),
            collapse = " & "), " \\\\"
    ),
    paste0(" & ", paste(vapply(rows, function(x) format_se(x$first_stage_se), ""),
                          collapse = " & "), " \\\\"),
    paste0("First-stage $F$ statistic & ",
           paste(vapply(rows, function(x) formatC(x$first_stage_f, format = "f", digits = 1), ""),
                 collapse = " & "), " \\\\"),
    "\\midrule",
    "Pair fixed effects & Yes & Yes & Yes & Yes \\\\",
    "Standard errors clustered by meeting & Yes & Yes & Yes & Yes \\\\",
    paste0("Observations & ",
           paste(vapply(rows, function(x) format(x$observations, big.mark = ","), ""),
                 collapse = " & "), " \\\\"),
    paste0("Pair fixed-effect groups & ",
           paste(vapply(rows, function(x) format(x$pair_fe_groups, big.mark = ","), ""),
                 collapse = " & "), " \\\\"),
    paste0("Regeneration replicates & ",
           paste(vapply(rows, function(x) as.character(x$draws), ""), collapse = " & "),
           " \\\\"),
    "Meeting clusters & 71 & 71 & 71 & 71 \\\\",
    "\\bottomrule",
    "\\end{tabular}",
    "\\begin{tablenotes}[flushleft]",
    "\\scriptsize",
    paste(
      "\\item \\textit{Notes:} The dependent variable is the forecast revision in",
      "percentage points. Column (1) compares the standard hawkish and dovish prompts",
      "over 25 regenerations. Columns (2) and (3) compare the low- and high-intensity endpoints",
      "of the five-regeneration dose design. Column (4) uses assigned doses $-2,-1,0,1,2$",
      "across high dove, low dove, neutral, low hawk, and high hawk; the unmodified",
      "original is excluded from that linear dose specification. Binary first stages",
      "are hawk-minus-dove tone differences, whereas column (4) is the tone change per",
      "one assigned-dose step. Standard errors are clustered by Copom meeting.",
      "* $p<0.10$, ** $p<0.05$, *** $p<0.01$."
    ),
    "\\end{tablenotes}",
    "\\end{scriptsize}",
    "\\end{threeparttable}",
    "\\end{table}"
  )
  writeLines(lines, path, useBytes = TRUE)
}

format_number <- function(value, digits = 4L) {
  if (!is.finite(value)) return("--")
  formatC(value, format = "f", digits = digits)
}

format_interval <- function(low, high, digits = 4L) {
  if (!is.finite(low) || !is.finite(high)) return("--")
  paste0(
    "[",
    formatC(low, format = "f", digits = digits),
    ", ",
    formatC(high, format = "f", digits = digits),
    "]"
  )
}

write_heldout_table <- function(path) {
  specs <- c(
    "Full sample, full calibration",
    "Pre-2024, pre-2024 calibration",
    "Held-out 2024-2025, pre-2024 calibration",
    "Held-out 2024-2025, full calibration"
  )
  rows <- lapply(specs, function(spec) get_row(
    heldout_results,
    "specification",
    spec
  ))
  lines <- c(
    "\\begin{table}[htbp]",
    "\\color{revisionred}",
    "\\centering",
    "\\begin{threeparttable}",
    "\\caption{Temporal-calibration holdout for common-prior Prompt-IV, 2024--2025}",
    "\\label{tab:heldout-iv}",
    "\\begin{scriptsize}",
    "\\setlength{\\tabcolsep}{4.2pt}",
    "\\begin{tabular}{lcccc}",
    "\\toprule",
    " & (1) & (2) & (3) & (4) \\\\",
    " & Full sample & Pre-2024 & Held-out & Held-out \\\\",
    "\\midrule",
    paste0(
      "Realized tone index & ",
      paste(vapply(rows, function(x) {
        format_estimate(x$prompt_iv, x$prompt_iv_p)
      }, ""), collapse = " & "),
      " \\\\"
    ),
    paste0(
      " & ",
      paste(vapply(rows, function(x) format_se(x$prompt_iv_se), ""), collapse = " & "),
      " \\\\"
    ),
    "\\midrule",
    "Two-profile calibration & Full & Pre-2024 & Pre-2024 & Full \\\\",
    "Evaluation meetings & All & Pre-2024 & 2024--2025 & 2024--2025 \\\\",
    paste0(
      "Meetings & ",
      paste(vapply(rows, function(x) as.character(x$meeting_clusters), ""), collapse = " & "),
      " \\\\"
    ),
    paste0(
      "Observations & ",
      paste(vapply(rows, function(x) format(x$observations, big.mark = ","), ""), collapse = " & "),
      " \\\\"
    ),
    paste0(
    "Meeting-by-regeneration groups & ",
      paste(vapply(rows, function(x) format(x$pair_fe_groups, big.mark = ","), ""), collapse = " & "),
      " \\\\"
    ),
    paste0(
      "First-stage coefficient & ",
      paste(vapply(rows, function(x) format_number(x$first_stage), ""), collapse = " & "),
      " \\\\"
    ),
    paste0(
      "First-stage $F$ & ",
      paste(vapply(rows, function(x) format_number(x$first_stage_f, 1L), ""), collapse = " & "),
      " \\\\"
    ),
    "\\bottomrule",
    "\\end{tabular}",
    "\\begin{tablenotes}[flushleft]",
    "\\scriptsize",
    paste(
      "\\item \\textit{Notes:} The dependent variable is the calibrated composite",
      "forecast revision under the common-prior design. Column (3) is the temporal-calibration",
      "holdout: the two-profile coefficients are estimated only on pre-2024",
      "meetings and then applied to the 14 meetings in 2024--2025. Column (4) uses",
      "the full-sample calibration on the same held-out meetings. Every model absorbs",
      "meeting-by-regeneration fixed effects and clusters standard errors by meeting.",
      "In every specification, $P_t^i$ is profile-specific and common only across arms",
      "and regenerations within $(t,i)$; regeneration index $s$ does not link meetings.",
      "This split is not a test against the model's unobserved training cutoff.",
      "* $p<0.10$, ** $p<0.05$, *** $p<0.01$."
    ),
    "\\end{tablenotes}",
    "\\end{scriptsize}",
    "\\end{threeparttable}",
    "\\end{table}"
  )
  writeLines(lines, path, useBytes = TRUE)
}

write_fiscal_table <- function(path) {
  specs <- c("Full sample", "No RTN window", "CDS-covered sample")
  rows <- lapply(specs, function(spec) get_row(
    fiscal_results,
    "specification",
    spec
  ))
  lines <- c(
    "\\begin{table}[htbp]",
    "\\color{revisionred}",
    "\\centering",
    "\\begin{threeparttable}",
    "\\caption{Common-prior Prompt-IV sensitivity across fiscal-news samples}",
    "\\label{tab:fiscal-iv}",
    "\\begin{footnotesize}",
    "\\begin{tabular}{lccc}",
    "\\toprule",
    " & (1) & (2) & (3) \\\\",
    " & Full sample & No RTN window & CDS-covered sample \\\\",
    "\\midrule",
    paste0(
      "Realized tone index & ",
      paste(vapply(rows, function(x) {
        format_estimate(x$prompt_iv, x$prompt_iv_p)
      }, ""), collapse = " & "),
      " \\\\"
    ),
    paste0(
      " & ",
      paste(vapply(rows, function(x) format_se(x$prompt_iv_se), ""), collapse = " & "),
      " \\\\"
    ),
    "\\midrule",
    "Meeting-by-regeneration fixed effects & Yes & Yes & Yes \\\\",
    "Meeting-clustered inference & Yes & Yes & Yes \\\\",
    paste0(
      "Meetings & ",
      paste(vapply(rows, function(x) as.character(x$meeting_clusters), ""), collapse = " & "),
      " \\\\"
    ),
    paste0(
      "Observations & ",
      paste(vapply(rows, function(x) format(x$observations, big.mark = ","), ""), collapse = " & "),
      " \\\\"
    ),
    paste0(
      "Meeting-by-regeneration groups & ",
      paste(vapply(rows, function(x) format(x$pair_fe_groups, big.mark = ","), ""), collapse = " & "),
      " \\\\"
    ),
    paste0(
      "First-stage coefficient & ",
      paste(vapply(rows, function(x) format_number(x$first_stage), ""), collapse = " & "),
      " \\\\"
    ),
    paste0(
      "First-stage $F$ & ",
      paste(vapply(rows, function(x) format_number(x$first_stage_f, 1L), ""), collapse = " & "),
      " \\\\"
    ),
    "\\bottomrule",
    "\\end{tabular}",
    "\\begin{tablenotes}[flushleft]",
    "\\scriptsize",
    paste(
      "\\item \\textit{Notes:} The dependent variable is the calibrated composite",
      "forecast revision under the common-prior design. Column (2) excludes meetings",
      "within five calendar days of an official Tesouro RTN release. Column (3)",
      "retains meetings for which the public five-year Brazil CDS series is available.",
      "Standard errors are clustered by Copom meeting.",
      "* $p<0.10$, ** $p<0.05$, *** $p<0.01$."
    ),
    "\\end{tablenotes}",
    "\\end{footnotesize}",
    "\\end{threeparttable}",
    "\\end{table}"
  )
  writeLines(lines, path, useBytes = TRUE)
}

write_calibration_table <- function(path) {
  display_labels <- c(
    "Immediate common-prior Prompt-IV" =
      "Immediate Prompt-IV, $\\widehat{\\beta}^{IV}$",
    "Focus projection, short run" =
      "Focus projection, short run, $\\widehat{\\beta}^{\\mathrm{Focus}}$",
    "Focus AR(1) persistence" =
      "Focus persistence, $\\widehat{\\rho}^{\\mathrm{Focus}}$"
  )
  lines <- c(
    "\\begin{table}[htbp]",
    "\\color{revisionred}",
    "\\centering",
    "\\begin{threeparttable}",
    "\\caption{Immediate Prompt-IV and observational Focus benchmark}",
    "\\label{tab:summary_calibration}",
    "\\begin{footnotesize}",
    "\\begin{tabular}{lccc}",
    "\\toprule",
    " & Estimate & Std.\\ error & 95\\% confidence interval \\\\",
    "\\midrule"
  )
  for (estimand_name in names(display_labels)) {
    row <- get_row(calibration_results, "estimand", estimand_name)
    lines <- c(
      lines,
      paste0(
        display_labels[[estimand_name]],
        " & ",
        format_number(row$estimate),
        " & ",
        format_number(row$std_error),
        " & ",
        format_interval(row$ci_low, row$ci_high),
        " \\\\"
      )
    )
  }
  lines <- c(
    lines,
    "\\bottomrule",
    "\\end{tabular}",
    "\\begin{tablenotes}[flushleft]",
    "\\scriptsize",
    paste(
      "\\item \\textit{Notes:} The first row is the immediate common-prior laboratory",
      "sensitivity from Table~\\ref{tab:common-prior-main}. The Focus rows come from the",
      "observational autoregressive projection and use Newey--West HAC inference with",
      "four lags. The two equations answer different questions: the Focus coefficient",
      "and persistence are not transformed or divided by Prompt-IV to create a transport ratio."
    ),
    "\\end{tablenotes}",
    "\\end{footnotesize}",
    "\\end{threeparttable}",
    "\\end{table}"
  )
  writeLines(lines, path, useBytes = TRUE)
}

write_inference_table <- function(path) {
  lines <- c(
    "\\begin{table}[htbp]",
    "\\color{revisionred}",
    "\\centering",
    "\\begin{threeparttable}",
    "\\caption{Alternative confidence intervals for the immediate Prompt-IV sensitivity}",
    "\\label{tab:app-iv-inference}",
    "\\begin{footnotesize}",
    "\\begin{tabular}{lccc}",
    "\\toprule",
    "Inference method & Point estimate & Standard error & 95\\% interval \\\\",
    "\\midrule"
  )
  for (method_name in inference_results$method) {
    row <- get_row(inference_results, "method", method_name)
    lines <- c(
      lines,
      paste0(
        method_name,
        " & ",
        format_number(row$estimate),
        " & ",
        format_number(row$std_error),
        " & ",
        format_interval(row$ci_low, row$ci_high),
        " \\\\"
      )
    )
  }
  lines <- c(
    lines,
    "\\bottomrule",
    "\\end{tabular}",
    "\\begin{tablenotes}[flushleft]",
    "\\scriptsize",
    paste(
      "\\item \\textit{Notes:} The first row is pair-fixed-effect 2SLS with standard",
      "errors clustered over 71 meetings. Anderson--Rubin and conditional likelihood-",
      "ratio intervals use \\texttt{ivmodel} after residualizing meeting-by-regeneration fixed",
      "effects. The ordinary cluster bootstrap resamples meetings with replacement",
      sprintf("%s times and treats repeated meeting copies as distinct bootstrap clusters.", bootstrap_reps),
      "It is a finite-sample check and is not labeled as a wild-cluster bootstrap."
    ),
    "\\end{tablenotes}",
    "\\end{footnotesize}",
    "\\end{threeparttable}",
    "\\end{table}"
  )
  writeLines(lines, path, useBytes = TRUE)
}

output_files$main_table_tex <- file.path(
  table_dir, paste0("common_prior_main_iv_", args$stamp, ".tex")
)
output_files$arm_table_tex <- file.path(
  table_dir, paste0("common_prior_dose_arm_effects_", args$stamp, ".tex")
)
output_files$iv_sensitivity_table_tex <- file.path(
  table_dir, paste0("common_prior_iv_sensitivity_", args$stamp, ".tex")
)
output_files$heldout_table_tex <- file.path(
  table_dir, paste0("common_prior_heldout_", args$stamp, ".tex")
)
output_files$fiscal_table_tex <- file.path(
  table_dir, paste0("common_prior_fiscal_", args$stamp, ".tex")
)
output_files$calibration_table_tex <- file.path(
  table_dir, paste0("common_prior_calibration_", args$stamp, ".tex")
)
output_files$inference_table_tex <- file.path(
  table_dir, paste0("common_prior_inference_", args$stamp, ".tex")
)
write_main_table(output_files$main_table_tex)
write_arm_table(output_files$arm_table_tex)
write_iv_sensitivity_table(output_files$iv_sensitivity_table_tex)
write_heldout_table(output_files$heldout_table_tex)
write_fiscal_table(output_files$fiscal_table_tex)
write_calibration_table(output_files$calibration_table_tex)
write_inference_table(output_files$inference_table_tex)

paper_table_files <- list(
  main = file.path(paper_table_dir, "rr_common_prior_main_iv.tex"),
  dose_arms = file.path(paper_table_dir, "rr_common_prior_dose_arm_effects.tex"),
  iv_sensitivity = file.path(paper_table_dir, "rr_common_prior_iv_sensitivity.tex"),
  heldout = file.path(paper_table_dir, "rr_common_prior_heldout.tex"),
  fiscal = file.path(paper_table_dir, "rr_common_prior_fiscal.tex"),
  calibration = file.path(paper_table_dir, "rr_common_prior_calibration.tex"),
  inference = file.path(paper_table_dir, "rr_common_prior_inference.tex")
)
write_main_table(paper_table_files$main)
write_arm_table(paper_table_files$dose_arms)
write_iv_sensitivity_table(paper_table_files$iv_sensitivity)
write_heldout_table(paper_table_files$heldout)
write_fiscal_table(paper_table_files$fiscal)
write_calibration_table(paper_table_files$calibration)
write_inference_table(paper_table_files$inference)
output_files$paper_tables <- paper_table_files

arm_order <- c("dove_high", "dove_low", "original", "neutral", "hawk_low", "hawk_high")
arm_labels <- c(
  dove_high = "High dove",
  dove_low = "Low dove",
  original = "Original",
  neutral = "Neutral",
  hawk_low = "Low hawk",
  hawk_high = "High hawk"
)
arm_colors <- c(
  dove_high = "#0072B2",
  dove_low = "#56B4E9",
  original = "#222222",
  neutral = "#777777",
  hawk_low = "#E69F00",
  hawk_high = "#D55E00"
)
dose_positions <- c(
  dove_high = -2,
  dove_low = -1,
  original = 0,
  neutral = 0,
  hawk_low = 1,
  hawk_high = 2
)

theme_journal <- function() {
  theme_classic(base_size = 10, base_family = "serif") +
    theme(
      axis.line = element_line(linewidth = 0.35, colour = "#222222"),
      axis.ticks = element_line(linewidth = 0.35, colour = "#222222"),
      panel.grid.major.y = element_line(colour = "#E3E3E3", linewidth = 0.3),
      panel.grid.minor = element_blank(),
      legend.position = "bottom",
      legend.title = element_blank(),
      legend.key.width = grid::unit(16, "pt"),
      plot.margin = margin(7, 9, 5, 7),
      plot.title = element_blank()
    )
}

save_plot <- function(plot, stem, width = 7.1, height = 4.0) {
  pdf_path <- file.path(figure_dir, paste0(stem, ".pdf"))
  png_path <- file.path(qa_figure_dir, paste0(stem, ".png"))
  ggsave(
    pdf_path, plot = plot, device = grDevices::cairo_pdf,
    width = width, height = height, units = "in", bg = "white"
  )
  ggsave(
    png_path, plot = plot, device = ragg::agg_png, dpi = 220,
    width = width, height = height, units = "in", bg = "white"
  )
  c(pdf = pdf_path, png = png_path)
}

main_by_draw[, prompt_iv_bp := 100 * prompt_iv]
draw_mean_bp <- mean(main_by_draw$prompt_iv_bp)
draw_plot <- ggplot(main_by_draw, aes(x = prompt_iv_bp)) +
  geom_histogram(
    bins = 8,
    fill = "#D3D3D3",
    colour = "#222222",
    linewidth = 0.35
  ) +
  geom_vline(
    xintercept = draw_mean_bp,
    colour = "#222222",
    linewidth = 0.75,
    linetype = "solid"
  ) +
  annotate(
    "text",
    x = draw_mean_bp,
    y = Inf,
    label = sprintf("Mean = %.2f bp", draw_mean_bp),
    hjust = -0.08,
    vjust = 1.30,
    size = 3.0,
    family = "serif"
  ) +
  scale_x_continuous(
    name = "Regeneration-specific common-prior Prompt-IV effect (basis points per tone point)",
    labels = label_number(accuracy = 0.1),
    expand = expansion(mult = c(0.03, 0.10))
  ) +
  scale_y_continuous(
    name = "Number of regeneration-specific estimates",
    breaks = breaks_pretty(n = 5),
    expand = expansion(mult = c(0, 0.08))
  ) +
  theme_journal() +
  theme(legend.position = "none")
output_files$main_draw_figure <- save_plot(
  draw_plot,
  "rr_common_prior_main_draw_distribution",
  width = 7.1,
  height = 3.8
)

tone_plot_data <- tone_cells[measure == "tone_pp"]
tone_plot_data[, `:=`(
  x = unname(dose_positions[arm]),
  arm = factor(arm, levels = arm_order),
  ci_low = estimate - 1.96 * std_error,
  ci_high = estimate + 1.96 * std_error,
  generated = as.character(arm) != "original"
)]
tone_generated <- tone_plot_data[as.character(arm) != "original"]
tone_original <- tone_plot_data[as.character(arm) == "original"]

tone_plot <- ggplot() +
  geom_line(
    data = tone_generated,
    aes(x = x, y = estimate, group = 1),
    colour = "#555555",
    linewidth = 0.65
  ) +
  geom_errorbar(
    data = tone_generated,
    aes(x = x, ymin = ci_low, ymax = ci_high, colour = arm),
    width = 0.08,
    linewidth = 0.45
  ) +
  geom_point(
    data = tone_generated,
    aes(x = x, y = estimate, colour = arm),
    size = 2.5
  ) +
  geom_errorbar(
    data = tone_original,
    aes(x = x, ymin = ci_low, ymax = ci_high),
    width = 0.08,
    linewidth = 0.45,
    colour = arm_colors[["original"]]
  ) +
  geom_point(
    data = tone_original,
    aes(x = x, y = estimate),
    shape = 18,
    size = 3.2,
    colour = arm_colors[["original"]]
  ) +
  scale_x_continuous(
    name = "Assigned prompt intensity",
    breaks = -2:2,
    labels = c("High\ndove", "Low\ndove", "Neutral", "Low\nhawk", "High\nhawk")
  ) +
  scale_y_continuous(
    name = "Realized lexicon tone (percentage points)",
    labels = label_number(accuracy = 0.1)
  ) +
  scale_colour_manual(
    values = arm_colors,
    labels = arm_labels,
    breaks = arm_order,
    drop = FALSE
  ) +
  guides(colour = guide_legend(nrow = 1, override.aes = list(size = 2.4))) +
  theme_journal()
output_files$dose_tone_figure <- save_plot(
  tone_plot,
  "rr_common_prior_dose_realized_tone",
  width = 7.1,
  height = 4.0
)

effect_plot_data <- arm_effects[sample == "Composite", .(
  arm,
  estimate,
  std_error
)]
effect_plot_data <- rbind(
  effect_plot_data,
  data.table(arm = "original", estimate = 0, std_error = 0)
)
effect_plot_data[, `:=`(
  x = unname(dose_positions[arm]),
  arm = factor(arm, levels = arm_order),
  ci_low_bp = 100 * (estimate - 1.96 * std_error),
  ci_high_bp = 100 * (estimate + 1.96 * std_error),
  estimate_bp = 100 * estimate
)]
effect_generated <- effect_plot_data[as.character(arm) != "original"]
effect_original <- effect_plot_data[as.character(arm) == "original"]

effect_plot <- ggplot() +
  geom_line(
    data = effect_generated,
    aes(x = x, y = estimate_bp, group = 1),
    colour = "#555555",
    linewidth = 0.65
  ) +
  geom_errorbar(
    data = effect_generated,
    aes(x = x, ymin = ci_low_bp, ymax = ci_high_bp, colour = arm),
    width = 0.08,
    linewidth = 0.45
  ) +
  geom_point(
    data = effect_generated,
    aes(x = x, y = estimate_bp, colour = arm),
    size = 2.5
  ) +
  geom_point(
    data = effect_original,
    aes(x = x, y = estimate_bp),
    shape = 18,
    size = 3.2,
    colour = arm_colors[["original"]]
  ) +
  scale_x_continuous(
    name = "Assigned prompt intensity",
    breaks = -2:2,
    labels = c("High\ndove", "Low\ndove", "Neutral", "Low\nhawk", "High\nhawk")
  ) +
  scale_y_continuous(
    name = "Composite revision minus original (basis points)",
    labels = label_number(accuracy = 0.5)
  ) +
  scale_colour_manual(
    values = arm_colors,
    labels = arm_labels,
    breaks = arm_order,
    drop = FALSE
  ) +
  guides(colour = guide_legend(nrow = 1, override.aes = list(size = 2.4))) +
  theme_journal()
output_files$dose_effect_figure <- save_plot(
  effect_plot,
  "rr_common_prior_dose_forecast_effects",
  width = 7.1,
  height = 4.0
)

# Publication figure focused on the requested high-hawk to high-dove gradient.
gradient_order <- c("hawk_high", "hawk_low", "neutral", "dove_low", "dove_high")
gradient_labels <- c(
  hawk_high = "High\nhawk",
  hawk_low = "Low\nhawk",
  neutral = "Neutral",
  dove_low = "Low\ndove",
  dove_high = "High\ndove"
)

gradient_tone <- tone_cells[
  measure == "tone_pp" & arm %in% gradient_order,
  .(arm, estimate, std_error)
]
gradient_tone[, `:=`(
  arm = factor(arm, levels = gradient_order),
  ci_low = estimate - 1.96 * std_error,
  ci_high = estimate + 1.96 * std_error,
  value_label = sprintf("%.2f", estimate)
)]

gradient_effect <- arm_effects[
  sample == "Composite" & arm %in% gradient_order,
  .(arm, estimate = 100 * estimate, std_error = 100 * std_error)
]
gradient_effect[, `:=`(
  arm = factor(arm, levels = gradient_order),
  ci_low = estimate - 1.96 * std_error,
  ci_high = estimate + 1.96 * std_error,
  value_label = sprintf("%+.2f", estimate)
)]

gradient_scale <- scale_colour_manual(
  values = arm_colors,
  breaks = gradient_order,
  guide = "none"
)
gradient_x <- scale_x_discrete(
  name = NULL,
  labels = gradient_labels,
  drop = FALSE
)

gradient_trend <- get_row(
  iv_sensitivity,
  "specification",
  "Ordered five-arm dose"
)
format_plot_p <- function(p_value) {
  if (p_value < 0.001) "p < 0.001" else sprintf("p = %.3f", p_value)
}
tone_trend_label <- sprintf(
  "+%.3f tone p.p. per step toward hawkishness\n%s",
  gradient_trend$first_stage,
  format_plot_p(gradient_trend$first_stage_p)
)
effect_trend_label <- sprintf(
  "+%.2f bp per step toward hawkishness\n%s\n1 bp = 0.01 p.p.",
  100 * gradient_trend$reduced_form,
  format_plot_p(gradient_trend$reduced_form_p)
)

gradient_tone_plot <- ggplot(
  gradient_tone,
  aes(x = arm, y = estimate, group = 1)
) +
  geom_line(colour = "#555555", linewidth = 0.7) +
  geom_errorbar(
    aes(ymin = ci_low, ymax = ci_high, colour = arm),
    width = 0.10,
    linewidth = 0.45
  ) +
  geom_point(aes(colour = arm), size = 2.6) +
  geom_text(
    aes(label = value_label),
    vjust = -1.45,
    size = 2.8,
    family = "serif",
    colour = "#222222"
  ) +
  gradient_scale +
  gradient_x +
  scale_y_continuous(
    name = paste0(
      "Tone measured in generated rewrite\n",
      "(hawk-minus-dove word share, p.p.)"
    ),
    labels = label_number(accuracy = 0.1),
    expand = expansion(mult = c(0.05, 0.16))
  ) +
  annotate(
    "text",
    x = 1,
    y = Inf,
    label = tone_trend_label,
    hjust = 0,
    vjust = 1.25,
    size = 2.35,
    family = "serif",
    lineheight = 0.95
  ) +
  theme_journal() +
  theme(legend.position = "none")

gradient_effect_plot <- ggplot(
  gradient_effect,
  aes(x = arm, y = estimate, group = 1)
) +
  geom_hline(yintercept = 0, colour = "#999999", linewidth = 0.35) +
  geom_line(colour = "#555555", linewidth = 0.7) +
  geom_errorbar(
    aes(ymin = ci_low, ymax = ci_high, colour = arm),
    width = 0.10,
    linewidth = 0.45
  ) +
  geom_point(aes(colour = arm), size = 2.6) +
  geom_text(
    aes(label = value_label),
    vjust = -1.45,
    size = 2.8,
    family = "serif",
    colour = "#222222"
  ) +
  gradient_scale +
  gradient_x +
  scale_y_continuous(
    name = paste0(
      "Synthetic forecast change vs. original minute\n",
      "(bp; 1 bp = 0.01 p.p.)"
    ),
    labels = label_number(accuracy = 0.5),
    expand = expansion(mult = c(0.08, 0.17))
  ) +
  annotate(
    "text",
    x = 1,
    y = Inf,
    label = effect_trend_label,
    hjust = 0,
    vjust = 1.25,
    size = 2.35,
    family = "serif",
    lineheight = 0.95
  ) +
  theme_journal() +
  theme(legend.position = "none")

gradient_plot <- gradient_tone_plot + gradient_effect_plot +
  plot_annotation(tag_levels = "A") &
  theme(
    plot.tag = element_text(face = "bold", family = "serif", size = 10),
    plot.tag.position = c(0.09, 0.99)
  )
output_files$dose_gradient_figure <- save_plot(
  gradient_plot,
  "rr_common_prior_dose_gradient",
  width = 7.1,
  height = 3.8
)

contrast_plot_data <- dose_contrasts[
  sample == "Composite" & contrast != "Neutral - low-dose midpoint"
]
contrast_plot_data[, `:=`(
  contrast = factor(contrast, levels = rev(c(
    "High hawk - high dove",
    "Low hawk - low dove",
    "High spread - low spread",
    "High hawk - low hawk",
    "Low dove - high dove",
    "Neutral - original"
  ))),
  estimate_bp = 100 * estimate,
  ci_low_bp = 100 * (estimate - 1.96 * std_error),
  ci_high_bp = 100 * (estimate + 1.96 * std_error)
)]

contrast_plot <- ggplot(
  contrast_plot_data,
  aes(y = contrast, x = estimate_bp)
) +
  geom_vline(xintercept = 0, colour = "#777777", linewidth = 0.4, linetype = "solid") +
  geom_errorbar(
    aes(xmin = ci_low_bp, xmax = ci_high_bp),
    orientation = "y",
    width = 0.16,
    linewidth = 0.5,
    colour = "#222222"
  ) +
  geom_point(size = 2.5, colour = "#222222") +
  scale_x_continuous(
    name = "Paired forecast-revision contrast (basis points)",
    labels = label_number(accuracy = 0.5),
    expand = expansion(mult = c(0.06, 0.08))
  ) +
  scale_y_discrete(name = NULL) +
  theme_journal() +
  theme(legend.position = "none")
output_files$dose_contrast_figure <- save_plot(
  contrast_plot,
  "rr_common_prior_dose_pairwise_contrasts",
  width = 7.1,
  height = 3.8
)

analysis_report <- list(
  status = "complete",
  generated_at = format(Sys.time(), tz = "America/Sao_Paulo", usetz = TRUE),
  implementation = paste(
    "Econometrics in R using fixest::feols for absorbed pair fixed effects,",
    "meeting-clustered covariance matrices, and 2SLS; coefficients independently",
    "verified with residualized ivreg::ivreg and sandwich::vcovCL; downstream",
    "delta-method, weak-IV, and meeting-cluster bootstrap inference uses",
    "car, ivmodel, and boot."
  ),
  packages = lapply(
    c(
      "RSQLite", "data.table", "fixest", "haven", "ivreg", "sandwich",
      "lmtest", "car", "ivmodel", "boot", "ggplot2"
    ),
    function(package) as.character(packageVersion(package))
  ),
  database = database_path,
  calibration = list(
    dataset = dataset_path,
    observations = nobs(mixture_model),
    r_squared = summary(mixture_model)$r.squared,
    coefficients = as.list(setNames(unname(mixture_weights), names(mixture_weights))),
    pre_2024_observations = nobs(mixture_model_pre_2024),
    pre_2024_coefficients = as.list(setNames(
      unname(mixture_weights_pre_2024),
      names(mixture_weights_pre_2024)
    )),
    counterfactual_rule = paste(
      "Only anchored and unanchored forecast revisions vary across textual arms;",
      "meeting controls and original-text length are frozen and absorbed by pair fixed effects."
    )
  ),
  runs = list(
    main = args$main_run_id,
    dose = args$dose_run_id,
    main_source = args$main_source_run_id,
    dose_source = args$dose_source_run_id
  ),
  external_controls = external_controls_path,
  inference_settings = list(
    bootstrap_reps = bootstrap_reps,
    bootstrap_seed = bootstrap_seed,
    bootstrap_cluster = "Copom meeting"
  ),
  sample = list(
    main_agent_rows = nrow(main),
    main_agent_pairs = uniqueN(main$pair_id),
    main_composite_rows = nrow(main_composite),
    main_composite_pairs = uniqueN(main_composite$pair_id),
    main_draws = uniqueN(main$corpus_iteration),
    dose_agent_rows = nrow(dose),
    dose_agent_pairs = uniqueN(dose$pair_id),
    dose_composite_rows = nrow(dose_composite),
    dose_composite_pairs = uniqueN(dose_composite$pair_id),
    dose_draws = uniqueN(dose$corpus_iteration),
    meetings = 71L
  ),
  main_results = main_results,
  main_two_way_clustered = main_two_way,
  main_two_way_vcov_warnings = as.list(unique(two_way_vcov_warnings)),
  main_draw_distribution = main_draw_summary,
  main_forecaster_heterogeneity = main_heterogeneity,
  heldout_common_prior = heldout_results,
  fiscal_common_prior = fiscal_results,
  focus_calibration = calibration_results,
  alternative_inference = inference_results,
  ivmodel_confidence_intervals = ivmodel_ci,
  dose_arm_effects = arm_effects,
  dose_pairwise_contrasts = dose_contrasts,
  dose_forecaster_heterogeneity = dose_heterogeneity,
  dose_iv_sensitivity = iv_sensitivity,
  dose_alternative_tone_measures = iv_alternative_measures,
  dose_quadratic = quadratic_result,
  dose_outcome_monotonicity = monotonicity,
  ivreg_verification = ivreg_verification,
  outputs = output_files
)

report_path <- file.path(
  table_dir,
  paste0("common_prior_full_analysis_", args$stamp, ".json")
)
jsonlite::write_json(
  release_metadata(analysis_report, root, args$stamp),
  report_path,
  auto_unbox = TRUE,
  pretty = TRUE,
  na = "null",
  digits = NA
)
output_files$analysis_json <- report_path

cat("\nMAIN COMMON-PRIOR RESULTS\n")
print(main_results)
cat("\nMAIN TWO-WAY-CLUSTER ROBUSTNESS\n")
print(main_two_way)
cat("\nMAIN CROSS-REGENERATION SUMMARY\n")
print(main_draw_summary)
cat("\nMAIN FORECASTER HETEROGENEITY\n")
print(main_heterogeneity)
cat("\nHELD-OUT COMMON-PRIOR RESULTS\n")
print(heldout_results)
cat("\nFISCAL-SAMPLE COMMON-PRIOR RESULTS\n")
print(fiscal_results)
cat("\nFOCUS CALIBRATION WITH IMMEDIATE PROMPT-IV\n")
print(calibration_results)
cat("\nALTERNATIVE INFERENCE\n")
print(inference_results)
cat("\nDOSE-RESPONSE PAIRED CONTRASTS - COMPOSITE\n")
print(dose_contrasts[sample == "Composite"])
cat("\nPROMPT-IV SENSITIVITY\n")
print(iv_sensitivity)
cat("\nOUTCOME MONOTONICITY\n")
print(monotonicity)
cat("\nIVREG COEFFICIENT VERIFICATION\n")
print(ivreg_verification)
cat("\nOUTPUTS\n")
print(output_files)
print(gc())
