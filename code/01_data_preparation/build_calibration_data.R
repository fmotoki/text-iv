# Build the full-sample calibration only from the selected Terra cohort.
source_root <- normalizePath(getwd(), winslash = "/", mustWork = TRUE)
source(file.path(source_root, "code", "read_run_spec.R"), local = TRUE)
suppressPackageStartupMessages({
  library(DBI)
  library(RSQLite)
  library(data.table)
  library(haven)
})
data.table::setDTthreads(1L)
database_path <- spec$paths$work_database
controls_path <- spec$paths$sector_controls
output_path <- spec$paths$calibration_data
qa_path <- run_path("results/qa/calibration_construction.csv")
audit_path <- run_path("outputs/work/calibration_audit.json")
if (!file.exists(database_path)) stop("Missing selected Terra working database")
if (!file.exists(controls_path)) stop("Strict activity controls are not ready. Inspect meeting_ibc_availability.csv")
if (!file.exists(spec$paths$availability_audit)) stop("Missing activity-availability evidence")

# Canonical sample and strict release-vintage support ----
expected_meetings <- as.integer(spec$sample$meeting_numbers)
if (!identical(expected_meetings, 203:281)) stop("Calibration requires the full 79-meeting design")
dates <- fread(spec$sample$dates_csv, showProgress = FALSE)
dates <- dates[meeting_number %in% expected_meetings, .(
  reuniao = as.integer(meeting_number), date_daily = as.Date(meeting_date)
)]
setorder(dates, reuniao)
if (!identical(dates$reuniao, expected_meetings) || anyNA(dates) ||
    anyDuplicated(dates$date_daily)) stop("Canonical meeting dates are incomplete or duplicated")
availability <- fread(spec$paths$availability_audit, showProgress = FALSE)
expected_availability <- CJ(meeting_number = expected_meetings,
                            series_code = as.integer(spec$activity_controls$sector_codes))
if (!all(c("meeting_number", "series_code", "availability_status") %in% names(availability)) ||
    nrow(availability) != 237L || anyDuplicated(availability[, .(meeting_number, series_code)]) ||
    nrow(fsetdiff(expected_availability, availability[, .(meeting_number, series_code)])) ||
    anyNA(availability$availability_status) || any(availability$availability_status != "READY")) {
  stop("Strict release, public-availability and vintage support is incomplete")
}
controls <- fread(controls_path, encoding = "UTF-8", showProgress = FALSE)
required_controls <- c("reuniao", "date_daily", "ibcbr_agro", "ibcbr_industry", "ibcbr_service")
if (!identical(names(controls), required_controls)) stop("Unexpected sector-control schema")
controls[, reuniao := as.integer(reuniao)]
controls[, date_daily := as.Date(date_daily)]
setorder(controls, reuniao)
if (!identical(controls$reuniao, expected_meetings) || anyNA(controls) ||
    !identical(controls$date_daily, dates$date_daily)) stop("Sector controls do not match canonical dates")
activity_columns <- c("ibcbr_agro", "ibcbr_industry", "ibcbr_service")
if (!all(vapply(controls[, ..activity_columns], is.numeric, logical(1))) ||
    any(!is.finite(as.matrix(controls[, ..activity_columns])))) stop("Invalid sector-control values")

