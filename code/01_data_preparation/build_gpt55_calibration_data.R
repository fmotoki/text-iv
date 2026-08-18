suppressPackageStartupMessages({
  library(DBI)
  library(RSQLite)
  library(data.table)
  library(haven)
})

data.table::setDTthreads(1L)

root <- normalizePath(getwd(), winslash = "/", mustWork = TRUE)
database_path <- file.path(
  root, "outputs", "work", "prompt_iv_replication.sqlite"
)
controls_path <- file.path(
  root, "data", "raw", "calibration_controls",
  "copom_meeting_activity_controls.csv"
)
output_path <- file.path(
  root, "outputs", "work", "gpt55_calibration_data.dta"
)
qa_path <- file.path(
  root, "results", "qa", "gpt55_calibration_construction.csv"
)

if (!file.exists(database_path)) {
  stop("Missing working replication database: ", database_path)
}
if (!file.exists(controls_path)) {
  stop("Missing non-LLM meeting controls: ", controls_path)
}

controls <- fread(controls_path, encoding = "UTF-8", showProgress = FALSE)
required_controls <- c(
  "reuniao", "date_daily", "ibcbr_agro", "ibcbr_industry", "ibcbr_service"
)
if (!identical(names(controls), required_controls)) {
  stop(
    "Meeting-control columns must be exactly: ",
    paste(required_controls, collapse = ", ")
  )
}
controls[, `:=`(
  reuniao = as.integer(reuniao),
  date_daily = as.Date(date_daily)
)]
setorder(controls, reuniao)
if (nrow(controls) != 71L || uniqueN(controls$reuniao) != 71L ||
    !identical(range(controls$reuniao), c(203L, 273L)) ||
    anyNA(controls)) {
  stop("Meeting controls must be a complete 71-meeting panel, 203--273")
}
activity_columns <- c("ibcbr_agro", "ibcbr_industry", "ibcbr_service")
if (!all(vapply(controls[, ..activity_columns], is.numeric, logical(1L))) ||
    any(!is.finite(as.matrix(controls[, ..activity_columns]))) ||
    is.unsorted(controls$date_daily, strictly = TRUE) ||
    !identical(
      as.character(range(controls$date_daily)),
      c("2016-11-30", "2025-09-17")
    )) {
  stop("Meeting-control dates or IBC-Br component index levels are invalid")
}

connection <- dbConnect(SQLite(), database_path)
on.exit(dbDisconnect(connection), add = TRUE)
dbExecute(connection, "PRAGMA busy_timeout = 60000")

run_id <- "common_prior_original_v1_20260712"
forecasts <- as.data.table(dbGetQuery(
  connection,
  paste(
    "SELECT meeting_number, forecaster_type, previous_expectation,",
    "new_expectation, forecast_revision, model, reasoning_effort,",
    "prompt_version, scenario, source_role, status",
    "FROM agent_results_latest WHERE run_id = ?"
  ),
  params = list(run_id)
))

if (nrow(forecasts) != 142L ||
    uniqueN(forecasts[, .(meeting_number, forecaster_type)]) != 142L ||
    uniqueN(forecasts$meeting_number) != 71L ||
    !setequal(forecasts$forecaster_type, c("anchored", "unanchored"))) {
  stop("GPT-5.5 original-minute run is not a complete 71 x 2 panel")
}
if (!all(forecasts$model == "gpt-5.5") ||
    !all(forecasts$reasoning_effort == "high") ||
    !all(forecasts$prompt_version == "forecaster_common_prior_v1") ||
    !all(forecasts$scenario == "original") ||
    !all(forecasts$source_role == "original") ||
    !all(forecasts$status == "completed") ||
    anyNA(forecasts[, .(
      previous_expectation, new_expectation, forecast_revision
    )])) {
  stop("GPT-5.5 original-minute run metadata or values are invalid")
}
forecast_numeric_columns <- c(
  "previous_expectation", "new_expectation", "forecast_revision"
)
if (any(!is.finite(as.matrix(forecasts[, ..forecast_numeric_columns])))) {
  stop("GPT-5.5 original-minute forecasts contain non-finite values")
}
if (max(abs(
  forecasts$forecast_revision -
    (forecasts$new_expectation - forecasts$previous_expectation)
)) > 1e-12) {
  stop("Stored GPT-5.5 forecast revisions fail the level-minus-prior identity")
}

setorder(forecasts, forecaster_type, meeting_number)
forecasts[, expected_prior := shift(new_expectation), by = forecaster_type]
initial_prior <- 3.8
if (any(abs(
  forecasts[meeting_number == 203L, previous_expectation] - initial_prior
) > 1e-12)) {
  stop("GPT-5.5 original-minute paths have an unexpected initial prior")
}
forecasts[meeting_number == 203L, expected_prior := initial_prior]
forecasts[, continuity_error := abs(previous_expectation - expected_prior)]
if (max(forecasts$continuity_error) > 1e-12) {
  stop("GPT-5.5 original-minute paths fail their within-profile continuity check")
}

