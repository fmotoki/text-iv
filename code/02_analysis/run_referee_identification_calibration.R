suppressPackageStartupMessages({
  library(DBI)
  library(RSQLite)
  library(data.table)
  library(haven)
  library(ivreg)
  library(sandwich)
  library(lmtest)
  library(car)
  library(quadprog)
  library(boot)
  library(glmnet)
  library(clusterSEs)
  library(MSwM)
  library(strucchange)
  library(urca)
  library(jsonlite)
})

data.table::setDTthreads(1L)
options(error = function() {
  cat("FATAL R ERROR: ", geterrmessage(), "\n", file = stderr())
  traceback(30)
  quit(save = "no", status = 1L, runLast = FALSE)
})

root <- normalizePath(".", winslash = "/", mustWork = TRUE)
source(file.path(root, "code", "02_analysis", "reproducibility_helpers.R"), local = TRUE)
database_path <- file.path(root, "outputs", "work", "prompt_iv_replication.sqlite")
dataset_path <- file.path(root, "outputs", "work", "gpt55_calibration_data.dta")
out_dir <- file.path(root, "outputs", "tables")
dir.create(out_dir, recursive = TRUE, showWarnings = FALSE)
stamp <- "20260715"
seed <- 20260715L

write_result <- function(data, name) {
  path <- file.path(out_dir, paste0(name, "_", stamp, ".csv"))
  fwrite(data, path, bom = FALSE)
  path
}

coef_row <- function(model, term) {
  table <- coeftable(model)
  if (!term %in% rownames(table)) stop(sprintf("Term %s not found", term))
  p_col <- grep("^Pr", colnames(table), value = TRUE)[1]
  data.table(
    estimate = unname(table[term, "Estimate"]),
    std_error = unname(table[term, "Std. Error"]),
    statistic = unname(table[term, "t value"]),
    p_value = unname(table[term, p_col])
  )
}

con <- dbConnect(SQLite(), database_path)
on.exit(dbDisconnect(con), add = TRUE)
dbExecute(con, "PRAGMA busy_timeout = 60000")

read_agent_run <- function(run_id, source_run_id, tone_suffix = "") {
  sql <- paste(
    "SELECT r.run_id, r.task_id, r.meeting_number, r.corpus_iteration,",
    "r.forecaster_type, r.pair_id, r.source_role, r.previous_expectation,",
    "r.new_expectation, r.forecast_revision, a.fixed_prior, a.source_run_id,",
    "a.source_text_hash, 100.0 * ts.score AS tone_pp, ts.hawk_count,",
    "ts.dove_count, ts.word_count",
    "FROM agent_results_latest AS r",
    "JOIN agent_tasks AS a ON a.run_id = r.run_id AND a.task_id = r.task_id",
    "JOIN tone_scores AS ts ON ts.run_id = a.source_run_id",
    " AND ts.meeting_number = r.meeting_number",
    " AND ts.iteration = r.corpus_iteration",
    " AND ts.role = r.source_role || ?",
    "WHERE r.run_id = ? AND r.status = 'completed' AND a.source_run_id = ?"
  )
  as.data.table(dbGetQuery(con, sql, params = list(tone_suffix, run_id, source_run_id)))
}

baseline <- as.data.table(read_dta(dataset_path))
baseline[, date_daily := as.Date(date_daily)]
setorder(baseline, reuniao)

main <- read_agent_run(
  "common_prior_main_25draws_v1_20260712",
  "v10_referee_d5_25draws_20260620",
  "_text"
)
if (nrow(main) != 7100L || uniqueN(main$pair_id) != 3550L) {
  stop("The common-prior 25-draw panel is incomplete")
}
main[, `:=`(
  hawk = as.integer(source_role == "hawk"),
  pair_id = factor(pair_id)
)]

mixture_formula <- inflacao12m ~ forecast_anchored_gpt55 +
  forecast_unanchored_gpt55 + wc_orig + ibcbr_agro +
  ibcbr_service + ibcbr_industry
mixture_model <- lm(mixture_formula, data = baseline)
mixture_weights <- coef(mixture_model)

