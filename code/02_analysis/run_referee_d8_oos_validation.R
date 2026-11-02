# Read the explicit run authority before opening any artifact.
source_root <- normalizePath(getwd(), winslash = "/", mustWork = TRUE)
source(file.path(source_root, "code", "read_run_spec.R"), local = TRUE)

# The neutral dataset name must still identify this exact model and specification.
calibration_audit_path <- run_path("outputs/work/calibration_audit.json")
if (!file.exists(spec$paths$calibration_data) || !file.exists(calibration_audit_path)) {
  stop("Selected Terra calibration is absent. Production collection/calibration has not run")
}
calibration_audit <- jsonlite::fromJSON(calibration_audit_path, simplifyVector = TRUE)
if (!identical(calibration_audit$fingerprint, spec$fingerprint) ||
    !identical(calibration_audit$spec_sha256,spec_sha256) ||
    !identical(calibration_audit$original_run,spec$run_ids$original_priors) ||
    !identical(calibration_audit$main_text_run,spec$run_ids$main_text) ||
    !identical(calibration_audit$model, spec$model) ||
    !identical(calibration_audit$reasoning_effort, spec$reasoning_effort) ||
    !identical(as.integer(calibration_audit$meetings), as.integer(spec$sample$meeting_numbers)) ||
    !identical(calibration_audit$calibration_sha256,
      digest::digest(spec$paths$calibration_data, algo = "sha256", file = TRUE, serialize = FALSE))) {
  stop("Calibration provenance, membership or bytes do not match this run")
}

suppressPackageStartupMessages({
  library(data.table)
  library(haven)
  library(jsonlite)
  library(lmtest)
  library(sandwich)
})

data.table::setDTthreads(1L)

source(file.path(source_root, "code", "02_analysis", "reproducibility_helpers.R"), local = TRUE)

dataset_path <- spec$paths$calibration_data
table_dir <- run_path("outputs/tables")
result_table_dir <- run_path("results/tables")
dir.create(table_dir, recursive = TRUE, showWarnings = FALSE)
dir.create(result_table_dir, recursive = TRUE, showWarnings = FALSE)

stamp <- result_stamp
min_train <- 24L
hac_lags <- 4L

mixture_columns <- c(
  "forecast_anchored",
  "forecast_unanchored",
  "wc_orig",
  "ibcbr_agro",
  "ibcbr_service",
  "ibcbr_industry"
)

data <- as.data.table(read_dta(dataset_path))
data[, date_daily := as.Date(date_daily)]
setorder(data, date_daily, reuniao)

if (nrow(data) != 79L || uniqueN(data$reuniao) != 79L) {
  stop("D8 requires exactly one observation for each of 79 Copom meetings")
}
if (uniqueN(data$date_daily) != 79L) {
  stop("D8 requires a unique date for each meeting")
}
if (!identical(range(as.integer(data$reuniao)), c(203L, 281L))) {
  stop("D8 expected Copom meetings 203 through 281")
}
if (!identical(
  as.character(range(data$date_daily)),
  c("2016-11-30", "2026-09-16")
)) {
  stop("D8 recovered an unexpected meeting-date range")
}

model_columns <- c("inflacao12m", mixture_columns)
if (sum(complete.cases(data[, ..model_columns])) != 79L) {
  stop("D8 mixture variables must be complete for all 79 meetings")
}

chronological_lag <- shift(data$inflacao12m)
if (!identical(as.numeric(data$l_inflacao12m), as.numeric(chronological_lag))) {
  stop("Stored Focus lag does not equal the chronological lag")
}
data[, lag_focus := chronological_lag]
if (sum(complete.cases(data[, .(inflacao12m, lag_focus)])) != 78L) {
  stop("D8 expected 78 usable Focus AR(1) pairs")
}
if (min_train <= length(mixture_columns) + 1L || min_train >= nrow(data)) {
  stop("D8 initial window is not compatible with the calibration dimension")
}