forecast_levels <- dcast(
  forecasts,
  meeting_number ~ forecaster_type,
  value.var = "new_expectation"
)
setnames(
  forecast_levels,
  c("meeting_number", "anchored", "unanchored"),
  c("reuniao", "forecast_anchored_gpt55", "forecast_unanchored_gpt55")
)

focus <- as.data.table(dbGetQuery(
  connection,
  paste(
    "SELECT date, median FROM external_focus_statistics",
    "WHERE source_entity = 'ExpectativasMercadoInflacao12Meses'",
    "AND indicator = 'IPCA' AND smoothed = 'N' AND calculation_base = 0"
  )
))
focus[, date_daily := as.Date(date)]
focus[, date := NULL]
if (nrow(focus) == 0L || uniqueN(focus$date_daily) != nrow(focus) ||
    anyNA(focus) || any(!is.finite(focus$median))) {
  stop("Focus twelve-month-ahead IPCA panel has duplicate or invalid dates")
}
setnames(focus, "median", "inflacao12m")

tone <- as.data.table(dbGetQuery(
  connection,
  paste(
    "SELECT meeting_number, score, word_count FROM tone_scores",
    "WHERE run_id = 'v10_referee_d5_25draws_20260620'",
    "AND role = 'original_text'"
  )
))
tone_check <- tone[, .(
  rows = .N,
  score_values = uniqueN(score),
  word_count_values = uniqueN(word_count)
), by = meeting_number]
if (nrow(tone_check) != 71L || any(tone_check$rows != 25L) ||
    any(tone_check$score_values != 1L) ||
    any(tone_check$word_count_values != 1L)) {
  stop("Original-minute tone is not invariant across the 25 regenerations")
}
if (anyNA(tone) || any(!is.finite(tone$score)) ||
    any(!is.finite(tone$word_count)) || any(tone$word_count <= 0)) {
  stop("Original-minute tone scores or word counts are invalid")
}
tone <- tone[, .(
  score_orig = 100 * first(score),
  wc_orig = as.integer(first(word_count))
), by = .(reuniao = meeting_number)]

calibration <- Reduce(
  function(x, y) merge(x, y, by = "reuniao", all = FALSE, sort = FALSE),
  list(controls, forecast_levels, tone)
)
calibration <- merge(
  calibration,
  focus,
  by = "date_daily",
  all = FALSE,
  sort = FALSE
)
setorder(calibration, reuniao)
calibration[, l_inflacao12m := shift(inflacao12m)]
setcolorder(calibration, c(
  "inflacao12m", "date_daily", "reuniao", "forecast_anchored_gpt55",
  "forecast_unanchored_gpt55", "score_orig", "wc_orig", "ibcbr_agro",
  "ibcbr_industry", "ibcbr_service", "l_inflacao12m"
))

level_columns <- setdiff(names(calibration), c("date_daily", "l_inflacao12m"))
if (nrow(calibration) != 71L || uniqueN(calibration$reuniao) != 71L ||
    uniqueN(calibration$date_daily) != 71L ||
    anyNA(calibration[, ..level_columns]) ||
    any(!is.finite(as.matrix(calibration[, ..level_columns]))) ||
    !is.na(calibration$l_inflacao12m[[1L]]) ||
    anyNA(calibration[-1L, l_inflacao12m]) ||
    any(!is.finite(calibration[-1L, l_inflacao12m]))) {
  stop("Constructed GPT-5.5 calibration data fail panel or completeness checks")
}

labels <- c(
  inflacao12m = "BCB Focus median twelve-month-ahead IPCA expectation, percent",
  date_daily = "Copom meeting date",
  reuniao = "Copom meeting number",
  forecast_anchored_gpt55 = "GPT-5.5 anchored-profile original-minute forecast",
  forecast_unanchored_gpt55 = "GPT-5.5 unanchored-profile original-minute forecast",
  score_orig = "Original-minute lexicon tone in percentage points",
  wc_orig = "Original-minute word count",
  ibcbr_agro = "Agriculture component of IBC-Br, index level",
  ibcbr_industry = "Industry component of IBC-Br, index level",
  ibcbr_service = "Services component of IBC-Br, index level",
  l_inflacao12m = "One-meeting lag of the Focus expectation"
)
for (variable in names(labels)) {
  attr(calibration[[variable]], "label") <- labels[[variable]]
}

dir.create(dirname(output_path), recursive = TRUE, showWarnings = FALSE)
dir.create(dirname(qa_path), recursive = TRUE, showWarnings = FALSE)
write_dta(as.data.frame(calibration), output_path, version = 14)
fwrite(
  forecasts[, .(
    run_id,
    model,
    meeting_number,
    forecaster_type,
    previous_expectation,
    new_expectation,
    expected_prior,
    continuity_error,
    status = "pass"
  )],
  qa_path
)

cat(sprintf(
  paste0(
    "Built GPT-5.5 calibration input: %d meetings, %d original forecasts, ",
    "max continuity error %.3g\n"
  ),
  nrow(calibration), nrow(forecasts), max(forecasts$continuity_error)
))