build_composite <- function(weights, meetings = NULL) {
  wide <- dcast(
    main,
    meeting_number + corpus_iteration + source_role + hawk + tone_pp ~
      forecaster_type,
    value.var = "forecast_revision"
  )
  if (anyNA(wide$anchored) || anyNA(wide$unanchored)) {
    stop("Composite panel is missing an anchored or unanchored response")
  }
  wide[, forecast_revision :=
         unname(weights[["forecast_anchored_gpt55"]]) * anchored +
         unname(weights[["forecast_unanchored_gpt55"]]) * unanchored]
  wide[, pair_id := factor(paste(meeting_number, corpus_iteration, sep = "::"))]
  if (!is.null(meetings)) wide <- wide[meeting_number %in% meetings]
  wide
}

cluster_term <- function(model, term, cluster) {
  vc <- vcovCL(model, cluster = cluster, type = "HC1")
  estimate <- unname(coef(model)[term])
  se <- sqrt(unname(vc[term, term]))
  df <- uniqueN(cluster) - 1L
  data.table(
    estimate = estimate,
    std_error = se,
    statistic = estimate / se,
    p_value = 2 * pt(abs(estimate / se), df = df, lower.tail = FALSE)
  )
}

within_pair <- function(data) {
  data[, .(
    forecast_revision_within = forecast_revision - mean(forecast_revision),
    tone_pp_within = tone_pp - mean(tone_pp),
    hawk_within = hawk - mean(hawk),
    meeting_number = meeting_number
  ), by = pair_id]
}

estimate_iv <- function(data, label) {
  transformed <- within_pair(data)
  rf <- lm(
    forecast_revision_within ~ hawk_within,
    data = transformed
  )
  fs <- lm(
    tone_pp_within ~ hawk_within,
    data = transformed
  )
  iv <- ivreg(
    forecast_revision_within ~ tone_pp_within |
      hawk_within,
    data = transformed
  )
  rf_row <- cluster_term(rf, "hawk_within", transformed$meeting_number)
  fs_row <- cluster_term(fs, "hawk_within", transformed$meeting_number)
  iv_row <- cluster_term(iv, "tone_pp_within", transformed$meeting_number)
  list(
    row = data.table(
      specification = label,
      reduced_form = rf_row$estimate,
      reduced_form_se = rf_row$std_error,
      reduced_form_p = rf_row$p_value,
      first_stage = fs_row$estimate,
      first_stage_se = fs_row$std_error,
      first_stage_f = fs_row$statistic^2,
      prompt_iv = iv_row$estimate,
      prompt_iv_se = iv_row$std_error,
      prompt_iv_p = iv_row$p_value,
      observations = nobs(iv),
      meetings = uniqueN(data$meeting_number),
      draws = uniqueN(data$corpus_iteration)
    ),
    rf = rf,
    fs = fs,
    iv = iv,
    transformed = transformed
  )
}

main_composite <- build_composite(mixture_weights)
cat(sprintf(
  paste0(
    "Agent columns: %s; wide columns: %s; mixture weights: anchored=%s, ",
    "unanchored=%s; finite agent rows: %d/%d and %d/%d; composite finite rows: %d/%d\n"
  ),
  paste(sort(unique(main$forecaster_type)), collapse = ", "),
  paste(names(main_composite), collapse = ", "),
    format(unname(mixture_weights[["forecast_anchored_gpt55"]]), digits = 8),
    format(unname(mixture_weights[["forecast_unanchored_gpt55"]]), digits = 8),
  sum(is.finite(main_composite$anchored)),
  nrow(main_composite),
  sum(is.finite(main_composite$unanchored)),
  nrow(main_composite),
  sum(is.finite(main_composite$forecast_revision)),
  nrow(main_composite)
))
flush.console()
main_iv <- estimate_iv(main_composite, "Current common-prior composite")
cat("STAGE 1/6: panel and main IV loaded\n")
flush.console()

# D5: complete cross-regeneration reporting and placebo outcome null.
forecaster_draw <- main[, .(
  tone_pp = mean(tone_pp),
  forecast_level = mean(new_expectation),
  forecast_revision = mean(forecast_revision)
), by = .(corpus_iteration, source_role, forecaster_type)]

forecaster_cell_sd <- main[, .(
  cell_tone_sd = sd(tone_pp),
  cell_forecast_level_sd = sd(new_expectation),
  cell_forecast_revision_sd = sd(forecast_revision)
), by = .(meeting_number, source_role, forecaster_type)]

