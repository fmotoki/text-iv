# The master is offline. Collection and reference acceptance are separate.
script_arg <- grep("^--file=", commandArgs(trailingOnly = FALSE), value = TRUE)
script_path <- normalizePath(sub("^--file=", "", script_arg[[1L]]), winslash = "/")
source_root <- normalizePath(file.path(dirname(script_path), ".."), winslash = "/")
setwd(source_root)
source(file.path(source_root, "code", "read_run_spec.R"))

arguments <- commandArgs(trailingOnly = TRUE)
if (any(!arguments %in% c("--resume=true", "--resume=false"))) stop("Unknown master argument")
resume <- "--resume=true" %in% arguments
if (!isTRUE(spec$execution$full_production_authorized) && spec$design_type == "production") {
  stop("Full production execution is not authorized in this specification")
}
source_database <- spec$paths$frozen_database
work_database <- spec$paths$work_database
if (!file.exists(source_database)) stop("Missing candidate source database: ", source_database)

# Verify the complete cohort before any cleanup or replacement.
rscript <- file.path(R.home("bin"), if (.Platform$OS.type == "windows") "Rscript.exe" else "Rscript")
if (!file.exists(rscript)) stop("Rscript executable not found")
Sys.setenv(MAKEFLAGS = "-j1", STAN_NUM_THREADS = "1")
status <- system2(rscript, shQuote(file.path(source_root, "code/04_verification/verify_inputs.R")))
if (!identical(status, 0L)) stop("Input verification failed before master cleanup")
source_hash <- digest::digest(source_database, algo = "sha256", file = TRUE, serialize = FALSE)
work_identity <- run_path("outputs/work/input_identity.json")
if (resume && file.exists(work_database)) {
  if (!file.exists(work_identity)) stop("Work database lacks a source identity record")
  identity <- jsonlite::fromJSON(work_identity)
  if (!identical(identity$fingerprint, spec$fingerprint) ||
      !identical(identity$spec_sha256, spec_sha256) ||
      !identical(identity$source_sha256, source_hash)) stop("Stale work database")
}
if (!resume) {
  # These exact directories belong exclusively to this selected run.
  for (relative in c("outputs/tables", "outputs/models", "outputs/work",
                     "results/tables", "results/figures", "results/qa", "logs/steps")) {
    target <- run_path(relative)
    if (file.exists(target) || dir.exists(target)) unlink(target, recursive = TRUE, force = TRUE)
  }
}
for (relative in c("outputs/tables", "outputs/models", "outputs/work",
                   "results/tables", "results/figures", "results/qa", "logs/steps")) {
  dir.create(run_path(relative), recursive = TRUE, showWarnings = FALSE)
}
if (!resume || !file.exists(work_database)) {
  if (!file.copy(source_database, run_path(work_database), overwrite = FALSE)) stop("Work database copy failed")
  jsonlite::write_json(list(fingerprint = spec$fingerprint, spec_sha256 = spec_sha256, source_sha256 = source_hash),
                       work_identity, auto_unbox = TRUE, pretty = TRUE)
}

steps <- list(
  c("calibration", "code/01_data_preparation/build_calibration_data.R"),
  c("knowledge_cutoff", "code/02_analysis/analyze_knowledge_cutoff.R"),
  c("common_prior", "code/02_analysis/analyze_common_prior_full.R"),
  c("oos_validation", "code/02_analysis/run_referee_d8_oos_validation.R"),
  c("arm_specific_prior", "code/02_analysis/analyze_arm_specific_prior_dose.R"),
  c("identification_calibration", "code/02_analysis/run_referee_identification_calibration.R"),
  c("bayesian_calibration", "code/02_analysis/run_referee_d6_bayesian_calibration.R"),
  c("external_validation", "code/02_analysis/run_referee_external_validation.R"),
  c("sunspot", "code/02_analysis/run_referee_d10_sunspot.R"),
  c("posterior_tournament", "code/02_analysis/run_referee_d11_posterior_tournament.R"),
  c("topic_preservation", "code/02_analysis/run_referee_d15_topic_preservation.R"),
  c("embedding_tone", "code/02_analysis/run_referee_d16_embedding_tone.R"),
  c("integration_tables", "code/03_reporting/build_referee_integration_tables.R"),
  c("baseline_figures", "code/03_reporting/build_baseline_figures.R"),
  c("metadata", "code/01_data_preparation/build_metadata.R"),
  c("verify_results", "code/04_verification/verify_results.R")
)
timings <- list()
for (index in seq_along(steps)) {
  name <- steps[[index]][[1L]]
  marker <- run_path(file.path("logs/steps", paste0(name, ".done.json")))
  log <- run_path(file.path("logs/steps", paste0(name, ".log")))
  if (resume && file.exists(marker)) {
    identity <- jsonlite::fromJSON(marker)
    if (!identical(identity$fingerprint, spec$fingerprint) ||
        !identical(identity$spec_sha256, spec_sha256) ||
        !identical(identity$source_sha256, source_hash)) stop("Stale stage marker: ", marker)
    timings[[index]] <- data.frame(step = name, status = "resumed", seconds = 0)
    next
  }
  started <- Sys.time()
  status <- system2(rscript, shQuote(file.path(source_root, steps[[index]][[2L]])), stdout = log, stderr = log)
  if (!identical(status, 0L)) stop("Stage failed: ", name, ". See ", log)
  jsonlite::write_json(list(fingerprint = spec$fingerprint, spec_sha256 = spec_sha256, source_sha256 = source_hash,
                           completed_at_utc = format(Sys.time(), tz = "UTC", usetz = TRUE)),
                       marker, auto_unbox = TRUE, pretty = TRUE)
  timings[[index]] <- data.frame(step = name, status = "complete",
                                seconds = as.numeric(difftime(Sys.time(), started, units = "secs")))
}
write.csv(do.call(rbind, timings), run_path("logs/master_run_times.csv"), row.names = FALSE)
writeLines(capture.output(sessionInfo()), run_path("logs/master_sessionInfo.txt"))
