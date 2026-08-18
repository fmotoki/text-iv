suppressPackageStartupMessages({
  library(DBI)
  library(RSQLite)
  library(data.table)
  library(jsonlite)
})

root <- normalizePath(".", winslash = "/", mustWork = TRUE)
run_id <- "v10_referee_step3_neutral_dose_5draws_20260627"
validation_dir <- file.path(root, "data", "interim", "dose_response", run_id)
database_path <- file.path(root, "data", "interim", "prompt_iv.sqlite")
migrated_at <- format(Sys.time(), tz = "UTC", usetz = TRUE)

read_jsonl <- function(path) {
  connection <- file(path, open = "rb")
  on.exit(close(connection), add = TRUE)
  stream_in(connection, verbose = FALSE, simplifyDataFrame = TRUE)
}

realized <- fread(
  file.path(validation_dir, "realized_dose_dataset.csv"),
  encoding = "UTF-8",
  showProgress = FALSE
)
semantic_manifest <- read_jsonl(file.path(validation_dir, "semantic_tone_manifest.jsonl"))
semantic_results <- read_jsonl(file.path(validation_dir, "semantic_tone_results.jsonl"))
pairwise_manifest <- read_jsonl(file.path(validation_dir, "pairwise_tone_manifest.jsonl"))
pairwise_results <- read_jsonl(file.path(validation_dir, "pairwise_tone_results.jsonl"))

if (nrow(realized) != 2130L || nrow(semantic_results) != 2130L ||
    nrow(pairwise_results) != 1775L) {
  stop("The blinded validation artifacts are incomplete")
}

semantic_tasks <- as.data.table(semantic_manifest)[, .(
  run_id = run_id,
  task_id,
  anonymous_id,
  meeting_number = as.integer(meeting_number),
  iteration = as.integer(iteration),
  text_role = as.character(text_role),
  assigned_dose = as.numeric(assigned_dose),
  text_hash,
  prompt_version,
  migrated_at
)]

semantic_scores <- as.data.table(semantic_results)[, .(
  run_id = run_id,
  task_id,
  status,
  meeting_number = as.integer(meeting_number),
  iteration = as.integer(iteration),
  text_role = as.character(text_role),
  score = as.numeric(stance_score),
  label = as.character(label),
  confidence = as.numeric(confidence),
  model,
  reasoning_effort,
  prompt_version,
  request_input_hash,
  raw_response_hash,
  created_at,
  migrated_at
)]

if (anyNA(semantic_scores$score) || sd(semantic_scores$score) == 0 ||
    any(abs(semantic_scores$score) > 1)) {
  stop("Semantic stance scores are missing, degenerate, or outside [-1, 1]")
}
semantic_check <- merge(
  semantic_scores[, .(meeting_number, iteration, text_role, score)],
  as.data.table(realized)[, .(
    meeting_number,
    iteration,
    text_role,
    realized_semantic_score = semantic_score
  )],
  by = c("meeting_number", "iteration", "text_role")
)
if (nrow(semantic_check) != 2130L ||
    max(abs(semantic_check$score - semantic_check$realized_semantic_score)) > 1e-12) {
  stop("Semantic scores disagree with the realized-dose dataset")
}

pairwise_tasks <- as.data.table(pairwise_manifest)[, .(
  run_id = run_id,
  task_id,
  anonymous_pair_id,
  meeting_number = as.integer(meeting_number),
  iteration = as.integer(iteration),
  pair_type,
  role_a,
  role_b,
  text_a_hash,
  text_b_hash,
  prompt_version,
  migrated_at
)]

pairwise_scores <- as.data.table(pairwise_results)[, .(
  run_id = run_id,
  task_id,
  status,
  meeting_number = as.integer(meeting_number),
  iteration = as.integer(iteration),
  pair_type,
  role_a,
  role_b,
  more_hawkish,
  more_hawkish_role,
  confidence = as.numeric(confidence),
  model,
  reasoning_effort,
  prompt_version,
  request_input_hash,
  raw_response_hash,
  created_at,
  migrated_at
)]

realized_db <- copy(realized)
realized_db[, `:=`(run_id = run_id, migrated_at = migrated_at)]
setcolorder(realized_db, c("run_id", setdiff(names(realized_db), "run_id")))

con <- dbConnect(SQLite(), database_path)
on.exit(dbDisconnect(con), add = TRUE)
dbExecute(con, "PRAGMA busy_timeout = 60000")

tables <- list(
  blinded_semantic_tasks = semantic_tasks,
  blinded_semantic_scores = semantic_scores,
  blinded_pairwise_tasks = pairwise_tasks,
  blinded_pairwise_scores = pairwise_scores,
  realized_dose_scores = realized_db
)

dbWithTransaction(con, {
  for (table_name in names(tables)) {
    if (dbExistsTable(con, table_name)) {
      dbExecute(
        con,
        sprintf("DELETE FROM %s WHERE run_id = ?", dbQuoteIdentifier(con, table_name)),
        params = list(run_id)
      )
    }
    dbWriteTable(con, table_name, as.data.frame(tables[[table_name]]), append = TRUE)
    dbExecute(
      con,
      sprintf(
        "CREATE INDEX IF NOT EXISTS %s ON %s(run_id, meeting_number, iteration)",
        dbQuoteIdentifier(con, paste0("idx_", table_name, "_run_cell")),
        dbQuoteIdentifier(con, table_name)
      )
    )
  }
})

semantic_db_check <- dbGetQuery(
  con,
  paste(
    "SELECT COUNT(*) AS n, COUNT(DISTINCT score) AS distinct_scores,",
    "MIN(score) AS minimum, MAX(score) AS maximum",
    "FROM blinded_semantic_scores WHERE run_id = ?"
  ),
  params = list(run_id)
)
if (semantic_db_check$n[[1]] != 2130L ||
    semantic_db_check$distinct_scores[[1]] < 10L ||
    semantic_db_check$minimum[[1]] < -1 ||
    semantic_db_check$maximum[[1]] > 1) {
  stop("Post-migration semantic-score integrity gate failed")
}

cat(sprintf(
  paste0(
    "Migrated %d realized-dose rows, %d semantic scores, and %d pairwise scores ",
    "to %s.\n"
  ),
  nrow(realized_db), nrow(semantic_scores), nrow(pairwise_scores), database_path
))