d5_forecaster_summary <- merge(
  forecaster_draw[, .(
    mean_tone = mean(tone_pp),
    between_draw_sd_tone = sd(tone_pp),
    mean_forecast_level = mean(forecast_level),
    between_draw_sd_forecast_level = sd(forecast_level),
    mean_forecast_revision = mean(forecast_revision),
    between_draw_sd_forecast_revision = sd(forecast_revision)
  ), by = .(source_role, forecaster_type)],
  forecaster_cell_sd[, .(
    mean_within_cell_sd_tone = mean(cell_tone_sd),
    mean_within_cell_sd_forecast_level = mean(cell_forecast_level_sd),
    mean_within_cell_sd_forecast_revision = mean(cell_forecast_revision_sd)
  ), by = .(source_role, forecaster_type)],
  by = c("source_role", "forecaster_type")
)

composite_draw <- main_composite[, .(
  tone_pp = mean(tone_pp),
  forecast_revision = mean(forecast_revision)
), by = .(corpus_iteration, source_role)]
composite_cell_sd <- main_composite[, .(
  cell_tone_sd = sd(tone_pp),
  cell_forecast_revision_sd = sd(forecast_revision)
), by = .(meeting_number, source_role)]
d5_composite_summary <- merge(
  composite_draw[, .(
    mean_tone = mean(tone_pp),
    between_draw_sd_tone = sd(tone_pp),
    mean_forecast_revision = mean(forecast_revision),
    between_draw_sd_forecast_revision = sd(forecast_revision)
  ), by = source_role],
  composite_cell_sd[, .(
    mean_within_cell_sd_tone = mean(cell_tone_sd),
    mean_within_cell_sd_forecast_revision = mean(cell_forecast_revision_sd)
  ), by = source_role],
  by = "source_role"
)
d5_composite_summary[, forecaster_type := "composite"]

retry <- as.data.table(dbGetQuery(
  con,
  paste(
    "SELECT stance, COUNT(*) AS completed_tasks,",
    "SUM(CASE WHEN retry_attempts_used > 0 THEN 1 ELSE 0 END) AS tasks_retried,",
    "SUM(retry_attempts_used) AS rejected_attempts,",
    "SUM(1 + retry_attempts_used) AS total_attempts",
    "FROM rewrite_results_latest",
    "WHERE run_id = 'v10_referee_d5_25draws_20260620' AND status = 'completed'",
    "GROUP BY stance"
  )
))
retry[, `:=`(
  task_retry_rate = tasks_retried / completed_tasks,
  attempt_rejection_rate = rejected_attempts / total_attempts
)]

placebo_wide <- dcast(
  main_composite,
  meeting_number + corpus_iteration + pair_id ~ source_role,
  value.var = c("tone_pp", "forecast_revision")
)
placebo_wide[, `:=`(
  tone_diff = tone_pp_hawk - tone_pp_dove,
  outcome_diff = forecast_revision_hawk - forecast_revision_dove
)]

set.seed(seed)
placebo_rows <- vector("list", 999L)
for (b in seq_len(999L)) {
  sign_z <- sample(c(-1, 1), nrow(placebo_wide), replace = TRUE)
  fs_model <- lm(tone_diff ~ sign_z, data = placebo_wide)
  rf_model <- lm(outcome_diff ~ sign_z, data = placebo_wide)
  iv_model <- ivreg(outcome_diff ~ tone_diff | sign_z, data = placebo_wide)
  fs <- cluster_term(fs_model, "sign_z", placebo_wide$meeting_number)
  rf <- cluster_term(rf_model, "sign_z", placebo_wide$meeting_number)
  iv <- cluster_term(iv_model, "tone_diff", placebo_wide$meeting_number)
  placebo_rows[[b]] <- data.table(
    permutation = b,
    first_stage = fs$estimate,
    first_stage_se = fs$std_error,
    first_stage_f = fs$statistic^2,
    reduced_form = rf$estimate,
    reduced_form_se = rf$std_error,
    reduced_form_p = rf$p_value,
    prompt_iv = iv$estimate,
    prompt_iv_se = iv$std_error,
    prompt_iv_p = iv$p_value
  )
}
placebo <- rbindlist(placebo_rows)
placebo_summary <- placebo[, .(
  permutations = .N,
  mean_first_stage_f = mean(first_stage_f),
  p95_first_stage_f = quantile(first_stage_f, 0.95),
  share_first_stage_f_above_10 = mean(first_stage_f > 10),
  mean_reduced_form = mean(reduced_form),
  share_reduced_form_p_below_05 = mean(reduced_form_p < 0.05),
  median_prompt_iv = median(prompt_iv),
  median_prompt_iv_se = median(prompt_iv_se),
  share_prompt_iv_p_below_05 = mean(prompt_iv_p < 0.05),
  prompt_iv_p025 = quantile(prompt_iv, 0.025),
  prompt_iv_p975 = quantile(prompt_iv, 0.975)
)]
cat("STAGE 2/6: D5 cross-draw and scrambled outcome placebo complete\n")
flush.console()

