# Verify the selected Terra release without accepting numerical references.
source_root <- normalizePath(getwd(), winslash = "/", mustWork = TRUE)
source(file.path(source_root, "code", "read_run_spec.R"))
suppressPackageStartupMessages({library(data.table); library(haven)})
result_dir <- run_path("results")
intermediate <- run_path("outputs/tables")
dir.create(run_path("results/qa"), recursive = TRUE, showWarnings = FALSE)
checks <- data.table(check_id = character(), status = character(), detail = character())
required <- paste0("knowledge_cutoff_", c("training", "coefficients", "predictions", "metrics",
  "framing_draws", "framing_meetings", "framing_summary"), "_", result_stamp, ".csv")
missing <- required[!file.exists(file.path(intermediate, required))]
if (length(missing)) {
  checks <- data.table(check_id = paste0("input:", missing), status = "NOT RUN", detail = "Production Terra diagnostic is absent")
  fwrite(checks, run_path("results/verification_report.csv"))
  stop("NOT RUN: full Terra diagnostic inputs are absent")
}
training <- fread(file.path(intermediate, required[[1]]))
coefficients <- fread(file.path(intermediate, required[[2]]))
predictions <- fread(file.path(intermediate, required[[3]]))
metrics <- fread(file.path(intermediate, required[[4]]))
draws <- fread(file.path(intermediate, required[[5]]))
meetings <- fread(file.path(intermediate, required[[6]]))
summary <- fread(file.path(intermediate, required[[7]]))
base <- as.data.table(haven::read_dta(spec$paths$calibration_data))
setorder(base, reuniao)
if (!identical(as.integer(base$reuniao), 203:281) || anyDuplicated(base$reuniao)) stop("Invalid calibration membership")
canonical <- fread(spec$sample$dates_csv)[meeting_number %in% 203:281]
setorder(canonical, meeting_number)
if (!identical(as.integer(canonical$meeting_number), 203:281) ||
    anyDuplicated(canonical$meeting_date) || anyNA(canonical$meeting_date) ||
    !identical(as.character(as.Date(base$date_daily)), as.character(as.Date(canonical$meeting_date)))) {
  stop("Calibration dates differ from the canonical map")
}
training_key <- if ("meeting_number" %in% names(training)) training$meeting_number else training$reuniao
if (!identical(sort(as.integer(training_key)), 203:271)) stop("The training set is not the frozen69")
training <- training[order(training_key)]
training_columns <- c("inflacao12m","forecast_anchored","forecast_unanchored","wc_orig",
                      "ibcbr_agro","ibcbr_service","ibcbr_industry")
if (!all(training_columns %in% names(training)) ||
    !identical(as.character(as.Date(training$date_daily)), as.character(as.Date(base[reuniao <= 271L,date_daily])))) {
  stop("Saved training dates or fields differ")
}
for (column in training_columns) {
  if (any(!is.finite(training[[column]])) ||
      max(abs(training[[column]]-base[reuniao <= 271L][[column]])) > 1e-12) stop("Saved training input differs: ", column)
}
formula <- inflacao12m ~ forecast_anchored + forecast_unanchored + wc_orig + ibcbr_agro + ibcbr_service + ibcbr_industry
fit <- stats::lm(formula, data = base[reuniao <= 271L], na.action = na.fail)
if (fit$rank != 7L || nrow(coefficients) != 7L || anyDuplicated(coefficients$term) ||
    !setequal(coefficients$term, names(stats::coef(fit))) ||
    max(abs(coefficients$estimate - stats::coef(fit)[coefficients$term])) > 1e-10) stop("Frozen coefficients differ from training-only OLS")
expected_predictions <- CJ(meeting_number = 272:281,
  series = c("frozen_calibrated_composite", "raw_anchored", "raw_unanchored", "focus_persistence"))
if (nrow(predictions) != 40L || anyDuplicated(predictions[, .(meeting_number,series)]) ||
    nrow(fsetdiff(expected_predictions, predictions[, .(meeting_number,series)]))) stop("Incomplete evaluation membership")
