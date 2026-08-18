suppressPackageStartupMessages(library(digest))

root <- normalizePath(getwd(), winslash = "/", mustWork = TRUE)
manifest_path <- file.path(root, "data", "metadata", "input_checksums.csv")
manifest <- read.csv(manifest_path, stringsAsFactors = FALSE)

rows <- lapply(seq_len(nrow(manifest)), function(index) {
  item <- manifest[index, ]
  path <- file.path(root, item$path)
  exists <- file.exists(path)
  observed_size <- if (exists) file.info(path)$size else NA_real_
  observed_hash <- if (exists) digest(path, algo = "sha256", file = TRUE, serialize = FALSE) else NA_character_
  data.frame(
    path = item$path,
    exists = exists,
    expected_size = item$size_bytes,
    observed_size = observed_size,
    expected_sha256 = item$sha256,
    observed_sha256 = observed_hash,
    status = if (exists && identical(as.numeric(observed_size), as.numeric(item$size_bytes)) &&
      identical(tolower(observed_hash), tolower(item$sha256))) "pass" else "fail",
    stringsAsFactors = FALSE
  )
})

result <- do.call(rbind, rows)
dir.create(file.path(root, "results", "qa"), recursive = TRUE, showWarnings = FALSE)
write.csv(result, file.path(root, "results", "qa", "input_verification.csv"), row.names = FALSE)
if (any(result$status != "pass")) stop("One or more frozen inputs failed checksum verification")
cat(sprintf("Verified %d frozen input files\n", nrow(result)))
