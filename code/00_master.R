script_arg <- grep("^--file=", commandArgs(trailingOnly = FALSE), value = TRUE)
script_path <- normalizePath(sub("^--file=", "", script_arg[[1L]]), winslash = "/")
root <- normalizePath(file.path(dirname(script_path), ".."), winslash = "/")
setwd(root)

parse_args <- function() {
  values <- list(resume = FALSE)
  for (arg in commandArgs(trailingOnly = TRUE)) {
    if (grepl("^--resume=", arg)) {
      values$resume <- tolower(sub("^--resume=", "", arg)) %in% c("1", "true", "yes")
    }
  }
  values
}

args <- parse_args()
required_markers <- c("README.md", "renv.lock", "data", "code", "reference")
if (!all(file.exists(file.path(root, required_markers)))) {
  stop("The master script is not running from a complete replication package")
}

safe_unlink <- function(relative_path) {
  target <- normalizePath(file.path(root, relative_path), winslash = "/", mustWork = FALSE)
  root_prefix <- paste0(root, "/")
  if (!startsWith(target, root_prefix) || identical(target, root)) {
    stop("Refusing to clean a path outside the replication package: ", target)
  }
  if (file.exists(target) || dir.exists(target)) unlink(target, recursive = TRUE, force = TRUE)
}

if (!args$resume) {
  for (path in c(
    "outputs/tables", "outputs/models", "outputs/work",
    "results/tables", "results/figures", "results/qa",
    "results/verification_report.csv", "results/intext_results.csv",
    "results/intext_numbers.tex", "logs/steps",
    "logs/master_run_times.csv", "logs/master_sessionInfo.txt",
    "logs/package_versions.csv"
  )) safe_unlink(path)
}

for (path in c(
  "outputs/tables", "outputs/models", "outputs/work",
  "results/tables", "results/figures", "results/qa",
  "logs/steps"
)) dir.create(file.path(root, path), recursive = TRUE, showWarnings = FALSE)

rscript <- file.path(R.home("bin"), if (.Platform$OS.type == "windows") "Rscript.exe" else "Rscript")
if (!file.exists(rscript)) stop("Rscript executable not found: ", rscript)
Sys.setenv(MAKEFLAGS = "-j1", STAN_NUM_THREADS = "1")

run_step <- function(number, name, script, extra_args = character()) {
  marker <- file.path(root, "logs", "steps", sprintf("%02d_%s.done", number, name))
  log_path <- file.path(root, "logs", "steps", sprintf("%02d_%s.log", number, name))
  if (args$resume && file.exists(marker)) {
    cat(sprintf("[%02d] %s: already complete\n", number, name))
    return(data.frame(step = number, name = name, status = "resumed", seconds = 0))
  }
  cat(sprintf("[%02d] %s\n", number, name))
  started <- Sys.time()
  status <- system2(
    rscript,
    args = c(script, extra_args),
    stdout = log_path,
    stderr = log_path
  )
  elapsed <- as.numeric(difftime(Sys.time(), started, units = "secs"))
  if (!identical(status, 0L)) {
    stop(sprintf("Step %02d (%s) failed. See %s", number, name, log_path))
  }
  writeLines(format(Sys.time(), tz = "UTC", usetz = TRUE), marker)
  data.frame(step = number, name = name, status = "complete", seconds = elapsed)
}

timings <- list()
timings[[1L]] <- run_step(1, "verify_inputs", "code/04_verification/verify_inputs.R")

source_database <- file.path(root, "data", "generated", "prompt_iv_replication_source.sqlite")
work_database <- file.path(root, "outputs", "work", "prompt_iv_replication.sqlite")
if (!args$resume || !file.exists(work_database)) {
  if (!file.copy(source_database, work_database, overwrite = TRUE, copy.mode = TRUE)) {
    stop("Unable to create the working database")
  }
}

steps <- list(
  c("build_gpt55_calibration_input", "code/01_data_preparation/build_gpt55_calibration_data.R"),
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
  c("verify_results", "code/04_verification/verify_results.R")
)

for (index in seq_along(steps)) {
  timings[[index + 1L]] <- run_step(index + 1L, steps[[index]][[1L]], steps[[index]][[2L]])
}

timing_table <- do.call(rbind, timings)
write.csv(timing_table, file.path(root, "logs", "master_run_times.csv"), row.names = FALSE)
writeLines(capture.output(sessionInfo()), file.path(root, "logs", "master_sessionInfo.txt"))
lock <- jsonlite::fromJSON(file.path(root, "renv.lock"), simplifyVector = FALSE)
package_versions <- data.frame(
  package = names(lock$Packages),
  version = vapply(lock$Packages, `[[`, character(1L), "Version"),
  source = vapply(lock$Packages, `[[`, character(1L), "Source"),
  stringsAsFactors = FALSE
)
write.csv(
  package_versions[order(package_versions$package), ],
  file.path(root, "logs", "package_versions.csv"),
  row.names = FALSE
)
cat(sprintf(
  "Replication complete: %.1f minutes\n",
  sum(timing_table$seconds) / 60
))