expected_dates <- as.Date(canonical$meeting_date[match(predictions$meeting_number, canonical$meeting_number)])
if (!identical(as.character(as.Date(predictions$date_daily)), as.character(expected_dates)) ||
    any(predictions$window != fifelse(predictions$meeting_number %in% 272:276, "pre", "post")) ||
    any(predictions$after_knowledge_cutoff != (expected_dates > as.Date(spec$cutoff)))) {
  stop("Evaluation dates, windows or cutoff flags differ from the fixed design")
}
base[, persistence := shift(inflacao12m)]
for (series_name in expected_predictions[, unique(series)]) {
  actual <- predictions[series == series_name][order(meeting_number)]
  expected <- switch(series_name,
    frozen_calibrated_composite = as.numeric(stats::predict(fit, newdata = base[reuniao >= 272L])),
    raw_anchored = base[reuniao >= 272L, forecast_anchored],
    raw_unanchored = base[reuniao >= 272L, forecast_unanchored],
    focus_persistence = base[reuniao >= 272L, persistence])
  if (any(!is.finite(expected)) || max(abs(actual$prediction - expected)) > 1e-10 ||
      max(abs(actual$focus - base[reuniao >= 272L, inflacao12m])) > 1e-12 ||
      max(abs(actual$signed_error - (actual$prediction - actual$focus))) > 1e-12 ||
      max(abs(actual$squared_error - actual$signed_error^2)) > 1e-12 ||
      max(abs(actual$absolute_error - abs(actual$signed_error))) > 1e-12) stop("Prediction or error identity failed: ", series_name)
}
if (nrow(metrics) != 8L || anyDuplicated(metrics[, .(window,series)]) || any(metrics$n != 5L)) stop("Invalid metric keys")
for (index in seq_len(nrow(metrics))) {
  row <- metrics[index]
  errors <- predictions[window == row$window & series == row$series]
  if (nrow(errors) != 5L || abs(row$rmse - sqrt(mean(errors$squared_error))) > 1e-12 ||
      abs(row$mae - mean(errors$absolute_error)) > 1e-12 ||
      abs(row$mean_signed_error - mean(errors$signed_error)) > 1e-12) stop("Metric formula mismatch")
  benchmark_rmse <- metrics[window == row$window & series == "focus_persistence", rmse]
  if (row$series != "frozen_calibrated_composite") {
    if (!is.na(row$benchmark_rmse_ratio) || row$ratio_status != "not_applicable") stop("Ratio applies only to the composite")
  } else if (benchmark_rmse == 0) {
    if (!is.na(row$benchmark_rmse_ratio) || row$ratio_status != "undefined_zero_benchmark_rmse") {
      stop("Zero persistence RMSE must produce an explicitly undefined ratio")
    }
  } else if (!is.finite(row$benchmark_rmse_ratio) || row$ratio_status != "defined" ||
             abs(row$benchmark_rmse_ratio - row$rmse / benchmark_rmse) > 1e-12) stop("Composite/persistence RMSE ratio mismatch")
  before <- metrics[window == "pre" & series == row$series]
  after <- metrics[window == "post" & series == row$series]
  if (nrow(before) != 1L || nrow(after) != 1L ||
      abs(row$post_minus_pre_rmse - (after$rmse-before$rmse)) > 1e-12 ||
      abs(row$post_minus_pre_mae - (after$mae-before$mae)) > 1e-12 ||
      abs(row$post_minus_pre_mean_signed_error - (after$mean_signed_error-before$mean_signed_error)) > 1e-12) {
    stop("Descriptive post-minus-pre metric changes differ")
  }
}
expected_draws <- CJ(meeting_number = 272:281, corpus_iteration = seq_len(spec$draws$dose),
  contrast = c("dove_high", "dove_low", "hawk_low", "hawk_high"), forecaster_type = c(spec$profiles,"composite"))
keys <- names(expected_draws)
if (nrow(draws) != nrow(expected_draws) || anyDuplicated(draws[, ..keys]) ||
    nrow(fsetdiff(expected_draws, draws[, ..keys]))) stop("Incomplete framing counterparts")
draw_dates <- as.Date(canonical$meeting_date[match(draws$meeting_number,canonical$meeting_number)])
if (!identical(as.character(as.Date(draws$date_daily)), as.character(draw_dates)) ||
    any(draws$window != fifelse(draws$meeting_number %in% 272:276,"pre","post"))) {
  stop("Raw framing audit windows or dates differ from the design")
}
raw <- draws[forecaster_type != "composite"]
if (anyNA(raw[, .(source_run_id,source_text_hash,corpus_hash,neutral_hash)]) ||
    any(raw$source_run_id != spec$run_ids$dose_text) || any(raw$source_text_hash != raw$corpus_hash) ||
    max(abs(raw$fixed_prior - raw$neutral_prior)) > 1e-12 ||
    max(abs(raw$fixed_prior - raw$original_prior)) > 1e-12 ||
    max(abs(raw$contrast_pp - (raw$arm_forecast - raw$neutral_forecast))) > 1e-12) stop("Framing source/prior/hash mismatch")