# Original trajectories, same-date Focus and unchanged original text ----
connection <- dbConnect(SQLite(), database_path, flags = RSQLite::SQLITE_RO)
forecasts <- as.data.table(dbGetQuery(connection, paste(
  "SELECT r.meeting_number, r.forecaster_type, r.previous_expectation,",
  "r.new_expectation, r.forecast_revision, r.model, r.reasoning_effort,",
  "r.prompt_version, r.scenario, r.source_role, r.status, a.source_text_hash",
  "FROM agent_results_latest r JOIN agent_tasks a",
  "ON a.run_id=r.run_id AND a.task_id=r.task_id WHERE r.run_id=?"
), params = list(spec$run_ids$original_priors)))
focus <- as.data.table(dbGetQuery(connection, paste(
  "SELECT date, median FROM external_focus_statistics",
  "WHERE source_entity='ExpectativasMercadoInflacao12Meses'",
  "AND indicator='IPCA' AND smoothed='N' AND calculation_base=0"
)))
tone <- as.data.table(dbGetQuery(connection, paste(
  "SELECT t.meeting_number,t.iteration,t.score,t.word_count,c.text_hash",
  "FROM tone_scores t JOIN corpus_texts c ON c.run_id=t.run_id",
  "AND c.meeting_number=t.meeting_number AND c.iteration=t.iteration AND c.role='original'",
  "WHERE t.run_id=? AND t.role='original_text' AND c.complete=1"
), params = list(spec$run_ids$main_text)))
dbDisconnect(connection)
expected_forecasts <- CJ(meeting_number = expected_meetings,
                         forecaster_type = as.character(spec$profiles))
if (nrow(forecasts) != 158L || anyDuplicated(forecasts[, .(meeting_number, forecaster_type)]) ||
    nrow(fsetdiff(expected_forecasts, forecasts[, .(meeting_number, forecaster_type)])) ||
    anyNA(forecasts) || any(forecasts$model != spec$model) ||
    any(forecasts$reasoning_effort != spec$reasoning_effort) ||
    any(forecasts$prompt_version != spec$prompt_versions$original_priors) ||
    any(forecasts$scenario != "original") || any(forecasts$source_role != "original") ||
    any(forecasts$status != "completed")) stop("Original Terra paths fail exact support or provenance")
numeric_columns <- c("previous_expectation", "new_expectation", "forecast_revision")
if (any(!is.finite(as.matrix(forecasts[, ..numeric_columns]))) ||
    max(abs(forecasts$forecast_revision - (forecasts$new_expectation - forecasts$previous_expectation))) > 1e-12) {
  stop("Original forecast values or revision identity failed")
}
setorder(forecasts, forecaster_type, meeting_number)
forecasts[, expected_prior := shift(new_expectation), by = forecaster_type]
forecasts[meeting_number == 203L, expected_prior := spec$initial_expectation]
forecasts[, continuity_error := abs(previous_expectation - expected_prior)]
if (anyNA(forecasts$continuity_error) || max(forecasts$continuity_error) > 1e-12) stop("Original-profile chronology failed")
expected_tone <- CJ(meeting_number = expected_meetings, iteration = seq_len(spec$draws$main))
if (nrow(tone) != nrow(expected_tone) || anyDuplicated(tone[, .(meeting_number, iteration)]) ||
    nrow(fsetdiff(expected_tone, tone[, .(meeting_number, iteration)])) || anyNA(tone)) stop("Original tone support failed")
tone_check <- tone[, .(scores = uniqueN(score), words = uniqueN(word_count), hashes = uniqueN(text_hash)), by = meeting_number]
if (any(tone_check$scores != 1L | tone_check$words != 1L | tone_check$hashes != 1L) ||
    any(!is.finite(tone$score)) || any(!is.finite(tone$word_count)) || any(tone$word_count <= 0)) {
  stop("Original text, tone or word count varies across regenerations")
}
original_hashes <- unique(tone[, .(meeting_number, text_hash)])
hash_check <- merge(forecasts[, .(meeting_number, source_text_hash)], original_hashes,
                    by = "meeting_number", all = TRUE, allow.cartesian = TRUE)
if (anyNA(hash_check) || any(hash_check$source_text_hash != hash_check$text_hash)) stop("Original forecast/text hashes disagree")
focus[, date_daily := as.Date(date)]
focus <- focus[date_daily %in% dates$date_daily, .(date_daily, inflacao12m = median)]
if (nrow(focus) != 79L || anyDuplicated(focus$date_daily) || anyNA(focus) ||
    any(!is.finite(focus$inflacao12m))) stop("Same-date unsmoothed IPCA Focus target is missing or tied")
