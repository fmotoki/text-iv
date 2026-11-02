# Every active R stage reads one specification before opening artifact files.
# Base R supplies canonical paths but no project-specific write authority.
# This narrow guard adds the selected run boundary to normalizePath.
spec_path <- Sys.getenv("REPLICATION_SPEC_PATH", unset = "")
if (!nzchar(spec_path) || !file.exists(spec_path)) {
  stop("Set REPLICATION_SPEC_PATH to an existing explicit production or QA specification")
}
spec_path <- normalizePath(spec_path, winslash = "/", mustWork = TRUE)
spec <- jsonlite::fromJSON(spec_path, simplifyVector = TRUE)
required_fields <- c("source_root", "run_root", "release_id", "design_type", "model",
                     "reasoning_effort", "sample", "run_ids", "paths", "fingerprint")
if (!all(required_fields %in% names(spec))) stop("Incomplete replication specification")
source_root <- normalizePath(spec$source_root, winslash = "/", mustWork = TRUE)
if (!file.exists(file.path(source_root, "code", "replication_spec.json"))) {
  stop("The specification source_root is not the source package")
}
if (!spec$design_type %in% c("production", "qa_fixture", "qa_smoke")) {
  stop("Unknown design_type")
}
if (!identical(spec$model, "gpt-5.6-terra") || !identical(spec$reasoning_effort, "high")) {
  stop("This cohort requires exactly gpt-5.6-terra at high reasoning")
}
if (!identical(as.character(spec$cutoff), "2026-02-16")) stop("Unexpected knowledge cutoff")
if (anyDuplicated(spec$run_ids) || any(!startsWith(unlist(spec$run_ids), "terra56_"))) {
  stop("Run identities must be unique and Terra-prefixed")
}
meetings <- as.integer(spec$sample$meeting_numbers)
if (!length(meetings) || anyDuplicated(meetings) || is.unsorted(meetings, strictly = TRUE)) {
  stop("Invalid meeting selection")
}
if (identical(spec$design_type, "production")) {
  if (!identical(meetings, 203:281) || spec$draws$main != 25L || spec$draws$dose != 5L) {
    stop("Production requires meetings 203:281, 25 main draws and five dose draws")
  }
  if (!identical(as.integer(spec$evaluation$pre), 272:276) ||
      !identical(as.integer(spec$evaluation$post), 277:281) ||
      spec$evaluation$training_first != 203L || spec$evaluation$training_last != 271L) {
    stop("The frozen calibration and evaluation memberships changed")
  }
}
if (!identical(as.character(spec$profiles), c("anchored", "unanchored")) ||
    spec$blocks != 5L || spec$initial_expectation != 3.8) {
  stop("The profiles, five-block design or initial prior changed")
}
if (!identical(spec$activity_controls$alignment, "latest_published_as_of_meeting") ||
    !identical(as.integer(spec$activity_controls$sector_codes), c(29602L, 29604L, 29606L))) {
  stop("Activity-control specification changed")
}

root_input <- gsub("\\\\", "/", spec$run_root)
if (!grepl("^[A-Za-z]:/|^/", root_input) || grepl("(^|/)\\.\\.(/|$)", root_input)) {
  stop("run_root must be absolute without parent traversal")
}
root <- normalizePath(root_input, winslash = "/", mustWork = TRUE)
if (identical(tolower(root), tolower(source_root))) stop("run_root cannot equal source_root")
if (identical(spec$design_type, "production")) {
  if (is.null(spec$artifact_root) || !dir.exists(spec$artifact_root)) stop("Missing artifact_root")
  artifact_root <- normalizePath(spec$artifact_root, winslash = "/", mustWork = TRUE)
  if (!identical(dirname(artifact_root), dirname(source_root)) ||
      identical(tolower(artifact_root), tolower(source_root))) {
    stop("artifact_root must be a separate sibling of the code repository")
  }
  expected_root <- paste0(artifact_root, "/runs/terra56/", spec$release_id)
  if (!identical(tolower(root), tolower(expected_root))) {
    stop("Production run_root must be ARTIFACT_ROOT/runs/terra56/release_id")
  }
} else {
  # QA fixtures are separately owned system-TEMP directories, never result trees.
  temporary_root <- normalizePath(Sys.getenv("TEMP", unset = tempdir()), winslash = "/", mustWork = TRUE)
  if (!startsWith(tolower(root), paste0(tolower(temporary_root), "/"))) {
    stop("QA run_root must be a separately owned system-TEMP directory")
  }
}