fit_lm_predict <- function(train, test_row, y, x_columns) {
  columns <- c(y, x_columns)
  working <- as.data.frame(train[, ..columns])
  working <- working[complete.cases(working), , drop = FALSE]
  if (nrow(working) <= length(x_columns) + 1L) {
    stop("Insufficient complete observations for an expanding-window model")
  }
  formula <- as.formula(paste(y, "~", paste(x_columns, collapse = " + ")))
  model <- lm(formula, data = working, na.action = na.fail)
  if (model$rank != length(coef(model)) || anyNA(coef(model))) {
    stop("Rank-deficient D8 forecasting model")
  }
  prediction <- as.numeric(predict(model, newdata = as.data.frame(test_row)))
  if (length(prediction) != 1L || !is.finite(prediction)) {
    stop("D8 produced a non-finite forecast")
  }
  prediction
}

loo <- rbindlist(lapply(seq_len(nrow(data)), function(index) {
  train <- data[-index]
  test <- data[index]
  prediction <- fit_lm_predict(
    train,
    test,
    "inflacao12m",
    mixture_columns
  )
  data.table(
    reuniao = test$reuniao,
    date_daily = test$date_daily,
    actual = test$inflacao12m,
    pred_composite_loo = prediction,
    error_composite_loo = test$inflacao12m - prediction
  )
}))

if (nrow(loo) != 79L || any(!is.finite(loo$error_composite_loo))) {
  stop("D8 leave-one-meeting-out predictions are incomplete")
}
loo_sse <- sum(loo$error_composite_loo^2)
loo_tss <- sum((loo$actual - mean(loo$actual))^2)
loo_summary <- data.table(
  n = nrow(loo),
  rmse = sqrt(mean(loo$error_composite_loo^2)),
  mae = mean(abs(loo$error_composite_loo)),
  r2_cv = 1 - loo_sse / loo_tss
)

expanding_rows <- vector("list", nrow(data) - min_train)
for (index in seq.int(min_train + 1L, nrow(data))) {
  train <- data[seq_len(index - 1L)]
  test <- data[index]
  if (max(train$date_daily) >= test$date_daily) {
    stop("D8 expanding window contains a non-prior training observation")
  }

  pred_composite <- fit_lm_predict(
    train,
    test,
    "inflacao12m",
    mixture_columns
  )
  pred_ar1 <- fit_lm_predict(
    train,
    test,
    "inflacao12m",
    "lag_focus"
  )

  anchored <- test$forecast_anchored
  unanchored <- test$forecast_unanchored
  equal_weight <- 0.5 * anchored + 0.5 * unanchored

  history <- train[, .(
    inflacao12m,
      forecast_anchored,
      forecast_unanchored
  )]
  history <- history[complete.cases(history)]
  anchored_mse <- mean(
      (history$inflacao12m - history$forecast_anchored)^2
  )
  unanchored_mse <- mean(
      (history$inflacao12m - history$forecast_unanchored)^2
  )
  anchored_inverse_mse <- 1 / max(anchored_mse, 1e-12)
  unanchored_inverse_mse <- 1 / max(unanchored_mse, 1e-12)
  anchored_weight <- anchored_inverse_mse /
    (anchored_inverse_mse + unanchored_inverse_mse)
  unanchored_weight <- 1 - anchored_weight

  output_index <- index - min_train
  expanding_rows[[output_index]] <- data.table(
    reuniao = test$reuniao,
    date_daily = test$date_daily,
    actual = test$inflacao12m,
    pred_composite = pred_composite,
    pred_focus_ar1 = pred_ar1,
    pred_anchored_agent = anchored,
    pred_unanchored_agent = unanchored,
    pred_equal_weight = equal_weight,
    pred_bates_granger = anchored_weight * anchored +
      unanchored_weight * unanchored,
    pred_historical_mean = mean(train$inflacao12m),
    bates_granger_weight_anchored = anchored_weight,
    bates_granger_weight_unanchored = unanchored_weight,
    training_meetings = nrow(train),
    ar1_training_pairs = sum(complete.cases(
      train[, .(inflacao12m, lag_focus)]
    ))
  )
}
expanding <- rbindlist(expanding_rows)

