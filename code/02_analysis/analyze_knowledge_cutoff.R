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
  library(DBI)
  library(RSQLite)
})
data.table::setDTthreads(1L)

# Frozen calibration on meetings 203:271 ----
panel <- as.data.table(read_dta(spec$paths$calibration_data))
panel[, reuniao := as.integer(reuniao)]
panel[, date_daily := as.Date(date_daily)]
setorder(panel, reuniao)
dates <- fread(spec$sample$dates_csv, showProgress = FALSE)
dates <- dates[meeting_number %in% 203:281]
setorder(dates, meeting_number)
if (!identical(panel$reuniao, 203:281) || nrow(panel) != 79L ||
    anyDuplicated(panel$date_daily) || !identical(as.integer(dates$meeting_number), 203:281) ||
    !identical(as.character(panel$date_daily), as.character(as.Date(dates$meeting_date)))) {
  stop("Cutoff diagnostic requires the canonical 79-meeting panel")
}
panel[, after_knowledge_cutoff := date_daily > as.Date(spec$cutoff)]
if (sum(panel$after_knowledge_cutoff) != 5L ||
    !identical(panel[after_knowledge_cutoff == TRUE, reuniao], 277:281)) stop("Knowledge-cutoff classification failed")
training_meetings <- seq.int(spec$evaluation$training_first, spec$evaluation$training_last)
pre_meetings <- as.integer(spec$evaluation$pre)
post_meetings <- as.integer(spec$evaluation$post)
if (!identical(training_meetings, 203:271) || !identical(pre_meetings, 272:276) ||
    !identical(post_meetings, 277:281)) stop("Frozen calibration or evaluation memberships changed")
model_columns <- c("inflacao12m", "forecast_anchored", "forecast_unanchored", "wc_orig",
                   "ibcbr_agro", "ibcbr_service", "ibcbr_industry")
if (!all(model_columns %in% names(panel)) || anyNA(panel[, ..model_columns]) ||
    any(!is.finite(as.matrix(panel[, ..model_columns])))) stop("Incomplete/non-finite calibration variables")
training <- panel[reuniao %in% training_meetings]
if (nrow(training) != 69L) stop("Frozen calibration must have exactly 69 training meetings")
fit <- stats::lm(inflacao12m ~ forecast_anchored + forecast_unanchored + wc_orig +
  ibcbr_agro + ibcbr_service + ibcbr_industry, data = training, na.action = na.fail)
if (fit$rank != length(stats::coef(fit)) || any(!is.finite(stats::coef(fit)))) stop("Frozen calibration is rank deficient")
coefficients <- data.table(term = names(stats::coef(fit)), estimate = unname(stats::coef(fit)))
coefficient_hash <- digest::digest(coefficients, algo = "sha256")
training_hash <- digest::digest(training[, c("reuniao", "date_daily", model_columns), with=FALSE], algo="sha256")
fit_fingerprint <- digest::digest(list(spec = spec$fingerprint, training = training_hash,
                                     coefficients = coefficient_hash), algo="sha256")
# Persistence uses the previous observed Focus value, including earlier test meetings.
panel[, focus_persistence := shift(inflacao12m)]
evaluation <- panel[reuniao %in% c(pre_meetings, post_meetings)]
evaluation[, frozen_calibrated_composite := as.numeric(stats::predict(fit, newdata = evaluation))]
evaluation[, raw_anchored := forecast_anchored]
evaluation[, raw_unanchored := forecast_unanchored]
evaluation[, window := fifelse(reuniao %in% pre_meetings, "pre", "post")]
predictions <- melt(evaluation,
  id.vars=c("reuniao", "date_daily", "inflacao12m", "after_knowledge_cutoff", "window"),
  measure.vars=c("frozen_calibrated_composite", "raw_anchored", "raw_unanchored", "focus_persistence"),
  variable.name="series", value.name="prediction", variable.factor=FALSE)
setnames(predictions, c("reuniao", "inflacao12m"), c("meeting_number", "focus"))
predictions[, signed_error := prediction - focus]
predictions[, squared_error := signed_error^2]
predictions[, absolute_error := abs(signed_error)]
if (nrow(predictions) != 40L || anyNA(predictions) || any(!is.finite(predictions$prediction))) stop("Incomplete evaluation predictions")
metrics <- predictions[, .(n=.N, rmse=sqrt(mean(squared_error)), mae=mean(absolute_error),
  mean_signed_error=mean(signed_error)), by=.(window, series)]
if (nrow(metrics) != 8L || any(metrics$n != 5L)) stop("Metrics must retain five meetings in every window")
benchmark <- metrics[series=="focus_persistence", .(window, benchmark_rmse=rmse)]
metrics <- merge(metrics, benchmark, by="window", all.x=TRUE, sort=FALSE)
# The descriptive ratio compares only the frozen composite with Focus persistence.
metrics[, `:=`(benchmark_rmse_ratio = NA_real_, ratio_status = "not_applicable")]
metrics[series == "frozen_calibrated_composite", `:=`(
  benchmark_rmse_ratio = fifelse(benchmark_rmse == 0, NA_real_, rmse / benchmark_rmse),
  ratio_status = fifelse(benchmark_rmse == 0, "undefined_zero_benchmark_rmse", "defined"))]