# D6: unconstrained, convex, Dirichlet-compatible, and centered-ridge calibration.
d6_unrestricted <- lm(
  inflacao12m ~ forecast_anchored_gpt55 + forecast_unanchored_gpt55 +
    ibcbr_agro + ibcbr_industry + ibcbr_service,
  data = baseline
)
d6_vcov <- vcovHC(d6_unrestricted, type = "HC2")
d6_coeftest <- coeftest(d6_unrestricted, vcov. = d6_vcov)
d6_test_u1 <- linearHypothesis(
  d6_unrestricted,
  "forecast_unanchored_gpt55 = 1",
  vcov. = d6_vcov,
  test = "F"
)

convex_data <- baseline[, .(
    y_adj = inflacao12m - forecast_unanchored_gpt55,
    diff_agent = forecast_anchored_gpt55 - forecast_unanchored_gpt55,
  ibcbr_agro,
  ibcbr_industry,
  ibcbr_service
)]

fit_convex <- function(data) {
  x <- model.matrix(
    ~ diff_agent + ibcbr_agro + ibcbr_industry + ibcbr_service,
    data = data
  )
  dmat <- 2 * crossprod(x) + diag(1e-9, ncol(x))
  dvec <- 2 * crossprod(x, data$y_adj)[, 1]
  lambda_vector <- rep(0, ncol(x))
  lambda_vector[which(colnames(x) == "diff_agent")] <- 1
  solution <- solve.QP(
    Dmat = dmat,
    dvec = dvec,
    Amat = cbind(lambda_vector, -lambda_vector),
    bvec = c(0, -1),
    meq = 0L
  )$solution
  names(solution) <- colnames(x)
  fitted_y_adj <- as.numeric(x %*% solution)
  list(
    coefficients = solution,
    lambda_anchored = unname(solution[["diff_agent"]]),
    lambda_unanchored = 1 - unname(solution[["diff_agent"]]),
    rmse = sqrt(mean((data$y_adj - fitted_y_adj)^2))
  )
}

convex_fit <- fit_convex(convex_data)
convex_boot_stat <- function(data, indices) {
  fit <- fit_convex(data[indices])
  c(fit$lambda_anchored, fit$lambda_unanchored, fit$rmse)
}
set.seed(seed + 1L)
convex_boot <- boot(convex_data, convex_boot_stat, R = 1999L)
convex_ci <- apply(convex_boot$t, 2, quantile, probs = c(0.025, 0.5, 0.975), na.rm = TRUE)
convex_sd <- apply(convex_boot$t, 2, sd, na.rm = TRUE)

x_ridge <- model.matrix(
  ~ diff_agent + ibcbr_agro + ibcbr_industry + ibcbr_service,
  data = convex_data
)[, -1, drop = FALSE]
y_ridge <- convex_data$y_adj - 0.5 * convex_data$diff_agent
set.seed(seed + 2L)
ridge_cv <- cv.glmnet(
  x = x_ridge,
  y = y_ridge,
  alpha = 0,
  nfolds = nrow(convex_data),
  foldid = seq_len(nrow(convex_data)),
  grouped = FALSE,
  penalty.factor = c(1, 0, 0, 0),
  standardize = TRUE
)
ridge_coef <- as.matrix(coef(ridge_cv, s = "lambda.min"))
ridge_lambda_a <- 0.5 + unname(ridge_coef["diff_agent", 1])

