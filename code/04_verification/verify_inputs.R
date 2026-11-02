# Validate source bytes and the selected cohort before any analytical producer.
source_root <- normalizePath(getwd(), winslash = "/", mustWork = TRUE)
source(file.path(source_root, "code", "read_run_spec.R"))
suppressPackageStartupMessages({library(DBI); library(RSQLite); library(data.table)})
database <- spec$paths$frozen_database
if (!file.exists(database)) stop("NOT RUN: the new Terra candidate source is absent")
sidecar <- paste0(database, ".manifest.json")
if (!file.exists(sidecar)) stop("NOT RUN: candidate export provenance is absent")
candidate <- jsonlite::fromJSON(sidecar)
if (!identical(candidate$fingerprint, spec$fingerprint) ||
    !identical(candidate$spec_sha256, spec_sha256) ||
    !identical(candidate$database_sha256,
               digest::digest(database, algo = "sha256", file = TRUE, serialize = FALSE))) {
  stop("Candidate export identity or bytes changed")
}
connection <- dbConnect(SQLite(), database, flags = RSQLite::SQLITE_RO)
integrity <- dbGetQuery(connection, "PRAGMA integrity_check")[[1L]]
if (!identical(integrity, "ok")) stop("Candidate database integrity failed")
runs <- dbReadTable(connection, "runs")
required_runs <- unname(unlist(spec$run_ids))
if (!all(required_runs %in% runs$run_id)) stop("Selected run registration is incomplete")
for (identifier in required_runs) {
  row <- runs[runs$run_id == identifier, , drop = FALSE]
  if (nrow(row) != 1L) stop("Duplicate selected run registration")
  provenance <- jsonlite::fromJSON(row$metadata_json)
  if (!identical(provenance$fingerprint, spec$fingerprint) ||
      !identical(provenance$spec_sha256, spec_sha256)) stop("Run configuration identity mismatch: ", identifier)
}
forecasts <- as.data.table(dbGetQuery(connection, paste(
  "SELECT run_id,meeting_number,corpus_iteration,forecaster_type,source_role,status,model,reasoning_effort",
  "FROM agent_results_latest"
)))
if (anyNA(forecasts[, .(run_id,meeting_number,forecaster_type,source_role,status,model,reasoning_effort)]) ||
    any(forecasts$status != "completed") || any(forecasts$model != spec$model) ||
    any(forecasts$reasoning_effort != spec$reasoning_effort)) stop("Invalid or mixed-model forecast cohort")
original <- forecasts[run_id == spec$run_ids$original_priors]
expected <- CJ(meeting_number = as.integer(spec$sample$meeting_numbers), forecaster_type = spec$profiles)
if (nrow(original) != nrow(expected) ||
    anyDuplicated(original[, .(meeting_number,forecaster_type)]) ||
    nrow(fsetdiff(expected, original[, .(meeting_number,forecaster_type)]))) stop("Incomplete original trajectory")
for (design in c("common_prior_main", "common_prior_dose", "recursive_dose")) {
  is_main <- design == "common_prior_main"
  roles <- if (is_main) spec$roles$main else spec$roles$dose
  draws <- if (is_main) spec$draws$main else spec$draws$dose
  expected <- CJ(meeting_number = as.integer(spec$sample$meeting_numbers),
                 corpus_iteration = seq_len(draws), source_role = roles, forecaster_type = spec$profiles)
  actual <- forecasts[run_id == spec$run_ids[[design]]]
  keys <- c("meeting_number","corpus_iteration","source_role","forecaster_type")
  if (nrow(actual) != nrow(expected) || anyDuplicated(actual[, ..keys]) ||
      nrow(fsetdiff(expected, actual[, ..keys]))) stop("Incomplete selected forecast keys: ", design)
}
scoring <- as.data.table(dbGetQuery(connection, paste(
  "SELECT s.run_id,s.source_run_id,s.meeting_number,s.iteration,s.text_role,s.text_hash,s.score,",
  "s.model,s.reasoning_effort,s.status,s.runner_dry_run,s.prompt_version,s.prompt_hash,",
  "s.request_input_hash,s.raw_response_hash,s.spec_fingerprint,",
  "t.meeting_number AS task_meeting,t.iteration AS task_iteration,t.text_role AS task_role,",
  "t.source_run_id AS task_source,t.text_hash AS task_hash,t.prompt_hash AS task_prompt,",
  "t.request_input_hash AS task_request,c.text_hash AS corpus_hash",
  "FROM blinded_semantic_scores s LEFT JOIN blinded_semantic_tasks t",
  "ON t.run_id=s.run_id AND t.task_id=s.task_id LEFT JOIN corpus_texts c",
  "ON c.run_id=s.source_run_id AND c.meeting_number=s.meeting_number",
  "AND c.iteration=s.iteration AND c.role=s.text_role AND c.complete=1"
)))
expected_semantic <- CJ(meeting_number = as.integer(spec$sample$meeting_numbers),
  iteration = seq_len(spec$draws$dose), text_role = spec$roles$dose)