metric_changes <- dcast(metrics, series ~ window, value.var="rmse")
metric_changes[, post_minus_pre_rmse := post - pre]
metrics <- merge(metrics, metric_changes[, .(series, post_minus_pre_rmse)], by="series", all.x=TRUE)
mae_changes <- dcast(metrics, series ~ window, value.var="mae")
mae_changes[, post_minus_pre_mae := post-pre]
metrics <- merge(metrics,mae_changes[,.(series,post_minus_pre_mae)],by="series",all.x=TRUE)
bias_changes <- dcast(metrics, series ~ window, value.var="mean_signed_error")
bias_changes[, post_minus_pre_mean_signed_error := post-pre]
metrics <- merge(metrics,bias_changes[,.(series,post_minus_pre_mean_signed_error)],by="series",all.x=TRUE)

# Immediate framing contrasts with common original-path priors ----
if (!file.exists(spec$paths$work_database)) stop("Missing Terra database. Empirical framing diagnostic is NOT RUN")
connection <- dbConnect(SQLite(), spec$paths$work_database, flags=RSQLite::SQLITE_RO)
framing <- as.data.table(dbGetQuery(connection, paste(
  "SELECT r.meeting_number,r.corpus_iteration,r.forecaster_type,r.source_role,",
  "r.new_expectation,r.previous_expectation,r.forecast_revision,r.model,r.reasoning_effort,r.status,",
  "t.fixed_prior,t.source_run_id,t.source_text_hash,c.text_hash AS corpus_hash",
  "FROM agent_results_latest r JOIN agent_tasks t ON t.run_id=r.run_id AND t.task_id=r.task_id",
  "JOIN corpus_texts c ON c.run_id=t.source_run_id AND c.meeting_number=t.meeting_number",
  "AND c.iteration=t.corpus_iteration AND c.role=t.source_role AND c.complete=1",
  "WHERE r.run_id=? AND r.meeting_number BETWEEN 272 AND 281",
  "AND r.source_role IN ('neutral','dove_high','dove_low','hawk_low','hawk_high')"
), params=list(spec$run_ids$common_prior_dose)))
original <- as.data.table(dbGetQuery(connection, paste(
  "SELECT meeting_number,forecaster_type,previous_expectation,model,reasoning_effort,status",
  "FROM agent_results_latest WHERE run_id=? AND meeting_number BETWEEN 272 AND 281"
), params=list(spec$run_ids$original_priors)))
dbDisconnect(connection)
expected_framing <- CJ(meeting_number=c(pre_meetings, post_meetings),
  corpus_iteration=seq_len(spec$draws$dose), forecaster_type=as.character(spec$profiles),
  source_role=c("neutral", "dove_high", "dove_low", "hawk_low", "hawk_high"))
key_columns <- names(expected_framing)
if (nrow(framing) != nrow(expected_framing) || anyDuplicated(framing[, ..key_columns]) ||
    nrow(fsetdiff(expected_framing, framing[, ..key_columns])) || anyNA(framing) ||
    any(framing$model != spec$model) || any(framing$reasoning_effort != spec$reasoning_effort) ||
    any(framing$status != "completed") || any(framing$source_run_id != spec$run_ids$dose_text) ||
    any(framing$source_text_hash != framing$corpus_hash)) stop("Incomplete/mismatched Terra framing counterparts or source hashes")
if (any(!is.finite(framing$new_expectation)) || any(!is.finite(framing$fixed_prior)) ||
    max(abs(framing$previous_expectation-framing$fixed_prior)) > 1e-12 ||
    max(abs(framing$forecast_revision-(framing$new_expectation-framing$previous_expectation))) > 1e-12) {
  stop("Framing forecast/prior identity failed")
}
expected_original <- CJ(meeting_number=c(pre_meetings, post_meetings), forecaster_type=as.character(spec$profiles))
original_keys <- names(expected_original)
if (nrow(original) != 20L || anyDuplicated(original[, ..original_keys]) ||
    nrow(fsetdiff(expected_original, original[, ..original_keys])) || anyNA(original) ||
    any(original$model != spec$model) || any(original$reasoning_effort != spec$reasoning_effort) ||
    any(original$status != "completed")) stop("Original-path evaluation priors are incomplete")
setnames(original, "previous_expectation", "original_prior")
framing <- merge(framing, original[, .(meeting_number, forecaster_type, original_prior)],
  by=c("meeting_number", "forecaster_type"), all.x=TRUE)
if (max(abs(framing$fixed_prior-framing$original_prior)) > 1e-12) stop("Treatment prior is not the original-path prior supplied at t")
neutral <- framing[source_role=="neutral", .(meeting_number, corpus_iteration, forecaster_type,
  neutral_forecast=new_expectation, neutral_prior=fixed_prior, neutral_hash=source_text_hash)]
framing_draws <- merge(framing[source_role!="neutral"], neutral,
  by=c("meeting_number", "corpus_iteration", "forecaster_type"), all.x=TRUE)