d6_table <- rbindlist(list(
  data.table(
    specification = "Unrestricted OLS, no word count",
    anchored_weight = coef(d6_unrestricted)[["forecast_anchored_gpt55"]],
    unanchored_weight = coef(d6_unrestricted)[["forecast_unanchored_gpt55"]],
    anchored_std_error = sqrt(d6_vcov["forecast_anchored_gpt55", "forecast_anchored_gpt55"]),
    unanchored_std_error = sqrt(d6_vcov["forecast_unanchored_gpt55", "forecast_unanchored_gpt55"]),
    anchored_p_value = d6_coeftest["forecast_anchored_gpt55", 4],
    unanchored_p_value = d6_coeftest["forecast_unanchored_gpt55", 4],
    anchored_ci_low = coef(d6_unrestricted)[["forecast_anchored_gpt55"]] -
      1.96 * sqrt(d6_vcov["forecast_anchored_gpt55", "forecast_anchored_gpt55"]),
    anchored_ci_high = coef(d6_unrestricted)[["forecast_anchored_gpt55"]] +
      1.96 * sqrt(d6_vcov["forecast_anchored_gpt55", "forecast_anchored_gpt55"]),
    unanchored_ci_low = coef(d6_unrestricted)[["forecast_unanchored_gpt55"]] -
      1.96 * sqrt(d6_vcov["forecast_unanchored_gpt55", "forecast_unanchored_gpt55"]),
    unanchored_ci_high = coef(d6_unrestricted)[["forecast_unanchored_gpt55"]] +
      1.96 * sqrt(d6_vcov["forecast_unanchored_gpt55", "forecast_unanchored_gpt55"]),
    rmse = sqrt(mean(resid(d6_unrestricted)^2)),
    constraint = "none"
  ),
  data.table(
    specification = "Convex QP, no word count",
    anchored_weight = convex_fit$lambda_anchored,
    unanchored_weight = convex_fit$lambda_unanchored,
    anchored_std_error = convex_sd[1],
    unanchored_std_error = convex_sd[2],
    anchored_p_value = NA_real_,
    unanchored_p_value = NA_real_,
    anchored_ci_low = convex_ci[1, 1],
    anchored_ci_high = convex_ci[3, 1],
    unanchored_ci_low = convex_ci[1, 2],
    unanchored_ci_high = convex_ci[3, 2],
    rmse = convex_fit$rmse,
    constraint = "weights in [0,1] and sum to one"
  ),
  data.table(
    specification = "Centered ridge selected by leave-one-out CV",
    anchored_weight = ridge_lambda_a,
    unanchored_weight = 1 - ridge_lambda_a,
    anchored_std_error = NA_real_,
    unanchored_std_error = NA_real_,
    anchored_p_value = NA_real_,
    unanchored_p_value = NA_real_,
    anchored_ci_low = NA_real_,
    anchored_ci_high = NA_real_,
    unanchored_ci_low = NA_real_,
    unanchored_ci_high = NA_real_,
    rmse = sqrt(min(ridge_cv$cvm)),
    constraint = "Gaussian ridge prior centered at 0.5"
  )
), fill = TRUE)
d6_wald <- data.table(
  hypothesis = "unanchored weight = 1",
  statistic = d6_test_u1$F[2],
  numerator_df = d6_test_u1$Df[2],
  denominator_df = d6_test_u1$Res.Df[2],
  p_value = d6_test_u1$`Pr(>F)`[2]
)
cat("STAGE 3/6: D6 frequentist constrained calibration complete\n")
flush.console()

# D7: sample splits, rolling windows, threshold model, and Markov states.
sgs <- as.data.table(dbGetQuery(
  con,
  "SELECT series_code, date, value FROM external_bcb_sgs"
))
sgs[, date := as.Date(date)]
latest_before <- function(code, dates, lag_days = 0L) {
  source <- sgs[sgs$series_code == code][order(date)]
  vapply(dates, function(target_date) {
    eligible <- source[date <= target_date - lag_days]
    if (!nrow(eligible)) return(NA_real_)
    eligible$value[[nrow(eligible)]]
  }, numeric(1))
}

baseline[, actual_ipca12 := latest_before(13522L, date_daily, 0L)]
baseline[, high_inflation := actual_ipca12 > median(actual_ipca12, na.rm = TRUE)]