keys <- names(expected_semantic)
if (nrow(scoring) != nrow(expected_semantic) || anyNA(scoring) ||
    anyDuplicated(scoring[, ..keys]) || nrow(fsetdiff(expected_semantic, scoring[, ..keys])) ||
    any(scoring$run_id != spec$run_ids$blinded_scoring) ||
    any(scoring$source_run_id != spec$run_ids$dose_text) || any(scoring$task_source != spec$run_ids$dose_text) ||
    any(scoring$status != "completed") || any(scoring$runner_dry_run != 0L) ||
    any(scoring$model != spec$model) || any(scoring$reasoning_effort != spec$reasoning_effort) ||
    any(scoring$spec_fingerprint != spec$fingerprint) || any(!is.finite(scoring$score)) ||
    any(scoring$prompt_version != spec$prompt_versions$semantic) ||
    any(scoring$task_meeting != scoring$meeting_number) || any(scoring$task_iteration != scoring$iteration) ||
    any(scoring$task_role != scoring$text_role) || any(scoring$task_hash != scoring$text_hash) ||
    any(scoring$text_hash != scoring$corpus_hash) || any(scoring$task_prompt != scoring$prompt_hash) ||
    any(scoring$task_request != scoring$request_input_hash) ||
    any(!grepl("^[0-9a-f]{64}$", scoring$raw_response_hash))) stop("Semantic score keys or provenance failed")

pairwise <- as.data.table(dbGetQuery(connection, paste(
  "SELECT s.run_id,s.source_run_id,s.meeting_number,s.iteration,s.pair_type,s.role_a,s.role_b,",
  "s.text_a_hash,s.text_b_hash,s.model,s.reasoning_effort,s.status,s.runner_dry_run,",
  "s.prompt_version,s.prompt_hash,s.request_input_hash,s.raw_response_hash,s.spec_fingerprint,",
  "t.meeting_number AS task_meeting,t.iteration AS task_iteration,t.pair_type AS task_pair,",
  "t.role_a AS task_role_a,t.role_b AS task_role_b,t.source_run_id AS task_source,",
  "t.text_a_hash AS task_hash_a,t.text_b_hash AS task_hash_b,t.prompt_hash AS task_prompt,",
  "t.request_input_hash AS task_request,a.text_hash AS corpus_a,b.text_hash AS corpus_b",
  "FROM blinded_pairwise_scores s LEFT JOIN blinded_pairwise_tasks t",
  "ON t.run_id=s.run_id AND t.task_id=s.task_id LEFT JOIN corpus_texts a",
  "ON a.run_id=s.source_run_id AND a.meeting_number=s.meeting_number AND a.iteration=s.iteration",
  "AND a.role=s.role_a AND a.complete=1 LEFT JOIN corpus_texts b",
  "ON b.run_id=s.source_run_id AND b.meeting_number=s.meeting_number AND b.iteration=s.iteration",
  "AND b.role=s.role_b AND b.complete=1"
)))
expected_pairwise <- CJ(meeting_number = as.integer(spec$sample$meeting_numbers),
  iteration = seq_len(spec$draws$dose), pair_type = names(spec$pairwise_pairs))
keys <- names(expected_pairwise)
if (nrow(pairwise) != nrow(expected_pairwise) || anyNA(pairwise) ||
    anyDuplicated(pairwise[, ..keys]) || nrow(fsetdiff(expected_pairwise, pairwise[, ..keys])) ||
    any(pairwise$run_id != spec$run_ids$blinded_scoring) || any(pairwise$source_run_id != spec$run_ids$dose_text) ||
    any(pairwise$task_source != spec$run_ids$dose_text) || any(pairwise$status != "completed") ||
    any(pairwise$runner_dry_run != 0L) || any(pairwise$model != spec$model) ||
    any(pairwise$reasoning_effort != spec$reasoning_effort) || any(pairwise$spec_fingerprint != spec$fingerprint) ||
    any(pairwise$prompt_version != spec$prompt_versions$pairwise) ||
    any(pairwise$task_meeting != pairwise$meeting_number) || any(pairwise$task_iteration != pairwise$iteration) ||
    any(pairwise$task_pair != pairwise$pair_type) || any(pairwise$task_role_a != pairwise$role_a) ||
    any(pairwise$task_role_b != pairwise$role_b) || any(pairwise$task_hash_a != pairwise$text_a_hash) ||
    any(pairwise$task_hash_b != pairwise$text_b_hash) || any(pairwise$text_a_hash != pairwise$corpus_a) ||
    any(pairwise$text_b_hash != pairwise$corpus_b) || any(pairwise$task_prompt != pairwise$prompt_hash) ||
    any(pairwise$task_request != pairwise$request_input_hash) ||
    any(!grepl("^[0-9a-f]{64}$", pairwise$raw_response_hash))) stop("Pairwise score keys or provenance failed")
