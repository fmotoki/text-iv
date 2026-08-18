script_arg <- grep("^--file=", commandArgs(trailingOnly = FALSE), value = TRUE)
script_path <- normalizePath(sub("^--file=", "", script_arg[[1L]]), winslash = "/")
root <- normalizePath(file.path(dirname(script_path), ".."), winslash = "/")
setwd(root)

options(
  repos = c(CRAN = "https://cloud.r-project.org"),
  renv.consent = TRUE
)

if (!requireNamespace("renv", quietly = TRUE)) {
  install.packages("renv")
}

renv::consent(provided = TRUE)
# A vanilla Rscript invocation does not source the project .Rprofile.
# Load the project explicitly so restore targets the package-local library
# rather than the user's global library.
renv::load(project = root)
# clusterSEs 2.6.5 declares several packages under Depends. Some renv versions
# schedule it before those packages during a transactional parallel restore.
# Restore the dependency graph first, then install clusterSEs from the lockfile.
renv::restore(
  project = root,
  lockfile = file.path(root, "renv.lock"),
  exclude = "clusterSEs",
  prompt = FALSE
)
clusterSEs_version <- tryCatch(
  as.character(utils::packageVersion("clusterSEs", lib.loc = .libPaths()[[1L]])),
  error = function(error) NA_character_
)
if (!identical(clusterSEs_version, "2.6.5")) {
  renv::restore(
    project = root,
    lockfile = file.path(root, "renv.lock"),
    packages = "clusterSEs",
    prompt = FALSE
  )
}

# A machine-wide renv cache can contain a Windows rstan binary built against a
# different R/Rcpp patch level even when its package version matches the
# lockfile.  Detect that case by loading the namespace.  Only on failure,
# rebuild the exact locked Stan stack from CRAN sources in the project library.
rstan_loads <- function() {
  library <- normalizePath(.libPaths()[[1L]], winslash = "/", mustWork = TRUE)
  probe_script <- tempfile("probe-rstan-", fileext = ".R")
  probe_log <- tempfile("probe-rstan-", fileext = ".log")
  on.exit(unlink(c(probe_script, probe_log), force = TRUE), add = TRUE)
  writeLines(
    c(
      sprintf(".libPaths(c(%s, .libPaths()))", dQuote(library)),
      "suppressPackageStartupMessages(loadNamespace('rstan'))"
    ),
    probe_script
  )

  status <- tryCatch(
    system2(
      file.path(R.home("bin"), "Rscript"),
      args = c("--vanilla", shQuote(probe_script)),
      stdout = probe_log,
      stderr = probe_log
    ),
    error = function(error) 1L
  )
  if (identical(status, 0L)) {
    return(TRUE)
  }

  details <- if (file.exists(probe_log)) readLines(probe_log, warn = FALSE) else character()
  message(
    "The restored rstan binary could not be loaded in a clean subprocess.",
    if (length(details)) paste0("\n", paste(details, collapse = "\n")) else ""
  )
  FALSE
}

download_locked_source <- function(package, version) {
  cran <- sub("/+$", "", unname(getOption("repos")[["CRAN"]]))
  filename <- sprintf("%s_%s.tar.gz", package, version)
  urls <- c(
    sprintf("%s/src/contrib/%s", cran, filename),
    sprintf("%s/src/contrib/Archive/%s/%s", cran, package, filename)
  )
  destination <- file.path(tempdir(), filename)

  for (url in urls) {
    unlink(destination, force = TRUE)
    status <- try(
      utils::download.file(url, destination, mode = "wb", quiet = FALSE),
      silent = TRUE
    )
    if (
      !inherits(status, "try-error") &&
        identical(status, 0L) &&
        file.exists(destination) &&
        isTRUE(file.info(destination)$size > 0)
    ) {
      return(destination)
    }
  }

  stop("Could not download the locked source package ", package, " ", version)
}

if (!rstan_loads()) {
  locked_stan <- c(
    RcppParallel = "5.1.11-2",
    StanHeaders = "2.32.10",
    rstan = "2.32.7"
  )
  source_packages <- Map(download_locked_source, names(locked_stan), locked_stan)
  source_packages <- unname(unlist(source_packages, use.names = FALSE))
  tryCatch(
    utils::install.packages(
      source_packages,
      repos = NULL,
      type = "source",
      lib = .libPaths()[[1L]]
    ),
    finally = unlink(source_packages, force = TRUE)
  )

  if (!rstan_loads()) {
    stop("rstan still cannot be loaded after rebuilding the locked Stan stack")
  }
  message("Rebuilt the locked Stan stack from source after an incompatible cached binary.")
}

dir.create(file.path(root, "logs"), recursive = TRUE, showWarnings = FALSE)
writeLines(capture.output(sessionInfo()), file.path(root, "logs", "setup_sessionInfo.txt"))
cat("R environment restored from renv.lock\n")