fit_regime <- function(label, meetings) {
  sample <- baseline[reuniao %in% meetings]
  if (nrow(sample) < 12L) return(NULL)
  mix <- lm(mixture_formula, data = sample)
  weights <- coef(mix)
  mix_ct <- coeftest(
    mix,
    vcov. = vcovHC(mix, type = "HC2")
  )
  comp <- build_composite(weights, meetings)
  iv <- estimate_iv(comp, label)$row
  market <- lm(inflacao12m ~ score_orig + l_inflacao12m, data = sample)
  hac <- NeweyWest(
    market,
    lag = min(2L, floor(nobs(market)^(1 / 4))),
    prewhite = FALSE,
    adjust = TRUE
  )
  ct <- coeftest(market, vcov. = hac)
  data.table(
    regime = label,
    start_date = min(sample$date_daily),
    end_date = max(sample$date_daily),
    meetings = nrow(sample),
      anchored_weight = unname(weights[["forecast_anchored_gpt55"]]),
      anchored_weight_se_hc2 = unname(mix_ct["forecast_anchored_gpt55", "Std. Error"]),
      anchored_weight_p = unname(mix_ct["forecast_anchored_gpt55", "Pr(>|t|)"]),
      unanchored_weight = unname(weights[["forecast_unanchored_gpt55"]]),
      unanchored_weight_se_hc2 = unname(mix_ct["forecast_unanchored_gpt55", "Std. Error"]),
      unanchored_weight_p = unname(mix_ct["forecast_unanchored_gpt55", "Pr(>|t|)"]),
    prompt_iv = iv$prompt_iv,
    prompt_iv_se = iv$prompt_iv_se,
    prompt_iv_p = iv$prompt_iv_p,
    first_stage_f = iv$first_stage_f,
    focus_tone = unname(coef(market)[["score_orig"]]),
    focus_tone_se_hac = unname(ct["score_orig", "Std. Error"]),
    focus_tone_p = unname(ct["score_orig", "Pr(>|t|)"]),
    focus_rho = unname(coef(market)[["l_inflacao12m"]]),
    focus_rho_se_hac = unname(ct["l_inflacao12m", "Std. Error"]),
    focus_rho_p = unname(ct["l_inflacao12m", "Pr(>|t|)"])
  )
}

market_full <- lm(inflacao12m ~ score_orig + l_inflacao12m, data = baseline)
set.seed(seed + 3L)
ms_model <- msmFit(
  market_full,
  k = 2L,
  p = 0L,
  sw = c(TRUE, TRUE, TRUE, TRUE),
  control = list(parallelization = FALSE, maxiter = 500L)
)
baseline[, markov_state := max.col(ms_model@Fit@smoProb, ties.method = "first")]

regime_sets <- list(
  "Pre-Campos Neto" = baseline[date_daily < as.Date("2019-03-20"), reuniao],
  "Campos Neto onward" = baseline[date_daily >= as.Date("2019-03-20"), reuniao],
  "Pre-2021" = baseline[date_daily < as.Date("2021-01-01"), reuniao],
  "2021 onward" = baseline[date_daily >= as.Date("2021-01-01"), reuniao],
  "Below-median realized inflation" = baseline[high_inflation == FALSE, reuniao],
  "Above-median realized inflation" = baseline[high_inflation == TRUE, reuniao],
  "Markov state 1" = baseline[markov_state == 1L, reuniao],
  "Markov state 2" = baseline[markov_state == 2L, reuniao]
)
d7_regimes <- rbindlist(Map(fit_regime, names(regime_sets), regime_sets), fill = TRUE)

d7_rolling <- rbindlist(lapply(24:nrow(baseline), function(end_index) {
  sample <- baseline[(end_index - 23L):end_index]
  fit <- fit_regime(
    sprintf("Rolling 24 ending %s", sample$date_daily[[nrow(sample)]]),
    sample$reuniao
  )
  fit[, window_end := sample$date_daily[[nrow(sample)]]]
  fit
}))

threshold_market <- lm(
  inflacao12m ~ l_inflacao12m + score_orig * high_inflation,
  data = baseline
)
threshold_hac <- NeweyWest(threshold_market, lag = 4L, prewhite = FALSE, adjust = TRUE)
threshold_matrix <- unclass(coeftest(threshold_market, vcov. = threshold_hac))
threshold_ct <- data.table(
  term = rownames(threshold_matrix),
  as.data.frame(threshold_matrix, check.names = FALSE)
)
setnames(
  threshold_ct,
  c("Estimate", "Std. Error", "t value", "Pr(>|t|)"),
  c("estimate", "std_error", "statistic", "p_value")
)