for (index in seq_len(nrow(pairwise))) {
  row <- pairwise[index]
  if (!identical(sort(c(row$role_a, row$role_b)), sort(spec$pairwise_pairs[[row$pair_type]]))) {
    stop("Blinded pair roles do not match the selected comparison")
  }
}

realized <- as.data.table(dbGetQuery(connection, paste(
  "SELECT d.run_id,d.scorer_run_id,d.source_run_id,d.meeting_number,d.iteration,d.text_role,",
  "d.source_text_hash,d.text_hash,d.model,d.reasoning_effort,d.status,d.runner_dry_run,",
  "d.spec_fingerprint,d.semantic_score,d.pairwise_score,c.text_hash AS corpus_hash,s.score AS selected_score",
  "FROM realized_dose_scores d LEFT JOIN corpus_texts c ON c.run_id=d.source_run_id",
  "AND c.meeting_number=d.meeting_number AND c.iteration=d.iteration AND c.role=d.text_role AND c.complete=1",
  "LEFT JOIN blinded_semantic_scores s ON s.run_id=d.scorer_run_id AND s.source_run_id=d.source_run_id",
  "AND s.meeting_number=d.meeting_number AND s.iteration=d.iteration AND s.text_role=d.text_role"
)))
keys <- names(expected_semantic)
if (nrow(realized) != nrow(expected_semantic) || anyNA(realized) || anyDuplicated(realized[, ..keys]) ||
    nrow(fsetdiff(expected_semantic, realized[, ..keys])) || any(realized$run_id != spec$run_ids$dose_text) ||
    any(realized$source_run_id != spec$run_ids$dose_text) || any(realized$scorer_run_id != spec$run_ids$blinded_scoring) ||
    any(realized$model != spec$model) || any(realized$reasoning_effort != spec$reasoning_effort) ||
    any(realized$status != "completed") || any(realized$runner_dry_run != 0L) ||
    any(realized$spec_fingerprint != spec$fingerprint) || any(realized$text_hash != realized$corpus_hash) ||
    any(realized$source_text_hash != realized$corpus_hash) || any(!is.finite(realized$pairwise_score)) ||
    max(abs(realized$semantic_score-realized$selected_score)) > 1e-12) stop("Realized-dose provenance or exact support failed")
focus <- as.data.table(dbGetQuery(connection, paste(
  "SELECT date,median FROM external_focus_statistics",
  "WHERE source_entity='ExpectativasMercadoInflacao12Meses' AND indicator='IPCA'",
  "AND smoothed='N' AND calculation_base=0"
)))
dates <- fread(spec$sample$dates_csv)[meeting_number %in% spec$sample$meeting_numbers]
dates[, meeting_date := as.Date(meeting_date)]
focus[, date := as.Date(date)]
focus <- focus[date %in% dates$meeting_date]
if (nrow(dates) != length(spec$sample$meeting_numbers) || anyDuplicated(dates$meeting_number) ||
    anyNA(dates$meeting_date) || anyDuplicated(dates$meeting_date) ||
    nrow(focus) != nrow(dates) || anyDuplicated(focus$date) || anyNA(focus) ||
    any(!is.finite(focus$median)) || !setequal(focus$date, dates$meeting_date)) {
  stop("Same-meeting-date Focus targets are incomplete or duplicated")
}
dbDisconnect(connection)
if (!file.exists(spec$paths$availability_audit) || !file.exists(spec$paths$sector_controls)) {
  stop("Strict activity inputs are not ready")
}
availability <- fread(spec$paths$availability_audit)
expected <- CJ(meeting_number = as.integer(spec$sample$meeting_numbers),
               series_code = as.integer(spec$activity_controls$sector_codes))
if (nrow(availability) != nrow(expected) ||
    anyDuplicated(availability[, .(meeting_number,series_code)]) ||
    nrow(fsetdiff(expected, availability[, .(meeting_number,series_code)])) ||
    anyNA(availability$availability_status) || any(availability$availability_status != "READY")) {
  stop("Strict publication, vintage or cutoff evidence is incomplete")
}
# Later release metadata adds a checksum inventory. Absence cannot erase export checks.
manifest_path <- run_path("data/metadata/input_checksums.csv")
if (file.exists(manifest_path)) {
  manifest <- fread(manifest_path)
  for (index in seq_len(nrow(manifest))) {
    item <- manifest[index]
    if (!file.exists(item$path) || file.info(item$path)$size != item$size_bytes ||
        !identical(digest::digest(item$path, algo = "sha256", file = TRUE, serialize = FALSE), item$sha256)) {
      stop("Frozen input checksum mismatch: ", item$path)
    }
  }
}
dir.create(run_path("results/qa"), recursive = TRUE, showWarnings = FALSE)
write.csv(data.frame(check = c("source_and_input_identity","candidate_hash","exact_cohort","strict_activity_inputs"),
                     status = "PASS", fingerprint = spec$fingerprint),
          run_path("results/qa/input_verification.csv"), row.names = FALSE)
cat("Selected Terra input checks passed.\n")