run_path <- function(relative_path) {
  # No standard path command knows the selected run's protected destinations.
  candidate <- gsub("\\\\", "/", as.character(relative_path))
  if (length(candidate) != 1L || is.na(candidate) || !nzchar(candidate) ||
      grepl("(^|/)\\.\\.(/|$)", candidate)) stop("Invalid artifact path")
  if (!grepl("^[A-Za-z]:/|^/", candidate)) candidate <- file.path(root, candidate)
  ancestor <- candidate
  while (!file.exists(ancestor) && !dir.exists(ancestor)) {
    parent <- dirname(ancestor)
    if (identical(parent, ancestor)) stop("Artifact path has no existing ancestor")
    ancestor <- parent
  }
  resolved <- normalizePath(ancestor, winslash = "/", mustWork = TRUE)
  suffix <- substring(candidate, nchar(ancestor) + 1L)
  target <- paste0(resolved, suffix)
  root_prefix <- paste0(tolower(root), "/")
  if (!startsWith(tolower(target), root_prefix) || identical(tolower(target), tolower(root))) {
    stop("Artifact path escapes the selected run: ", candidate)
  }
  if (nzchar(Sys.readlink(ancestor)) && !is.na(Sys.readlink(ancestor))) {
    stop("Artifact path uses a symbolic link: ", candidate)
  }
  target
}

# A copied effective specification identifies this run before any artifact write.
effective_spec_path <- run_path("effective_spec.json")
if (!file.exists(effective_spec_path) || !identical(
  spec, jsonlite::fromJSON(effective_spec_path, simplifyVector = TRUE)
)) stop("Missing or changed effective specification. Start a new matching run.")

# Resolve every configured artifact path before any writer or cleanup can run.
for (name in names(spec$paths)) spec$paths[[name]] <- run_path(spec$paths[[name]])
existing_artifacts <- list.files(root, recursive = TRUE, full.names = TRUE,
                                 all.files = TRUE, include.dirs = TRUE, no.. = TRUE)
for (item in existing_artifacts) invisible(run_path(item))
spec$run_root <- root
result_stamp <- as.character(spec$result_stamp)
if (length(result_stamp) != 1L || !nzchar(result_stamp) || grepl("[/\\\\]", result_stamp)) {
  stop("Invalid result stamp")
}
if (!is.character(spec$fingerprint) || length(spec$fingerprint) != 1L ||
    !grepl("^[0-9a-f]{64}$", spec$fingerprint)) {
  stop("The effective specification needs a final source/input fingerprint before execution")
}
spec_sha256 <- digest::digest(spec_path, algo = "sha256", file = TRUE, serialize = FALSE)

# Resume is valid only for the same source and immutable input bytes.
for (field in c("source_hashes", "input_hashes")) {
  hashes <- spec[[field]]
  if (!is.list(hashes) || !length(hashes) || is.null(names(hashes)) ||
      length(names(hashes)) != length(hashes) || anyNA(names(hashes)) ||
      any(!nzchar(names(hashes))) || anyDuplicated(names(hashes)) ||
      any(!vapply(hashes, function(value) is.character(value) && length(value) == 1L &&
                  !is.na(value) && grepl("^[0-9a-f]{64}$", value), logical(1)))) {
    stop(field, " must be a named object of SHA-256 strings")
  }
}
if (is.null(spec$source_hashes) || !length(spec$source_hashes)) stop("Missing source freeze")
for (item in names(spec$source_hashes)) {
  if (grepl("^[A-Za-z]:[/\\\\]|^/", item) || grepl("(^|[/\\\\])\\.\\.([/\\\\]|$)", item)) {
    stop("source_hashes keys must be relative paths inside SOURCE_ROOT")
  }
  file <- file.path(source_root, item)
  if (file.exists(file) && !startsWith(tolower(normalizePath(file, winslash = "/", mustWork = TRUE)),
                                     paste0(tolower(source_root), "/"))) {
    stop("Source hash path escapes SOURCE_ROOT: ", item)
  }
  if (!file.exists(file) || !identical(
    digest::digest(file, algo = "sha256", file = TRUE, serialize = FALSE),
    as.character(spec$source_hashes[[item]])
  )) stop("Source fingerprint mismatch: ", item)
}
if (is.null(spec$input_hashes) || !length(spec$input_hashes)) stop("Missing input freeze")
for (file in names(spec$input_hashes)) {
  if (!grepl("^[A-Za-z]:[/\\\\]|^/", file) || grepl("(^|[/\\\\])\\.\\.([/\\\\]|$)", file)) {
    stop("input_hashes keys must be absolute immutable-input paths")
  }
  if (!file.exists(file) || !identical(
    digest::digest(file, algo = "sha256", file = TRUE, serialize = FALSE),
    as.character(spec$input_hashes[[file]])
  )) stop("Input fingerprint mismatch: ", file)
}