if (nrow(expanding) != 55L ||
    expanding$reuniao[[1L]] != 227L ||
    expanding$reuniao[[55L]] != 281L ||
    as.character(expanding$date_daily[[1L]]) != "2019-12-11" ||
    as.character(expanding$date_daily[[55L]]) != "2026-09-16") {
  stop("D8 expanding-window forecast range is incomplete")
}

prediction_columns <- c(
  "pred_composite",
  "pred_focus_ar1",
  "pred_anchored_agent",
  "pred_unanchored_agent",
  "pred_equal_weight",
  "pred_bates_granger"
)
if (any(!is.finite(as.matrix(expanding[, ..prediction_columns])))) {
  stop("D8 expanding-window predictions contain non-finite values")
}

forecast_metrics <- function(predictions, prediction_column) {
  mask <- complete.cases(predictions[, c(
    "actual",
    prediction_column,
    "pred_historical_mean",
    "pred_focus_ar1"
  ), with = FALSE])
  working <- predictions[mask]
  error <- working$actual - working[[prediction_column]]
  sse <- sum(error^2)
  historical_sse <- sum(
    (working$actual - working$pred_historical_mean)^2
  )
  ar1_sse <- sum((working$actual - working$pred_focus_ar1)^2)
  data.table(
    model = sub("^pred_", "", prediction_column),
    n = length(error),
    rmse = sqrt(mean(error^2)),
    mae = mean(abs(error)),
    sse = sse,
    r2_oos_vs_historical_mean = 1 - sse / historical_sse,
    r2_oos_vs_focus_ar1 = 1 - sse / ar1_sse
  )
}
metrics <- rbindlist(lapply(
  prediction_columns,
  function(column) forecast_metrics(expanding, column)
))

mean_loss_test <- function(
    predictions,
    model_column,
    benchmark_column = "pred_focus_ar1") {
  mask <- complete.cases(predictions[, c(
    "actual",
    model_column,
    benchmark_column
  ), with = FALSE])
  working <- predictions[mask]
  difference <-
    (working$actual - working[[benchmark_column]])^2 -
    (working$actual - working[[model_column]])^2
  model <- lm(difference ~ 1)
  covariance <- NeweyWest(
    model,
    lag = hac_lags,
    prewhite = FALSE,
    adjust = TRUE
  )
  coefficient_table <- coeftest(model, vcov. = covariance)
  data.table(
    model = sub("^pred_", "", model_column),
    benchmark = sub("^pred_", "", benchmark_column),
    n = nobs(model),
    mean_loss_difference = unname(coef(model)[["(Intercept)"]]),
    se_hac = unname(coefficient_table["(Intercept)", "Std. Error"]),
    t_hac = unname(coefficient_table["(Intercept)", "t value"]),
    p_hac = unname(coefficient_table["(Intercept)", "Pr(>|t|)"]),
    positive_mean_means_model_improves_on_benchmark = TRUE
  )
}
loss_tests <- rbindlist(lapply(
  c("pred_composite", "pred_equal_weight", "pred_bates_granger"),
  function(column) mean_loss_test(expanding, column)
))

fmt <- function(value, digits = 4L) {
  formatC(value, format = "f", digits = digits)
}
significance_stars <- function(p_value) {
  if (p_value < 0.01) return("\\sym{***}")
  if (p_value < 0.05) return("\\sym{**}")
  if (p_value < 0.10) return("\\sym{*}")
  ""
}

write_loo_table <- function(path) {
  row <- loo_summary[1]
  lines <- c(
    "\\begin{table}[htbp]",
    "\\color{revisionred}",
    "\\centering",
    "\\begin{threeparttable}",
    "\\caption{Leave-one-meeting-out validation of the Focus calibration}",
    "\\label{tab:llm-loo}",
    "\\begin{footnotesize}",
    "\\begin{tabular}{p{0.46\\textwidth}c}",
    "\\toprule",
    "Validation statistic & Estimate \\\\",
    "\\midrule",
    paste0("Cross-validated $R^2$ & ", fmt(row$r2_cv), " \\\\"),
    paste0("RMSE (percentage points) & ", fmt(row$rmse), " \\\\"),
    paste0("MAE (percentage points) & ", fmt(row$mae), " \\\\"),
    paste0("Held-out predictions & ", row$n, " \\\\"),
    "\\bottomrule",
    "\\end{tabular}",
    "\\begin{tablenotes}[flushleft]",
    "\\scriptsize",
    paste(
      "\\item \\textit{Notes:} The outcome is the median twelve-month-ahead IPCA",
      "expectation from the Focus Survey. The calibration equation is re-estimated",
      "on the other 70 meetings before predicting each omitted meeting. $R^2$ uses",
      "deviations from the full-sample Focus mean. RMSE and MAE are in percentage points."
    ),
    "\\end{tablenotes}",
    "\\end{footnotesize}",
    "\\end{threeparttable}",
    "\\end{table}"
  )
  writeLines(lines, path, useBytes = TRUE)
}

