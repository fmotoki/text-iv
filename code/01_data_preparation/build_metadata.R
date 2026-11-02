# Build candidate metadata only after the selected inputs and analyses exist.
source_root <- normalizePath(getwd(), winslash = "/", mustWork = TRUE)
source(file.path(source_root, "code", "read_run_spec.R"))
suppressPackageStartupMessages({library(DBI); library(RSQLite); library(data.table); library(haven)})
metadata_dir <- run_path("data/metadata")
required <- c(spec$paths$frozen_database, spec$paths$calibration_data,
              spec$paths$sector_controls, spec$paths$availability_audit)
if (any(!file.exists(required))) stop("Candidate metadata requires completed selected inputs")
if (!identical(spec$raw_snapshot$status, "complete")) stop("The production raw snapshot is not complete")
base <- haven::read_dta(spec$paths$calibration_data)
if (anyDuplicated(base$reuniao) || !identical(sort(as.integer(base$reuniao)), 203:281)) stop("Incomplete calibration panel")
availability <- fread(spec$paths$availability_audit)
if (nrow(availability) != 237L || any(availability$availability_status != "READY")) stop("Strict activity inputs are not ready")
dir.create(metadata_dir, recursive = TRUE, showWarnings = FALSE)
connection <- dbConnect(SQLite(), spec$paths$frozen_database, flags = RSQLite::SQLITE_RO)
tables <- dbListTables(connection)
tables <- sort(tables[!startsWith(tables, "sqlite_")])
table_rows <- list()
dictionary_rows <- list()
for (index in seq_along(tables)) {
  table <- tables[[index]]
  identifier <- dbQuoteIdentifier(connection, table)
  count <- dbGetQuery(connection, paste("SELECT COUNT(*) AS n FROM", identifier))$n
  table_rows[[index]] <- data.table(table = table, rows = count, model = spec$model,
                                   fingerprint = spec$fingerprint)
  columns <- as.data.table(dbGetQuery(connection, paste0("PRAGMA table_info(", identifier, ")")))
  dictionary_rows[[index]] <- columns[, .(table = table, table_rows = count, variable = name,
                                         sqlite_type = type, not_null = as.logical(notnull),
                                         primary_key = as.logical(pk))]
}
fwrite(rbindlist(table_rows), run_path("data/metadata/database_tables.csv"))
dictionary <- rbindlist(dictionary_rows)
# The old dictionary supplies field meanings only, never old row counts or results.
legacy_dictionary <- fread(file.path(source_root, "data/metadata/database_data_dictionary.csv"),
  select = c("table", "variable", "description"))
if (anyDuplicated(legacy_dictionary[, .(table,variable)])) stop("Ambiguous dictionary template")
dictionary <- merge(dictionary, legacy_dictionary, by = c("table","variable"), all.x = TRUE, sort = FALSE)
new_meanings <- c(source_run_id = "Exact text-source run supplying the selected cell.",
  scorer_run_id = "Separate blinded-scoring run whose results define the realized dose.",
  spec_fingerprint = "Frozen specification/source/input identity.",
  request_input_hash = "SHA-256 of the complete structured model request.",
  raw_response_hash = "SHA-256 of the preserved raw model response.",
  model_revision = "Upstream embedding revision, or an explicit not-exposed status.",
  corpus_sha256 = "SHA-256 of the exact selected embedding corpus.")
for (name in names(new_meanings)) dictionary[variable == name, description := new_meanings[[name]]]
dictionary[is.na(description), description := "Field in the named selected-run table. Consult the versioned schema and raw provenance."]
fwrite(dictionary, run_path("data/metadata/database_data_dictionary.csv"))
fwrite(as.data.table(dbReadTable(connection, "runs")), run_path("data/metadata/collection_run_inventory.csv"))
fwrite(as.data.table(dbReadTable(connection, "external_source_manifest")), run_path("data/metadata/external_source_manifest.csv"))
dbDisconnect(connection)

# Labels retain Portuguese variables and distinguish model outputs from survey data.
descriptions <- c(inflacao12m = "Same-meeting-date median Focus IPCA expectation twelve months ahead, percent.",
  reuniao = "Copom meeting number, exactly 203 through 281.", date_daily = "Canonical Copom meeting date.",
  forecast_anchored = "Terra anchored original-path synthetic forecast, percent.",
  forecast_unanchored = "Terra unanchored original-path synthetic forecast, percent.",
  wc_orig = "Original-minute whitespace-token count.", score_orig = "Original lexicon tone, percentage points.",
  ibcbr_agro = "SGS29602 agriculture SA index, latest eligible actual release and verified release vintage.",
  ibcbr_industry = "SGS29604 industry SA index, latest eligible actual release and verified release vintage.",
  ibcbr_service = "SGS29606 services SA index, latest eligible actual release and verified release vintage.",
  l_inflacao12m = "Previous-meeting Focus expectation, computed before evaluation selection.")