forecast_levels <- dcast(forecasts, meeting_number ~ forecaster_type, value.var = "new_expectation")
setnames(forecast_levels, c("meeting_number", "anchored", "unanchored"),
         c("reuniao", "forecast_anchored", "forecast_unanchored"))
tone <- tone[, .(score_orig = 100 * first(score), wc_orig = as.integer(first(word_count))),
             by = .(reuniao = meeting_number)]

# Preserve intended keys and expose unmatched inputs ----
calibration <- merge(controls, forecast_levels, by = "reuniao", all = TRUE, sort = FALSE)
calibration <- merge(calibration, tone, by = "reuniao", all = TRUE, sort = FALSE)
calibration <- merge(calibration, focus, by = "date_daily", all.x = TRUE, sort = FALSE)
setorder(calibration, reuniao)
calibration[, l_inflacao12m := shift(inflacao12m)]
setcolorder(calibration, c("inflacao12m", "date_daily", "reuniao", "forecast_anchored",
  "forecast_unanchored", "score_orig", "wc_orig", "ibcbr_agro", "ibcbr_industry",
  "ibcbr_service", "l_inflacao12m"))
level_columns <- setdiff(names(calibration), c("date_daily", "l_inflacao12m"))
if (!identical(calibration$reuniao, expected_meetings) || anyNA(calibration[, ..level_columns]) ||
    any(!is.finite(as.matrix(calibration[, ..level_columns]))) ||
    !is.na(calibration$l_inflacao12m[1L]) || anyNA(calibration$l_inflacao12m[-1L])) stop("Calibration completeness failed")
labels <- c(inflacao12m = "Focus same-date twelve-month-ahead IPCA median, percent",
  date_daily = "Copom meeting date", reuniao = "Copom meeting number",
  forecast_anchored = "Synthetic Terra anchored original-minute forecast",
  forecast_unanchored = "Synthetic Terra unanchored original-minute forecast",
  score_orig = "Original lexicon tone in percentage points", wc_orig = "Original word count",
  ibcbr_agro = "Agriculture IBC-Br release-vintage index", ibcbr_industry = "Industry IBC-Br release-vintage index",
  ibcbr_service = "Services IBC-Br release-vintage index", l_inflacao12m = "Previous-meeting Focus expectation")
for (variable in names(labels)) attr(calibration[[variable]], "label") <- labels[[variable]]
for (path in c(output_path, qa_path, audit_path)) dir.create(dirname(path), recursive = TRUE, showWarnings = FALSE)
write_dta(as.data.frame(calibration), output_path, version = 14)
forecasts[, run_id := spec$run_ids$original_priors]
fwrite(forecasts, qa_path)
jsonlite::write_json(list(model = spec$model, reasoning_effort = spec$reasoning_effort,
  fingerprint = spec$fingerprint, spec_sha256 = spec_sha256, original_run = spec$run_ids$original_priors,
  main_text_run = spec$run_ids$main_text, meetings = expected_meetings,
  calibration_sha256 = digest::digest(output_path, algo = "sha256", file = TRUE, serialize = FALSE),
  availability_sha256 = digest::digest(spec$paths$availability_audit, algo = "sha256", file = TRUE, serialize = FALSE),
  raw_snapshot = spec$raw_snapshot, focus_target_audit = run_path("data/analysis/meeting_focus_target_audit.csv"),
  focus = list(entity = "ExpectativasMercadoInflacao12Meses", indicator = "IPCA", smoothed = "N",
               calculation_base = 0L, selected_observation_dates = as.character(calibration$date_daily)),
  activity_policy = spec$activity_controls, source_database = database_path,
  generated_at_utc = format(Sys.time(), tz = "UTC", usetz = TRUE)), audit_path,
  auto_unbox = TRUE, pretty = TRUE, na = "null")
cat("Built 79-meeting Terra calibration from 158 verified original forecasts.\n")