write_expanding_table <- function(path) {
  model_order <- c(
    "composite",
    "focus_ar1",
    "anchored_agent",
    "unanchored_agent",
    "equal_weight",
    "bates_granger"
  )
  rows <- lapply(model_order, function(model_name) metrics[model == model_name][1])
  value_row <- function(column, digits = 4L) {
    paste(vapply(rows, function(row) fmt(row[[column]], digits), ""), collapse = " & ")
  }
  lines <- c(
    "\\begin{table}[htbp]",
    "\\color{revisionred}",
    "\\centering",
    "\\begin{threeparttable}",
    "\\caption{Expanding-window forecast accuracy for Focus expectations}",
    "\\label{tab:llm-expanding}",
    "\\begin{scriptsize}",
    "\\setlength{\\tabcolsep}{3.0pt}",
      "\\begin{tabular*}{\\textwidth}{@{\\extracolsep{\\fill}}lcccccc@{}}",
    "\\toprule",
    " & Composite & Focus AR(1) & Anchored & Unanchored & Equal weight & Bates--Granger \\\\",
    "\\midrule",
    paste0("RMSE (pp) & ", value_row("rmse"), " \\\\"),
    paste0("MAE (pp) & ", value_row("mae"), " \\\\"),
    paste0(
      "$R^2_{\\mathrm{OOS}}$ vs. historical mean & ",
      value_row("r2_oos_vs_historical_mean"),
      " \\\\"
    ),
    paste0(
      "$R^2_{\\mathrm{OOS}}$ vs. Focus AR(1) & ",
      value_row("r2_oos_vs_focus_ar1"),
      " \\\\"
    ),
    paste0(
      "Forecasts & ",
      paste(vapply(rows, function(row) as.character(row$n), ""), collapse = " & "),
      " \\\\"
    ),
    "\\bottomrule",
      "\\end{tabular*}",
    "\\begin{tablenotes}[flushleft]",
    "\\scriptsize",
    paste(
      "\\item \\textit{Notes:} The first forecast follows 24 observed meetings;",
      "the Focus AR(1) is initially estimated on 23 available lagged pairs.",
      "All parameters are re-estimated recursively using only prior meetings.",
      "Equal weight averages the two agent forecasts. Bates--Granger is a restricted",
      "convex combination with inverse historical-MSE weights estimated recursively.",
      "RMSE and MAE are in percentage points."
    ),
    "\\end{tablenotes}",
    "\\end{scriptsize}",
    "\\end{threeparttable}",
    "\\end{table}"
  )
  writeLines(lines, path, useBytes = TRUE)
}