labels <- unname(descriptions[names(base)])
labels[is.na(labels)] <- "Field in the selected model-neutral calibration dataset."
# vapply's callback selects the first descriptive class when haven attaches several.
fwrite(data.table(file = spec$paths$calibration_data, rows = nrow(base), variable = names(base),
                  storage_class = vapply(base, function(x) class(x)[[1L]], character(1)),
                  description = labels), run_path("data/metadata/analysis_data_dictionary.csv"))

# The legacy exhibit list is a format template, never numerical Terra evidence.
exhibits <- fread(file.path(source_root, "data/metadata/exhibit_manifest.csv"))
new_exhibits <- data.table(
  exhibit_id = c("T_KNOWLEDGE_AGREEMENT", "T_KNOWLEDGE_FRAMING", "F_KNOWLEDGE_AGREEMENT", "F_KNOWLEDGE_FRAMING"),
  type = c("table", "table", "figure", "figure"),
  output_path = c("results/tables/rr_knowledge_cutoff_agreement.tex", "results/tables/rr_knowledge_cutoff_framing.tex",
                  "results/figures/rr_knowledge_cutoff_agreement.pdf", "results/figures/rr_knowledge_cutoff_framing.pdf"),
  producer = c(rep("code/03_reporting/build_referee_integration_tables.R", 2), rep("code/03_reporting/build_baseline_figures.R", 2)),
  description = c("Frozen69 Focus agreement", "Equal-meeting common-prior framing", "Focus agreement and meeting errors", "Meeting framing contrasts and five-meeting means"))
new_exhibits[, reference_path := sub("^results/", "reference/", output_path)]
exhibits <- rbindlist(list(exhibits, new_exhibits), use.names = TRUE)
if (anyDuplicated(exhibits$exhibit_id) || anyDuplicated(exhibits$output_path)) stop("Duplicate exhibit identity")
fwrite(exhibits, run_path("data/metadata/exhibit_manifest.csv"))

# Existing non-BCB flags remain immutable references with their original limited support.
sources <- data.table(
  component = c("Original minutes", "BCB supporting data", "Sector activity controls", "Text rewrites", "Synthetic forecasts", "Fiscal sensitivity flags"),
  source = c(spec$sample$dates_csv, spec$raw_snapshot$path, spec$paths$sector_controls,
             spec$paths$frozen_database, spec$paths$frozen_database, spec$legacy_reference_inputs$fiscal_flags),
  model = c(NA_character_, NA_character_, NA_character_, spec$model, spec$model, NA_character_),
  support = c("203:281", "Selected immutable snapshot manifest", "79 meetings, three SA sectors",
              "Main25 and dose5 regenerations", "Both profiles, common and recursive designs", "Documented historical subset only"),
  policy = c("Meeting date, not presumed document exposure", "Official SGS/Olinda and actual disclosure history",
             spec$activity_controls$policy, "Unchanged rewrite prompts", "Model-based outcomes", "No fabricated extension"))
fwrite(sources, run_path("data/metadata/data_sources.csv"))
input_paths <- unique(c(spec$paths$frozen_database, spec$paths$sector_controls,
  spec$paths$availability_audit, spec$legacy_reference_inputs$fiscal_flags,
  spec$sample$dates_csv, list.files(file.path(source_root, "data/raw/copom_minutes"), full.names = TRUE),
  list.files(spec$raw_snapshot$path, recursive = TRUE, full.names = TRUE)))
input_paths <- input_paths[!dir.exists(input_paths)]
if (any(!file.exists(input_paths))) stop("Missing recorded input")
input_rows <- data.table(path = input_paths, size_bytes = file.info(input_paths)$size,
  sha256 = vapply(input_paths, digest::digest, character(1), algo = "sha256", file = TRUE, serialize = FALSE))
fwrite(input_rows, run_path("data/metadata/input_checksums.csv"))
references <- list.files(spec$paths$reference, recursive = TRUE, full.names = TRUE)
references <- references[!dir.exists(references)]
reference_rows <- data.table(path = references, size_bytes = file.info(references)$size,
  sha256 = vapply(references, digest::digest, character(1), algo = "sha256", file = TRUE, serialize = FALSE))
fwrite(reference_rows, run_path("data/metadata/reference_checksums.csv"))
jsonlite::write_json(list(model = spec$model, reasoning_effort = spec$reasoning_effort, service_tier = spec$service_tier,
  fingerprint = spec$fingerprint, specification_sha256 = spec_sha256, release_id = spec$release_id,
  raw_snapshot = spec$raw_snapshot, activity_controls = spec$activity_controls,
  training = 203:271, evaluation_pre = 272:276, evaluation_post = 277:281,
  exhibits = as.list(table(exhibits$type)), reference_accepted = isTRUE(spec$execution$reference_accepted),
  generated_at_utc = format(Sys.time(), tz = "UTC", usetz = TRUE)),
  run_path("data/metadata/release_metadata.json"), auto_unbox = TRUE, pretty = TRUE)
cat(sprintf("Candidate metadata written. %d tables and %d figures.\n", sum(exhibits$type == "table"), sum(exhibits$type == "figure")))