break_fit <- breakpoints(
  inflacao12m ~ score_orig + l_inflacao12m,
  data = baseline,
  breaks = 2L
)
break_indices <- breakdates(break_fit)
d7_breaks <- data.table(
  break_fraction = as.numeric(break_indices),
  break_index = as.integer(round(as.numeric(break_indices) * nrow(baseline))),
  break_date = baseline$date_daily[pmax(1L, pmin(nrow(baseline), as.integer(round(
    as.numeric(break_indices) * nrow(baseline)
  ))))]
)
d7_coverage <- data.table(
  requested_split = "Bonomo et al. (2024) unanchored 2011-2016 versus reanchored",
  sample_start = min(baseline$date_daily),
  sample_end = max(baseline$date_daily),
  identified_in_current_sample = FALSE,
  reason = paste(
    "The estimation sample begins in November 2016, after the reanchoring",
    "window identified in Bonomo et al.; no within-sample Bonomo regime contrast exists."
  )
)
cat("STAGE 4/6: D7 regime and stability estimates complete\n")
flush.console()

# D23: exact wild-cluster IV inference and local-to-unity sensitivity.
set.seed(seed + 4L)
wild_boot_reps <- as.integer(Sys.getenv("PROMPT_IV_WILD_BOOT_REPS", "9999"))
wild_data <- as.data.frame(main_iv$transformed)
wild_model <- ivreg(
  forecast_revision_within ~ tone_pp_within | hawk_within,
  data = wild_data
)
wild <- cluster.wild.ivreg(
  mod = wild_model,
  dat = wild_data,
  cluster = ~meeting_number,
  ci.level = 0.95,
  impose.null = FALSE,
  boot.reps = wild_boot_reps,
  report = FALSE,
  prog.bar = FALSE,
  seed = seed + 4L
)
d23_wild <- data.table(
  method = "clusterSEs wild cluster bootstrap-t for ivreg",
  estimate = unname(coef(wild_model)[["tone_pp_within"]]),
  p_value = unname(wild$p.values["tone_pp_within", 1]),
  ci_low = unname(wild$ci["tone_pp_within", "CI lower"]),
  ci_high = unname(wild$ci["tone_pp_within", "CI higher"]),
  bootstrap_replications = wild_boot_reps,
  clusters = uniqueN(main_iv$transformed$meeting_number)
)

focus_model <- lm(inflacao12m ~ score_orig + l_inflacao12m, data = baseline)
focus_hac <- NeweyWest(focus_model, lag = 4L, prewhite = FALSE, adjust = TRUE)
focus_rho <- coef(focus_model)[["l_inflacao12m"]]
focus_rho_se <- sqrt(focus_hac["l_inflacao12m", "l_inflacao12m"])
rho_grid <- c(0.80, 0.85, 0.90, 0.925, 0.95, 0.975, 0.99, 0.995)
d23_local_to_unity <- data.table(
  assumed_rho = rho_grid,
  local_parameter_c = nrow(baseline) * (1 - rho_grid),
  long_run_multiplier = 1 / (1 - rho_grid),
  transformed_focus_tone = coef(focus_model)[["score_orig"]] / (1 - rho_grid)
)
adf <- ur.df(
  na.omit(baseline$inflacao12m),
  type = "drift",
  lags = 1L,
  selectlags = "Fixed"
)
d23_persistence <- data.table(
  rho = focus_rho,
  rho_se_hac = focus_rho_se,
  rho_ci_low = focus_rho - 1.96 * focus_rho_se,
  rho_ci_high = focus_rho + 1.96 * focus_rho_se,
  adf_tau2 = adf@teststat[1, "tau2"],
  adf_5pct_critical = adf@cval["tau2", "5pct"],
  reject_unit_root_5pct = adf@teststat[1, "tau2"] < adf@cval["tau2", "5pct"]
)
cat("STAGE 5/6: D23 wild-cluster and persistence estimates complete\n")
flush.console()