wide <- dcast(raw, meeting_number+corpus_iteration+contrast ~ forecaster_type, value.var="contrast_pp")
wide[, expected_composite := stats::coef(fit)[["forecast_anchored"]]*anchored + stats::coef(fit)[["forecast_unanchored"]]*unanchored]
composite <- merge(wide,draws[forecaster_type=="composite"],by=c("meeting_number","corpus_iteration","contrast"),all=TRUE)
if (nrow(composite)!=10L*spec$draws$dose*4L || anyNA(composite$expected_composite) ||
    max(abs(composite$expected_composite-composite$contrast_pp))>1e-12) stop("Composite framing weights differ from the frozen slopes")
expected_means <- draws[, .(contrast_pp = mean(contrast_pp)), by = .(meeting_number,contrast,forecaster_type)]
expected_dates <- as.Date(canonical$meeting_date[match(meetings$meeting_number, canonical$meeting_number)])
if (anyNA(expected_dates) ||
    !identical(as.character(as.Date(meetings$date_daily)), as.character(expected_dates)) ||
    any(meetings$window != fifelse(meetings$meeting_number %in% 272:276, "pre", "post"))) {
  stop("Framing dates or five-meeting window membership changed")
}
matched <- merge(expected_means, meetings, by = c("meeting_number","contrast","forecaster_type"), suffixes = c("_expected","_saved"), all = TRUE)
if (nrow(matched) != 120L || anyNA(matched$contrast_pp_expected) || anyNA(matched$contrast_pp_saved) ||
    max(abs(matched$contrast_pp_expected - matched$contrast_pp_saved)) > 1e-12) stop("Draw-to-meeting weights differ")
expected_windows <- meetings[, .(mean_contrast_pp = mean(contrast_pp), n_meetings = .N), by = .(window,contrast,forecaster_type)]
matched <- merge(expected_windows, summary, by = c("window","contrast","forecaster_type"), suffixes = c("_expected","_saved"), all = TRUE)
if (nrow(matched) != 24L || any(matched$n_meetings_expected != 5L) ||
    max(abs(matched$mean_contrast_pp_expected - matched$mean_contrast_pp_saved)) > 1e-12) stop("Window weights differ")
audit <- jsonlite::fromJSON(file.path(intermediate, paste0("knowledge_cutoff_audit_", result_stamp, ".json")))
if (!identical(audit$model,spec$model) || !identical(audit$reasoning_effort,spec$reasoning_effort) ||
    !identical(audit$fingerprint,spec$fingerprint) || !identical(audit$units,"percentage_points") ||
    !identical(audit$error_sign,"prediction_minus_Focus") || audit$n_per_window != 5L ||
    !identical(audit$spec_sha256,spec_sha256) || !identical(audit$source_runs,spec$run_ids) ||
    !identical(as.integer(audit$training),203:271) || !identical(as.integer(audit$pre),272:276) ||
    !identical(as.integer(audit$post),277:281) ||
    !identical(audit$calibration_sha256,digest::digest(spec$paths$calibration_data,algo="sha256",file=TRUE,serialize=FALSE)) ||
    !grepl("^[0-9a-f]{64}$",audit$fit_fingerprint) || !grepl("^[0-9a-f]{64}$",audit$training_input_sha256) ||
    !identical(audit$frozen_coefficient_sha256,digest::digest(file.path(intermediate,required[[2]]),algo="sha256",file=TRUE,serialize=FALSE))) stop("Cutoff audit identity failed")
checks <- data.table(check_id = c("frozen69","predictions40","metrics8","framing_weights","audit_identity"), status = "PASS", detail = spec$fingerprint)

# Verify the data actually passed to all affected date plots, including raw tone.
plot_files <- c("baseline_figure_data.csv", "tone_figure_data.csv", "rolling_calibration_figure_data.csv",
                "knowledge_cutoff_agreement_figure_data.csv", "knowledge_cutoff_framing_figure_data.csv")