write_loss_test_table <- function(path) {
  model_order <- c("composite", "equal_weight", "bates_granger")
  model_labels <- c(
    composite = "LLM composite",
    equal_weight = "Equal weight",
    bates_granger = "Bates--Granger"
  )
  rows <- lapply(model_order, function(model_name) loss_tests[model == model_name][1])
  body <- vapply(seq_along(rows), function(index) {
    row <- rows[[index]]
    paste0(
      model_labels[[model_order[[index]]]], " & ",
      fmt(row$mean_loss_difference), significance_stars(row$p_hac), " & ",
      fmt(row$se_hac), " & ", fmt(row$t_hac, 3L), " \\\\"
    )
  }, "")
  lines <- c(
    "\\begin{table}[htbp]",
    "\\color{revisionred}",
    "\\centering",
    "\\begin{threeparttable}",
    "\\caption{HAC tests of mean squared-error differentials against Focus AR(1)}",
    "\\label{tab:llm-gw}",
    "\\begin{footnotesize}",
    "\\begin{tabular}{lddd}",
    "\\toprule",
    "Competing model & \\mc{Mean loss differential} & \\mc{HAC SE} & \\mc{$t$ statistic} \\\\",
    "\\midrule",
    body,
    "\\bottomrule",
    "\\end{tabular}",
    "\\begin{tablenotes}[flushleft]",
    "\\scriptsize",
    paste(
      "\\item \\textit{Notes:} The differential is the squared error of Focus AR(1)",
      "minus the squared error of the competing model, so a positive value favors",
      "the competing model. Each row is an intercept-only regression of the loss",
      "differential with Newey--West HAC standard errors using four lags.",
      "* $p<0.10$, ** $p<0.05$, *** $p<0.01$."
    ),
    "\\end{tablenotes}",
    "\\end{footnotesize}",
    "\\end{threeparttable}",
    "\\end{table}"
  )
  writeLines(lines, path, useBytes = TRUE)
}

output_paths <- list(
  loo_predictions = file.path(
    table_dir,
    paste0("referee_d8_loo_predictions_", stamp, ".csv")
  ),
  loo_summary = file.path(
    table_dir,
    paste0("referee_d8_loo_summary_", stamp, ".csv")
  ),
  expanding_predictions = file.path(
    table_dir,
    paste0("referee_d8_expanding_predictions_", stamp, ".csv")
  ),
  forecast_metrics = file.path(
    table_dir,
    paste0("referee_d8_forecast_metrics_", stamp, ".csv")
  ),
  mean_loss_tests = file.path(
    table_dir,
    paste0("referee_d8_mean_loss_tests_", stamp, ".csv")
  ),
  report = file.path(
    table_dir,
    paste0("referee_d8_oos_validation_", stamp, ".json")
  ),
  loo_table = file.path(result_table_dir, "rr_llm_loo.tex"),
  expanding_table = file.path(result_table_dir, "rr_llm_expanding.tex"),
  mean_loss_table = file.path(result_table_dir, "rr_llm_gw.tex")
)

fwrite(loo, output_paths$loo_predictions)
fwrite(loo_summary, output_paths$loo_summary)
fwrite(expanding, output_paths$expanding_predictions)
fwrite(metrics, output_paths$forecast_metrics)
fwrite(loss_tests, output_paths$mean_loss_tests)
write_loo_table(output_paths$loo_table)
write_expanding_table(output_paths$expanding_table)
write_loss_test_table(output_paths$mean_loss_table)

report <- list(
  status = "complete",
  implementation = paste(
    "R stats::lm/predict for leave-one-meeting-out and expanding-window",
    "forecasts; sandwich::NeweyWest and lmtest::coeftest for unconditional",
    "HAC tests of mean squared-error differentials."
  ),
  packages = list(
    haven = as.character(packageVersion("haven")),
    sandwich = as.character(packageVersion("sandwich")),
    lmtest = as.character(packageVersion("lmtest")),
    data_table = as.character(packageVersion("data.table")),
    jsonlite = as.character(packageVersion("jsonlite"))
  ),
  dataset = dataset_path,
  settings = list(
    mixture_columns = mixture_columns,
    initial_training_meetings = min_train,
    initial_ar1_lagged_pairs = expanding$ar1_training_pairs[[1L]],
    hac_lags = hac_lags
  ),
  loo = loo_summary,
  expanding_window = list(
    forecasts = nrow(expanding),
    first_forecast_date = min(expanding$date_daily),
    last_forecast_date = max(expanding$date_daily),
    metrics = metrics
  ),
  mean_loss_differentials_against_focus_ar1 = loss_tests,
  outputs = output_paths
)
write_json(
  release_metadata(report, root, stamp),
  output_paths$report,
  auto_unbox = TRUE,
  pretty = TRUE,
  digits = 16,
  na = "null"
)

cat(sprintf(
  "Completed D8 OOS validation: LOO R2 %.6f; %d expanding forecasts\n",
  loo_summary$r2_cv,
  nrow(expanding)
))
