suppressPackageStartupMessages({
  library(data.table)
  library(digest)
})

script_arg <- grep("^--file=", commandArgs(trailingOnly = FALSE), value = TRUE)
script_path <- normalizePath(sub("^--file=", "", script_arg[[1L]]), winslash = "/")
source_root <- normalizePath(file.path(dirname(script_path), "..", ".."), winslash = "/")
setwd(source_root)
source(file.path(source_root, "code", "read_run_spec.R"))

excluded_roots <- c(
  ".git",
  "archives",
  "runs",
  "renv/library",
  "renv/library_broken_20260816",
  "renv/staging",
  "renv/local",
  "renv/cellar",
  "renv/lock",
  "renv/python",
  "renv/sandbox",
  "outputs/work",
  "outputs/models",
  "logs/steps",
  "tmp_cluster_lib"
)

excluded_files <- c("README_test.pdf")

# Prune expendable directories before recursion. Enumerating the complete
# project library and filtering it afterward can take several minutes once
# 00_setup.R has populated renv/library, even though those files are never
# released or included in the manifest.
collect_release_files <- function(directory) {
  entries <- list.files(
    directory,
    recursive = FALSE,
    full.names = TRUE,
    all.files = TRUE,
    no.. = TRUE
  )
  if (!length(entries)) return(character())

  relative_entries <- substring(gsub("\\\\", "/", entries), nchar(root) + 2L)
  excluded <- vapply(relative_entries, function(path) {
    any(path == excluded_roots | startsWith(path, paste0(excluded_roots, "/")))
  }, logical(1L))
  entries <- entries[!excluded]
  if (!length(entries)) return(character())

  info <- file.info(entries)
  is_directory <- !is.na(info$isdir) & info$isdir
  c(
    entries[!is_directory],
    unlist(lapply(entries[is_directory], collect_release_files), use.names = FALSE)
  )
}

paths <- collect_release_files(root)
relative <- substring(gsub("\\\\", "/", paths), nchar(root) + 2L)
keep <- relative != "data/metadata/package_file_manifest.csv" &
  !(relative %in% excluded_files) &
  !grepl("(^|/)__pycache__(/|$)|\\.py[co]$", relative)
paths <- paths[keep]
relative <- relative[keep]

manifest <- rbindlist(lapply(seq_along(paths), function(index) {
  data.table(
    path = relative[[index]],
    size_bytes = file.info(paths[[index]])$size,
    sha256 = digest(paths[[index]], algo = "sha256", file = TRUE, serialize = FALSE),
    release_id = spec$release_id,
    model = spec$model,
    reasoning_effort = spec$reasoning_effort,
    fingerprint = spec$fingerprint,
    specification_sha256 = spec_sha256,
    raw_snapshot_manifest_sha256 = if (is.null(spec$raw_snapshot$manifest_sha256)) NA_character_ else spec$raw_snapshot$manifest_sha256
  )
}))
setorder(manifest, path)
fwrite(manifest, run_path("data/metadata/package_file_manifest.csv"))
cat(sprintf("Package manifest written for %d files\n", nrow(manifest)))
