suppressPackageStartupMessages({
  library(DBI)
  library(RSQLite)
  library(data.table)
  library(haven)
  library(sandwich)
  library(lmtest)
  library(car)
  library(glmnet)
  library(e1071)
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

con <- dbConnect(SQLite(), database_path)
on.exit(dbDisconnect(con), add = TRUE)
dbExecute(con, "PRAGMA busy_timeout = 60000")

baseline <- as.data.table(read_dta(dataset_path))
baseline[, date_daily := as.Date(date_daily)]
setorder(baseline, reuniao)

sgs <- as.data.table(dbGetQuery(
  con,
  "SELECT series_code, series_name, date, value FROM external_bcb_sgs"
))
sgs[, date := as.Date(date)]
setorder(sgs, series_code, date)

focus <- as.data.table(dbGetQuery(
  con,
  paste(
    "SELECT source_entity, indicator, date, reference_period, meeting,",
    "calculation_type, mean, median, sd, min, max, respondents",
    "FROM external_focus_statistics"
  )
))
focus[, date := as.Date(date)]
setorder(focus, source_entity, date)

series_history <- function(code, cutoff, count = 1L) {
  values <- sgs[series_code == code & date <= cutoff]
  if (!nrow(values)) return(numeric())
  tail(values$value, count)
}

series_latest <- function(code, cutoff) {
  values <- series_history(code, cutoff, 1L)
  if (!length(values)) NA_real_ else values[[1]]
}

series_nearest <- function(code, target, max_distance = 45L) {
  values <- sgs[series_code == code]
  if (!nrow(values)) return(NA_real_)
  distances <- abs(as.integer(values$date - target))
  index <- which.min(distances)
  if (distances[[index]] > max_distance) return(NA_real_)
  values$value[[index]]
}

focus_latest <- function(entity, cutoff, calculation_type_filter = NULL, meeting_filter = NULL) {
  values <- focus[source_entity == entity & date <= cutoff]
  if (!is.null(calculation_type_filter)) {
    values <- values[values$calculation_type == calculation_type_filter]
  }
  if (!is.null(meeting_filter)) values <- values[values$meeting == meeting_filter]
  if (!nrow(values)) return(NULL)
  values[which.max(date)]
}

baseline[, calendar_year := as.integer(format(date_daily, "%Y"))]
baseline[, meeting_in_year := seq_len(.N), by = calendar_year]
baseline[calendar_year == 2016L, meeting_in_year := meeting_in_year + 7L]
baseline[, focus_meeting_code := sprintf("R%d/%d", meeting_in_year, calendar_year)]

build_external_row <- function(date, meeting_code) {
  ipca15 <- series_history(7478L, date - 20L, 2L)
  ibc <- series_history(24364L, date - 45L, 2L)
  brl <- series_history(10813L, date - 1L, 21L)
  focus_now <- focus_latest("ExpectativasMercadoInflacao12Meses", date - 1L)
  focus_week <- focus_latest("ExpectativasMercadoInflacao12Meses", date - 8L)
  focus_selic <- focus_latest(
    "ExpectativasMercadoSelic",
    date - 1L,
    meeting_filter = meeting_code
  )
  selic_pre <- series_latest(432L, date)
  selic_post <- series_latest(432L, date + 1L)

  brl_return <- if (length(brl) >= 6L) 100 * log(tail(brl, 1L) / brl[[length(brl) - 5L]]) else NA_real_
  brl_volatility <- if (length(brl) >= 10L) {
    100 * sqrt(252) * sd(diff(log(brl)))
  } else {
    NA_real_
  }

  data.table(
    ipca15_change_pre = if (length(ipca15) == 2L) diff(ipca15) else NA_real_,
    ibc_change_pre = if (length(ibc) == 2L) 100 * log(ibc[[2]] / ibc[[1]]) else NA_real_,
    brl_return_5d_pre = brl_return,
    brl_volatility_20d_pre = brl_volatility,
    focus_drift_7d_pre = if (!is.null(focus_now) && !is.null(focus_week)) {
      focus_now$median[[1]] - focus_week$median[[1]]
    } else {
      NA_real_
    },
    focus_selic_expected = if (is.null(focus_selic)) NA_real_ else focus_selic$median[[1]],
    selic_target_pre = selic_pre,
    selic_target_post = selic_post,
    selic_surprise = if (is.null(focus_selic)) NA_real_ else selic_post - focus_selic$median[[1]]
  )
}

external <- rbindlist(Map(
  build_external_row,
  baseline$date_daily,
  baseline$focus_meeting_code
))
external <- cbind(
  baseline[, .(meeting_number = reuniao, date = date_daily, focus_meeting_code)],
  external
)

focus_meeting_rows <- rbindlist(lapply(baseline$date_daily, function(date) {
  aggregate <- focus_latest("ExpectativasMercadoInflacao12Meses", date - 1L)
  top5_short <- focus_latest(
    "ExpectativasMercadoTop5Inflacao12Meses",
    date - 1L,
    calculation_type_filter = "CURTO_PRAZO"
  )
  top5_medium <- focus_latest(
    "ExpectativasMercadoTop5Inflacao12Meses",
    date - 1L,
    calculation_type_filter = "MEDIO_PRAZO"
  )
  top5_long <- focus_latest(
    "ExpectativasMercadoTop5Inflacao12Meses",
    date - 1L,
    calculation_type_filter = "LONGO_PRAZO"
  )
  get_value <- function(row, field) if (is.null(row)) NA_real_ else row[[field]][[1]]
  data.table(
    focus_mean = get_value(aggregate, "mean"),
    focus_median = get_value(aggregate, "median"),
    focus_sd = get_value(aggregate, "sd"),
    focus_min = get_value(aggregate, "min"),
    focus_max = get_value(aggregate, "max"),
    focus_respondents = get_value(aggregate, "respondents"),
    top5_short_median = get_value(top5_short, "median"),
    top5_medium_median = get_value(top5_medium, "median"),
    top5_long_median = get_value(top5_long, "median")
  )
}))
focus_meeting <- cbind(
  baseline[, .(meeting_number = reuniao, date = date_daily)],
  focus_meeting_rows
)

dbWithTransaction(con, {
  dbWriteTable(
    con,
    "referee_meeting_macro_controls",
    transform(external, date = as.character(date)),
    overwrite = TRUE
  )
  dbWriteTable(
    con,
    "referee_focus_meeting_benchmarks",
    transform(focus_meeting, date = as.character(date)),
    overwrite = TRUE
  )
})

# D2: assignment balance is exact by construction; realized treatment intensity
# is tested against the pre-meeting macro-financial vector at the meeting level.
tone <- as.data.table(dbGetQuery(
  con,
  paste(
    "SELECT meeting_number, iteration, role, 100.0 * score AS tone_pp",
    "FROM tone_scores",
    "WHERE run_id = 'v10_referee_d5_25draws_20260620'",
    "AND role IN ('hawk_text','dove_text')"
  )
))
tone_wide <- dcast(tone, meeting_number + iteration ~ role, value.var = "tone_pp")
tone_wide[, tone_gap := hawk_text - dove_text]
tone_gap <- tone_wide[, .(
  mean_tone_gap = mean(tone_gap),
  sd_tone_gap = sd(tone_gap)
), by = meeting_number]

orthogonality <- merge(tone_gap, external, by = "meeting_number")
orthogonality_variables <- c(
  "ipca15_change_pre", "ibc_change_pre", "brl_return_5d_pre",
  "focus_drift_7d_pre", "selic_surprise"
)
orthogonality <- orthogonality[complete.cases(orthogonality[, ..orthogonality_variables])]
scaled_names <- paste0("z_", orthogonality_variables)
orthogonality[, (scaled_names) := lapply(.SD, function(x) as.numeric(scale(x))),
  .SDcols = orthogonality_variables]
orthogonality_model <- lm(
  reformulate(scaled_names, response = "mean_tone_gap"),
  data = orthogonality
)
orthogonality_vcov <- vcovHC(orthogonality_model, type = "HC2")
orthogonality_ct <- coeftest(orthogonality_model, vcov. = orthogonality_vcov)
orthogonality_table <- data.table(
  term = rownames(orthogonality_ct),
  estimate = orthogonality_ct[, "Estimate"],
  std_error = orthogonality_ct[, "Std. Error"],
  statistic = orthogonality_ct[, "t value"],
  p_value = orthogonality_ct[, "Pr(>|t|)"],
  observations = nobs(orthogonality_model)
)
joint_test <- linearHypothesis(
  orthogonality_model,
  paste0(scaled_names, " = 0"),
  vcov. = orthogonality_vcov,
  test = "F"
)
d2_joint <- data.table(
  test = "Joint orthogonality of realized hawk-dove tone gap",
  statistic = joint_test$F[[2]],
  numerator_df = joint_test$Df[[2]],
  denominator_df = joint_test$Res.Df[[2]],
  p_value = joint_test$`Pr(>F)`[[2]],
  observations = nobs(orthogonality_model)
)
d2_assignment_balance <- data.table(
  covariate = orthogonality_variables,
  hawk_mean = vapply(orthogonality_variables, function(x) mean(orthogonality[[x]]), numeric(1)),
  dove_mean = vapply(orthogonality_variables, function(x) mean(orthogonality[[x]]), numeric(1)),
  standardized_difference = 0,
  explanation = "Each meeting-regeneration pair contains one hawk and one dove assignment."
)

# D9: official Focus aggregates and Top-5 are available; the institution-level
# endpoint is unavailable because BCB deactivated it for confidentiality.
dose_original <- as.data.table(dbGetQuery(
  con,
  paste(
    "SELECT meeting_number, corpus_iteration, forecaster_type, new_expectation",
    "FROM agent_results_latest",
    "WHERE run_id = 'common_prior_dose_6arm_5draws_v1_20260713'",
    "AND source_role = 'original' AND status = 'completed'"
  )
))
if (nrow(dose_original) != 710L) {
  stop(sprintf("Expected 710 common-prior original-arm rows, found %d", nrow(dose_original)))
}
synthetic_distribution <- dose_original[, .(
  synthetic_mean = mean(new_expectation),
  synthetic_sd = sd(new_expectation),
  synthetic_min = min(new_expectation),
  synthetic_max = max(new_expectation),
  synthetic_draws = .N
), by = meeting_number]
d9_meeting <- merge(focus_meeting, synthetic_distribution, by = "meeting_number")
d9_meeting[, `:=`(
  normalized_focus_dispersion = focus_sd / abs(focus_median),
  synthetic_to_focus_sd_ratio = synthetic_sd / focus_sd,
  independent_aggregation_benchmark = sqrt(2 / focus_respondents)
)]
d9_distribution_summary <- d9_meeting[, .(
  meetings = .N,
  mean_focus_respondents = mean(focus_respondents, na.rm = TRUE),
  mean_focus_sd = mean(focus_sd, na.rm = TRUE),
  mean_normalized_focus_dispersion = mean(normalized_focus_dispersion, na.rm = TRUE),
  mean_synthetic_sd = mean(synthetic_sd, na.rm = TRUE),
  mean_synthetic_to_focus_sd_ratio = mean(synthetic_to_focus_sd_ratio, na.rm = TRUE),
  mean_independent_aggregation_benchmark = mean(independent_aggregation_benchmark, na.rm = TRUE),
  correlation_date_level_dispersion = cor(focus_sd, synthetic_sd, use = "complete.obs")
)]

calibration_data <- merge(
  baseline,
  focus_meeting[, .(
    meeting_number,
    top5_short_median,
    top5_medium_median,
    top5_long_median
  )],
  by.x = "reuniao",
  by.y = "meeting_number"
)

calibration_terms <- c("forecast_anchored_gpt55", "forecast_unanchored_gpt55")
fit_top5 <- function(outcome, label) {
  formula <- as.formula(paste(
    outcome,
    "~ forecast_anchored_gpt55 + forecast_unanchored_gpt55 + ibcbr_agro + ibcbr_industry + ibcbr_service"
  ))
  model <- lm(formula, data = calibration_data)
  vc <- vcovHC(model, type = "HC2")
  ct <- coeftest(model, vcov. = vc)
  rbindlist(lapply(calibration_terms, function(term) {
    data.table(
      benchmark = label,
      term = term,
      estimate = ct[term, "Estimate"],
      std_error = ct[term, "Std. Error"],
      statistic = ct[term, "t value"],
      p_value = ct[term, "Pr(>|t|)"],
      observations = nobs(model),
      r_squared = summary(model)$r.squared
    )
  }))
}
d9_top5 <- rbindlist(list(
  fit_top5("top5_short_median", "Top-5 short horizon"),
  fit_top5("top5_medium_median", "Top-5 medium horizon"),
  fit_top5("top5_long_median", "Top-5 long horizon")
))
d9_access <- data.table(
  requested_source = c("Focus aggregate distribution", "Focus Top-5", "Focus institution panel"),
  status = c("collected", "collected", "unavailable"),
  detail = c(
    "Mean, median, standard deviation, minimum, maximum, and respondent count.",
    "Short-, medium-, and long-horizon Top-5 statistics.",
    "Official BCB endpoint deactivated because of microdata confidentiality risk."
  )
)

# D12 and D23: realized-horizon errors use official trailing 12-month IPCA at
# the closest monthly observation one year after each meeting.
validation <- merge(
  merge(baseline, focus_meeting, by.x = "reuniao", by.y = "meeting_number"),
  external,
  by.x = "reuniao",
  by.y = "meeting_number"
)
validation[, realized_ipca_12m_horizon := vapply(
  date_daily + 365L,
  function(target) series_nearest(13522L, target, 45L),
  numeric(1)
)]

d6_summary <- read_json(
  file.path(table_dir, paste0("referee_d6_bayesian_calibration_", stamp, ".json")),
  simplifyVector = TRUE
)
phi_a <- d6_summary$hierarchical_dirichlet$summary$mean[
  d6_summary$hierarchical_dirichlet$summary$parameter == "Anchored weight"
]
phi_u <- 1 - phi_a
validation[, synthetic_convex :=
  phi_a * forecast_anchored_gpt55 + phi_u * forecast_unanchored_gpt55]

cg_row <- function(name, forecast) {
  work <- data.table(
    forecast = forecast,
    realized = validation$realized_ipca_12m_horizon
  )
  work[, revision := forecast - shift(forecast)]
  work[, forecast_error := realized - forecast]
  work <- work[complete.cases(work)]
  model <- lm(forecast_error ~ revision, data = work)
  vc <- NeweyWest(model, lag = 4L, prewhite = FALSE, adjust = TRUE)
  ct <- coeftest(model, vcov. = vc)
  beta <- unname(coef(model)[["revision"]])
  se <- unname(ct["revision", "Std. Error"])
  gain <- 1 / (1 + beta)
  gain_se <- abs(-1 / (1 + beta)^2) * se
  data.table(
    forecaster = name,
    observations = nobs(model),
    cg_beta = beta,
    cg_beta_se_hac = se,
    cg_beta_p_hac = unname(ct["revision", "Pr(>|t|)"]),
    weight_on_signal_g = gain,
    weight_on_signal_se_delta = gain_se,
    realized_target = "Official IPCA accumulated over 12 months near t+365"
  )
}
d23_cg <- rbindlist(list(
  cg_row("Focus aggregate median", validation$focus_median),
  cg_row("LLM anchored", validation$forecast_anchored_gpt55),
  cg_row("LLM unanchored", validation$forecast_unanchored_gpt55),
  cg_row("LLM convex composite", validation$synthetic_convex)
))

focus_error <- data.table(
  error = validation$realized_ipca_12m_horizon - validation$focus_median
)
focus_error[, lag_error := shift(error)]
focus_error <- focus_error[complete.cases(focus_error)]
error_ar <- lm(error ~ lag_error, data = focus_error)
error_ar_vcov <- NeweyWest(error_ar, lag = 4L, prewhite = FALSE, adjust = TRUE)
error_ar_ct <- coeftest(error_ar, vcov. = error_ar_vcov)

composite_model <- lm(
  inflacao12m ~ synthetic_convex + ibcbr_agro + ibcbr_industry + ibcbr_service,
  data = validation
)
composite_residual <- data.table(residual = residuals(composite_model))
composite_residual[, lag_residual := shift(residual)]
composite_residual <- composite_residual[complete.cases(composite_residual)]
residual_ar <- lm(residual ~ lag_residual, data = composite_residual)
residual_ar_vcov <- NeweyWest(residual_ar, lag = 4L, prewhite = FALSE, adjust = TRUE)
residual_ar_ct <- coeftest(residual_ar, vcov. = residual_ar_vcov)

d8_loo_path <- file.path(
  table_dir,
  paste0("referee_d8_loo_summary_", stamp, ".csv")
)
if (!file.exists(d8_loo_path)) {
  stop("Run run_referee_d8_oos_validation.R before external validation")
}
d8_loo <- fread(d8_loo_path)
if (nrow(d8_loo) != 1L ||
    d8_loo$n[[1L]] != 71L ||
    !is.finite(d8_loo$r2_cv[[1L]])) {
  stop("D8 leave-one-meeting-out summary is malformed")
}

focus_updates <- diff(validation$focus_median)
d12_moments <- data.table(
  statistic = c(
    "Mean normalized Focus dispersion",
    "Focus forecast-error AR(1)",
    "Focus update excess kurtosis",
    "Convex-composite residual AR(1)",
    "LOO cross-validated R-squared"
  ),
  estimate = c(
    mean(d9_meeting$normalized_focus_dispersion, na.rm = TRUE),
    unname(coef(error_ar)[["lag_error"]]),
    e1071::kurtosis(focus_updates, na.rm = TRUE, type = 2),
    unname(coef(residual_ar)[["lag_residual"]]),
    d8_loo$r2_cv[[1L]]
  ),
  std_error = c(
    NA_real_,
    unname(error_ar_ct["lag_error", "Std. Error"]),
    NA_real_,
    unname(residual_ar_ct["lag_residual", "Std. Error"]),
    NA_real_
  ),
  observations = c(
    sum(is.finite(d9_meeting$normalized_focus_dispersion)),
    nobs(error_ar),
    sum(is.finite(focus_updates)),
    nobs(residual_ar),
    d8_loo$n[[1L]]
  )
)

# D13: a gain grid expands both original-text forecasters. Nonnegative lasso
# selection is estimated with glmnet and leave-one-meeting-out folds.
trajectory <- as.data.table(dbGetQuery(
  con,
  paste(
    "SELECT meeting_number, forecaster_type, previous_expectation, new_expectation",
    "FROM agent_results_latest",
    "WHERE run_id = 'common_prior_original_v1_20260712' AND status = 'completed'"
  )
))
if (nrow(trajectory) != 142L) stop("Original common-prior trajectory is incomplete")
trajectory_wide <- dcast(
  trajectory,
  meeting_number ~ forecaster_type,
  value.var = c("previous_expectation", "new_expectation")
)
gains <- c(0.25, 0.50, 0.75, 1.00, 1.25)
agent_columns <- character()
for (type in c("anchored", "unanchored")) {
  prior_col <- paste0("previous_expectation_", type)
  new_col <- paste0("new_expectation_", type)
  for (gain in gains) {
    name <- sprintf("%s_gain_%03d", type, as.integer(100 * gain))
    trajectory_wide[, (name) := get(prior_col) + gain * (get(new_col) - get(prior_col))]
    agent_columns <- c(agent_columns, name)
  }
}
ecology <- merge(
  baseline,
  trajectory_wide[, c("meeting_number", agent_columns), with = FALSE],
  by.x = "reuniao",
  by.y = "meeting_number"
)
ecology_controls <- c("ibcbr_agro", "ibcbr_industry", "ibcbr_service")
ecology_x <- as.matrix(ecology[, c(agent_columns, ecology_controls), with = FALSE])
set.seed(seed)
ecology_cv <- cv.glmnet(
  x = ecology_x,
  y = ecology$inflacao12m,
  alpha = 1,
  nfolds = nrow(ecology),
  foldid = seq_len(nrow(ecology)),
  penalty.factor = c(rep(1, length(agent_columns)), rep(0, length(ecology_controls))),
  lower.limits = c(rep(0, length(agent_columns)), rep(-Inf, length(ecology_controls))),
  standardize = TRUE,
  grouped = FALSE,
  keep = TRUE
)
lambda_index <- which.min(abs(ecology_cv$lambda - ecology_cv$lambda.min))
ecology_prediction <- ecology_cv$fit.preval[, lambda_index]
ecology_coefficients <- as.matrix(coef(ecology_cv, s = "lambda.min"))
d13_ecology <- data.table(
  predictor = rownames(ecology_coefficients),
  coefficient = ecology_coefficients[, 1]
)
d13_ecology[, `:=`(
  selected_lambda = ecology_cv$lambda.min,
  loo_rmse = sqrt(mean((ecology$inflacao12m - ecology_prediction)^2)),
  loo_r_squared = 1 - sum((ecology$inflacao12m - ecology_prediction)^2) /
    sum((ecology$inflacao12m - mean(ecology$inflacao12m))^2),
  observations = nrow(ecology)
)]

# D22: macro correlates of original-minute tone. All continuous regressors are
# standardized; calendar indicators capture chair and fiscal-regime episodes.
macro <- merge(baseline, external, by.x = "reuniao", by.y = "meeting_number")
macro[, `:=`(
  post_campos_neto = as.integer(date_daily >= as.Date("2019-03-20")),
  post_2021 = as.integer(date_daily >= as.Date("2021-01-01")),
  lula_fiscal_regime = as.integer(date_daily >= as.Date("2023-01-01"))
)]
macro_continuous <- c(
  "selic_target_pre", "ibc_change_pre", "brl_volatility_20d_pre",
  "ipca15_change_pre", "focus_drift_7d_pre"
)
macro_scaled <- paste0("z_", macro_continuous)
macro[, (macro_scaled) := lapply(.SD, function(x) as.numeric(scale(x))),
  .SDcols = macro_continuous]
macro_model <- lm(
  reformulate(
    c(macro_scaled, "post_campos_neto", "post_2021", "lula_fiscal_regime"),
    response = "score_orig"
  ),
  data = macro
)
macro_vcov <- vcovHC(macro_model, type = "HC2")
macro_ct <- coeftest(macro_model, vcov. = macro_vcov)
d22_macro <- data.table(
  term = rownames(macro_ct),
  estimate = macro_ct[, "Estimate"],
  std_error = macro_ct[, "Std. Error"],
  statistic = macro_ct[, "t value"],
  p_value = macro_ct[, "Pr(>|t|)"],
  observations = nobs(macro_model),
  r_squared = summary(macro_model)$r.squared
)
d22_bonomo <- data.table(
  requested_indicator = "Bonomo et al. anchored-versus-unanchored regime",
  estimable = FALSE,
  sample_start = min(baseline$date_daily),
  sample_end = max(baseline$date_daily),
  reason = "The sample begins after the 2011-2016 unanchoring/reanchoring episode."
)

write_table <- function(data, name) {
  path <- file.path(table_dir, paste0(name, "_", stamp, ".csv"))
  fwrite(data, path)
  path
}

outputs <- list(
  d2_assignment = write_table(d2_assignment_balance, "referee_d2_assignment_balance"),
  d2_tone = write_table(orthogonality_table, "referee_d2_tone_gap_orthogonality"),
  d2_joint = write_table(d2_joint, "referee_d2_joint_orthogonality"),
  d9_meeting = write_table(d9_meeting, "referee_d9_distribution_by_meeting"),
  d9_summary = write_table(d9_distribution_summary, "referee_d9_distribution_summary"),
  d9_top5 = write_table(d9_top5, "referee_d9_top5_calibration"),
  d9_access = write_table(d9_access, "referee_d9_data_access"),
  d12 = write_table(d12_moments, "referee_d12_moment_battery"),
  d13 = write_table(d13_ecology, "referee_d13_predictor_ecology"),
  d22 = write_table(d22_macro, "referee_d22_macro_correlates"),
  d22_bonomo = write_table(d22_bonomo, "referee_d22_bonomo_coverage"),
  d23_cg = write_table(d23_cg, "referee_d23_cg_weights_on_signal")
)

summary_path <- file.path(
  table_dir,
  paste0("referee_external_validation_", stamp, ".json")
)
write_json(
  release_metadata(list(
    generated_at = format(Sys.time(), tz = "UTC", usetz = TRUE),
    implementation = list(
      language = "R",
      packages = c("sandwich", "lmtest", "car", "glmnet", "e1071"),
      external_source = "Official BCB SGS and Focus OData tables stored in SQLite"
    ),
    d2 = list(
      assignment_balance = d2_assignment_balance,
      tone_gap_coefficients = orthogonality_table,
      joint_test = d2_joint
    ),
    d9 = list(
      distribution = d9_distribution_summary,
      top5 = d9_top5,
      access = d9_access
    ),
    d12 = d12_moments,
    d13 = d13_ecology,
    d22 = list(macro_correlates = d22_macro, bonomo_coverage = d22_bonomo),
    d23 = list(cg_weights_on_signal = d23_cg),
    outputs = outputs
  ), root, stamp),
  summary_path,
  auto_unbox = TRUE,
  pretty = TRUE,
  digits = 16
)

cat(sprintf("Completed external-validation modules: %s\n", summary_path))