for (filename in plot_files) {
  plotted <- fread(run_path(file.path("outputs/tables", filename)))
  if ("reuniao" %in% names(plotted)) setnames(plotted, "reuniao", "meeting_number")
  if ("date" %in% names(plotted)) setnames(plotted, "date", "date_daily")
  required_columns <- c("meeting_number", "date_daily", "after_knowledge_cutoff", "model", "calibration_type")
  if (!all(required_columns %in% names(plotted)) || anyNA(plotted[, ..required_columns])) {
    stop("Incomplete plotting metadata: ", filename)
  }
  expected_dates <- as.Date(canonical$meeting_date[match(plotted$meeting_number, canonical$meeting_number)])
  if (anyNA(expected_dates) ||
      !identical(as.character(as.Date(plotted$date_daily)), as.character(expected_dates)) ||
      any(plotted$after_knowledge_cutoff != (expected_dates > as.Date(spec$cutoff))) ||
      any(plotted$model != spec$model) || any(!nzchar(plotted$calibration_type))) {
    stop("Canonical plotting dates or cutoff flags failed: ", filename)
  }
  expected_type <- switch(filename,
    baseline_figure_data.csv = "full_79_and_leave_one_meeting_out",
    tone_figure_data.csv = "lexicon_tone",
    rolling_calibration_figure_data.csv = "rolling_24_meetings",
    knowledge_cutoff_agreement_figure_data.csv = fifelse(plotted$series=="frozen_calibrated_composite", "frozen_69",
      fifelse(plotted$series=="focus_persistence", "previous_meeting_focus", "raw_original_path")),
    knowledge_cutoff_framing_figure_data.csv = fifelse(plotted$forecaster_type=="composite", "frozen_69_contrast", "raw_common_prior_contrast"))
  if (any(plotted$calibration_type != expected_type)) stop("Incorrect plotted fit label: ", filename)
}
checks <- rbind(checks, data.table(check_id = "canonical_plotting_data", status = "PASS", detail = paste(plot_files,collapse = ",")))

# Reference acceptance is an explicit later author/reviewer action.
manifest <- fread(run_path("data/metadata/exhibit_manifest.csv"))
for (index in seq_len(nrow(manifest))) {
  item <- manifest[index]
  artifact <- run_path(item$output_path)
  present <- file.exists(artifact) && file.info(artifact)$size > 0
  accepted <- isTRUE(spec$execution$reference_accepted)
  reference <- run_path(item$reference_path)
  matched <- accepted && file.exists(reference) && present
  if (matched && item$type=="table") matched <- identical(
    paste(readLines(artifact,warn=FALSE,encoding="UTF-8"),collapse="\n"),
    paste(readLines(reference,warn=FALSE,encoding="UTF-8"),collapse="\n"))
  checks <- rbind(checks, data.table(check_id = paste0("artifact:",item$exhibit_id),
    status = if (!present) "FAIL" else if (!accepted) "NOT RUN" else if (matched) "PASS" else "FAIL",
    detail = if (!accepted) "Candidate reference has not been accepted" else "Accepted reference present; normalized text equality for tables"))
}
baseline <- spec$paths$numerical_baseline
if (!isTRUE(spec$execution$reference_accepted) || !file.exists(baseline)) {
  checks <- rbind(checks, data.table(check_id="numerical_baseline",status="NOT RUN",detail="New numerical references are unaccepted"))
} else {
  numerical <- fread(baseline)
  required_columns <- c("check_id","artifact","key_column","key_value","value_column","expected","tolerance")
  if (!all(required_columns %in% names(numerical)) || !nrow(numerical)) stop("Invalid numerical baseline schema")
  for (index in seq_len(nrow(numerical))) {
    item <- numerical[index]
    current <- fread(run_path(item$artifact))
    if (!is.na(item$key_column) && nzchar(item$key_column)) current <- current[as.character(current[[item$key_column]]) == as.character(item$key_value)]
    observed <- current[[item$value_column]]
    passed <- length(observed) == 1L && is.finite(observed) && is.finite(item$expected) &&
      is.finite(item$tolerance) && item$tolerance >= 0 && abs(observed-item$expected) <= item$tolerance
    checks <- rbind(checks,data.table(check_id=item$check_id,status=if(passed) "PASS" else "FAIL",detail="Reviewed Terra numerical baseline"))
  }
}
fwrite(checks, run_path("results/verification_report.csv"))