# D24: Shapley decomposition of tone-score variance.
tone_components <- as.data.table(dbGetQuery(
  con,
  paste(
    "SELECT meeting_number, iteration, role, hawk_count, dove_count, word_count,",
    "100.0 * score AS tone_pp",
    "FROM tone_scores WHERE run_id = 'v10_referee_d5_25draws_20260620'",
    "AND role IN ('original','hawk_text','dove_text')"
  )
))
tone_components[, numerator := 100 * (hawk_count - dove_count)]

variance_decomposition <- function(data) {
  mean_n <- mean(data$numerator)
  mean_l <- mean(data$word_count)
  v_actual <- var(data$numerator / data$word_count)
  v_n_first <- var(data$numerator / mean_l)
  v_l_first <- var(mean_n / data$word_count)
  contribution_n <- 0.5 * (v_n_first + v_actual - v_l_first)
  contribution_l <- 0.5 * (v_l_first + v_actual - v_n_first)
  data.table(
    observations = nrow(data),
    actual_variance = v_actual,
    numerator_contribution = contribution_n,
    denominator_contribution = contribution_l,
    numerator_share = contribution_n / v_actual,
    denominator_share = contribution_l / v_actual,
    covariance = cov(data$numerator, data$word_count),
    correlation = cor(data$numerator, data$word_count)
  )
}
d24_variance <- tone_components[, variance_decomposition(.SD), by = role]
cat("STAGE 6/6: D24 tone variance decomposition complete\n")
flush.console()

outputs <- list(
  d5_forecaster_summary = write_result(d5_forecaster_summary, "referee_d5_cross_draw_forecasters"),
  d5_composite_summary = write_result(d5_composite_summary, "referee_d5_cross_draw_composite"),
  d5_retry = write_result(retry, "referee_d5_retry_by_stance"),
  d5_placebo_draws = write_result(placebo, "referee_d5_scrambled_outcome_placebo_draws"),
  d5_placebo_summary = write_result(placebo_summary, "referee_d5_scrambled_outcome_placebo_summary"),
  d6_calibration = write_result(d6_table, "referee_d6_constrained_calibration"),
  d6_wald = write_result(d6_wald, "referee_d6_wald_unanchored_equals_one"),
  d7_regimes = write_result(d7_regimes, "referee_d7_regime_estimates"),
  d7_rolling = write_result(d7_rolling, "referee_d7_rolling_24_meetings"),
  d7_threshold = write_result(threshold_ct, "referee_d7_threshold_market"),
  d7_breaks = write_result(d7_breaks, "referee_d7_structural_breaks"),
  d7_coverage = write_result(d7_coverage, "referee_d7_bonomo_coverage"),
  d23_wild = write_result(d23_wild, "referee_d23_wild_cluster_iv"),
  d23_local_to_unity = write_result(d23_local_to_unity, "referee_d23_local_to_unity_grid"),
  d23_persistence = write_result(d23_persistence, "referee_d23_persistence"),
  d24_variance = write_result(d24_variance, "referee_d24_tone_variance_decomposition")
)

summary_payload <- list(
  generated_at = format(Sys.time(), tz = "UTC", usetz = TRUE),
  implementation = list(
    econometrics_language = "R",
    packages = c(
      "ivreg", "sandwich", "lmtest", "car", "quadprog",
      "boot", "glmnet", "clusterSEs", "MSwM", "strucchange", "urca"
    )
  ),
  d5 = list(
    main_iv = main_iv$row,
    placebo = placebo_summary,
    retry_by_stance = retry
  ),
  d6 = list(
    calibration = d6_table,
    wald_unanchored_equals_one = d6_wald
  ),
  d7 = list(
    regimes = d7_regimes,
    bonomo_coverage = d7_coverage,
    threshold = threshold_ct
  ),
  d23 = list(
    wild_cluster = d23_wild,
    persistence = d23_persistence
  ),
  d24 = list(variance_decomposition = d24_variance),
  outputs = outputs
)
summary_path <- file.path(out_dir, paste0("referee_identification_calibration_", stamp, ".json"))
write_json(
  release_metadata(summary_payload, root, stamp),
  summary_path,
  auto_unbox = TRUE,
  pretty = TRUE,
  digits = 16
)

cat(sprintf("Completed identification/calibration module. Summary: %s\n", summary_path))
