sanitize_release_metadata <- function(value, root) {
  normalized_root <- gsub(
    "\\\\",
    "/",
    normalizePath(root, winslash = "/", mustWork = TRUE)
  )

  if (is.data.frame(value)) {
    for (name in names(value)) {
      value[[name]] <- sanitize_release_metadata(value[[name]], normalized_root)
    }
    return(value)
  }

  if (is.list(value)) {
    return(lapply(value, sanitize_release_metadata, root = normalized_root))
  }

  if (is.character(value)) {
    value[] <- vapply(value, function(item) {
      if (is.na(item)) return(NA_character_)
      normalized <- gsub("\\\\", "/", item)
      if (!grepl("^[A-Za-z]:/|^/", normalized)) return(item)
      if (identical(normalized, normalized_root)) return(".")
      prefix <- paste0(normalized_root, "/")
      if (startsWith(normalized, prefix)) {
        return(substring(normalized, nchar(prefix) + 1L))
      }
      normalized
    }, character(1L))
  }

  value
}

release_metadata <- function(payload, root, analysis_version) {
  if (!is.list(payload) || is.data.frame(payload)) {
    stop("Release metadata payload must be a named list")
  }
  # Retain actual execution time separately from the prospective result stamp.
  payload$generated_at_utc <- format(Sys.time(), tz = "UTC", usetz = TRUE)
  payload$model <- spec$model
  payload$reasoning_effort <- spec$reasoning_effort
  payload$release_id <- spec$release_id
  payload$fingerprint <- spec$fingerprint
  payload$spec_sha256 <- spec_sha256
  payload$raw_snapshot <- spec$raw_snapshot
  payload$run_ids <- spec$run_ids
  payload$meeting_numbers <- spec$sample$meeting_numbers
  payload$evaluation <- spec$evaluation
  payload$generated_at <- NULL
  payload$analysis_version <- as.character(analysis_version)
  sanitize_release_metadata(payload, root)
}