if (anyNA(framing_draws) || any(abs(framing_draws$fixed_prior-framing_draws$neutral_prior)>1e-12)) stop("Neutral counterpart or common prior failed")
setnames(framing_draws, c("source_role", "new_expectation"), c("contrast", "arm_forecast"))
framing_draws[, contrast_pp := arm_forecast - neutral_forecast]
# Both profiles remain distinct until their frozen unrestricted slopes are applied.
composite <- dcast(framing_draws,
  meeting_number + corpus_iteration + contrast ~ forecaster_type, value.var="contrast_pp")
if (anyNA(composite)) stop("Composite contrast is missing a profile")
composite[, contrast_pp := stats::coef(fit)[["forecast_anchored"]]*anchored +
  stats::coef(fit)[["forecast_unanchored"]]*unanchored]
composite[, forecaster_type := "composite"]
framing_draws <- rbindlist(list(framing_draws, composite), use.names=TRUE, fill=TRUE)
framing_draws <- merge(framing_draws, evaluation[, .(meeting_number=reuniao, date_daily, window)],
  by="meeting_number", all.x=TRUE)
framing_meetings <- framing_draws[, .(contrast_pp=mean(contrast_pp)),
  by=.(meeting_number, date_daily, window, contrast, forecaster_type)]
framing_summary <- framing_meetings[, .(n_meetings=.N, mean_contrast_pp=mean(contrast_pp)),
  by=.(window, contrast, forecaster_type)]
if (nrow(framing_meetings)!=120L || nrow(framing_summary)!=24L || any(framing_summary$n_meetings!=5L)) {
  stop("Framing summaries must weight five complete meeting means equally")
}
framing_changes <- dcast(framing_summary, contrast+forecaster_type~window, value.var="mean_contrast_pp")
framing_changes[, post_minus_pre_pp := post-pre]
framing_summary <- merge(framing_summary, framing_changes[, .(contrast, forecaster_type, post_minus_pre_pp)],
  by=c("contrast", "forecaster_type"), all.x=TRUE)

# Save descriptive targets after validating all counterparts ----
for (folder in c("outputs/tables", "results/tables")) dir.create(run_path(folder), recursive=TRUE, showWarnings=FALSE)
fwrite(training, run_path(file.path("outputs/tables", paste0("knowledge_cutoff_training_",result_stamp,".csv"))))
coefficient_path <- run_path(file.path("outputs/tables", paste0("knowledge_cutoff_coefficients_",result_stamp,".csv")))
fwrite(coefficients,coefficient_path)
coefficient_file_sha256 <- digest::digest(coefficient_path,algo="sha256",file=TRUE,serialize=FALSE)
fwrite(predictions, run_path(file.path("outputs/tables", paste0("knowledge_cutoff_predictions_",result_stamp,".csv"))))
fwrite(metrics, run_path(file.path("outputs/tables", paste0("knowledge_cutoff_metrics_",result_stamp,".csv"))))
fwrite(framing_draws, run_path(file.path("outputs/tables", paste0("knowledge_cutoff_framing_draws_",result_stamp,".csv"))))
fwrite(framing_meetings, run_path(file.path("outputs/tables", paste0("knowledge_cutoff_framing_meetings_",result_stamp,".csv"))))
fwrite(framing_summary, run_path(file.path("outputs/tables", paste0("knowledge_cutoff_framing_summary_",result_stamp,".csv"))))
jsonlite::write_json(list(model=spec$model, reasoning_effort=spec$reasoning_effort,
  release_id=spec$release_id, fingerprint=spec$fingerprint, spec_sha256=spec_sha256,
  calibration_sha256=calibration_audit$calibration_sha256, training_input_sha256=training_hash,
  frozen_coefficient_sha256=coefficient_file_sha256,
  coefficient_value_sha256=coefficient_hash, fit_fingerprint=fit_fingerprint,
  units="percentage_points", error_sign="prediction_minus_Focus", training=training_meetings,
  pre=pre_meetings, post=post_meetings, n_per_window=5L, descriptive=TRUE,
  target="same-date Focus IPCA twelve-month-ahead median, unsmoothed, calculation base zero",
  source_runs=spec$run_ids, raw_snapshot=spec$raw_snapshot,
  activity_policy=spec$activity_controls, availability_audit=spec$paths$availability_audit,
  activity_information_set=as.data.frame(fread(spec$paths$availability_audit,showProgress=FALSE)),
  availability_audit_sha256=digest::digest(spec$paths$availability_audit,algo="sha256",file=TRUE,serialize=FALSE),
  information_limit="Release alignment alone does not establish real-time or human external validity",
  generated_at_utc=format(Sys.time(),tz="UTC",usetz=TRUE)),
  run_path(file.path("outputs/tables",paste0("knowledge_cutoff_audit_",result_stamp,".json"))),
  auto_unbox=TRUE,pretty=TRUE,na="null")
# The reporting stage builds the two descriptive LaTeX tables and the two figures.
cat("Completed frozen-69 Focus agreement diagnostic on ten evaluation meetings.\n")