# Candidate in-text macros always use the selected outputs, regardless of sign.
main <- fread(file.path(intermediate,paste0("common_prior_main_models_",result_stamp,".csv")))[sample=="Composite"]
dose <- fread(file.path(intermediate,paste0("common_prior_dose_arm_effects_",result_stamp,".csv")))[sample=="Composite"]
heldout <- fread(file.path(intermediate,paste0("common_prior_heldout_",result_stamp,".csv")))[grepl("^Held-out",specification)]
d8_loo <- fread(file.path(intermediate,paste0("referee_d8_loo_summary_",result_stamp,".csv")))
d8_metrics <- fread(file.path(intermediate,paste0("referee_d8_forecast_metrics_",result_stamp,".csv")))
d8_loss <- fread(file.path(intermediate,paste0("referee_d8_mean_loss_tests_",result_stamp,".csv")))
d8_composite <- d8_metrics[model=="composite"]
d8_ar1 <- d8_metrics[model=="focus_ar1"]
d8_composite_loss <- d8_loss[model=="composite"]
d8_bates_loss <- d8_loss[model=="bates_granger"]
if (any(c(nrow(main),nrow(heldout),nrow(d8_loo),nrow(d8_composite),nrow(d8_ar1),nrow(d8_composite_loss),nrow(d8_bates_loss))!=1L)) stop("Ambiguous numerical macro inputs")
intext <- data.table(
  result = c(
    "Main Prompt-IV", "Main standard error", "Reduced form", "First stage",
    "First-stage F", "Held-out Prompt-IV", "High-dove revision bp",
    "Low-dove revision bp", "Neutral revision bp", "Low-hawk revision bp",
    "High-hawk revision bp", "LOO R-squared", "LOO RMSE",
    "Expanding composite RMSE", "Expanding Focus AR(1) RMSE",
    "Expanding composite R-squared OOS", "Composite loss-difference p-value",
    "Bates-Granger loss-difference p-value"
  ),
  value = c(
    main$prompt_iv, main$prompt_iv_se, main$reduced_form, main$first_stage,
    main$first_stage_f, heldout$prompt_iv,
    100 * dose[arm == "dove_high"]$estimate,
    100 * dose[arm == "dove_low"]$estimate,
    100 * dose[arm == "neutral"]$estimate,
    100 * dose[arm == "hawk_low"]$estimate,
    100 * dose[arm == "hawk_high"]$estimate,
    d8_loo$r2_cv, d8_loo$rmse, d8_composite$rmse, d8_ar1$rmse,
    d8_composite$r2_oos_vs_historical_mean,
    d8_composite_loss$p_hac, d8_bates_loss$p_hac
  )
)
write.csv(intext, file.path(result_dir, "intext_results.csv"), row.names = FALSE)
tex <- c(
  sprintf("\\newcommand{\\MainPromptIV}{%.4f}", main$prompt_iv),
  sprintf("\\newcommand{\\MainPromptIVSE}{%.4f}", main$prompt_iv_se),
  sprintf("\\newcommand{\\MainReducedForm}{%.4f}", main$reduced_form),
  sprintf("\\newcommand{\\MainFirstStage}{%.4f}", main$first_stage),
  sprintf("\\newcommand{\\MainFirstStageF}{%.1f}", main$first_stage_f),
  sprintf("\\newcommand{\\HeldoutPromptIV}{%.4f}", heldout$prompt_iv),
  sprintf("\\newcommand{\\DoseHighDoveBP}{%.2f}", 100 * dose[arm == "dove_high"]$estimate),
  sprintf("\\newcommand{\\DoseLowDoveBP}{%.2f}", 100 * dose[arm == "dove_low"]$estimate),
  sprintf("\\newcommand{\\DoseNeutralBP}{%.2f}", 100 * dose[arm == "neutral"]$estimate),
  sprintf("\\newcommand{\\DoseLowHawkBP}{%.2f}", 100 * dose[arm == "hawk_low"]$estimate),
  sprintf("\\newcommand{\\DoseHighHawkBP}{%.2f}", 100 * dose[arm == "hawk_high"]$estimate),
  sprintf("\\newcommand{\\LooRtwo}{%.4f}", d8_loo$r2_cv),
  sprintf("\\newcommand{\\LooRMSE}{%.4f}", d8_loo$rmse),
  sprintf("\\newcommand{\\ExpandingCompositeRMSE}{%.4f}", d8_composite$rmse),
  sprintf("\\newcommand{\\ExpandingFocusAROneRMSE}{%.4f}", d8_ar1$rmse),
  sprintf(
    "\\newcommand{\\ExpandingCompositeROOS}{%.4f}",
    d8_composite$r2_oos_vs_historical_mean
  ),
  sprintf("\\newcommand{\\CompositeLossDiffP}{%.4f}", d8_composite_loss$p_hac),
  sprintf("\\newcommand{\\BatesGrangerLossDiffP}{%.4f}", d8_bates_loss$p_hac)
)
writeLines(tex, file.path(result_dir, "intext_numbers.tex"), useBytes = TRUE)


if (any(checks$status != "PASS")) stop("Terra verification is incomplete. See selected-run verification_report.csv")
cat(sprintf("Verified %d selected-run checks\n",nrow(checks)))
