suppressPackageStartupMessages({
  library(DBI)
  library(RSQLite)
  library(data.table)
  library(quanteda)
  library(stopwords)
  library(stm)
  library(sandwich)
  library(lmtest)
  library(jsonlite)
})
options(warn = 1)

data.table::setDTthreads(1L)
quanteda_options(threads = 1L)

root <- normalizePath(".", winslash = "/", mustWork = TRUE)
source(file.path(root, "code", "02_analysis", "reproducibility_helpers.R"), local = TRUE)
database_path <- file.path(root, "outputs", "work", "prompt_iv_replication.sqlite")
table_dir <- file.path(root, "outputs", "tables")
model_dir <- file.path(root, "outputs", "models")
dir.create(table_dir, recursive = TRUE, showWarnings = FALSE)
dir.create(model_dir, recursive = TRUE, showWarnings = FALSE)

stamp <- "20260715"
run_id <- "v10_referee_step3_neutral_dose_5draws_20260627"
topic_count <- 8L
seed <- 20260715L

con <- dbConnect(SQLite(), database_path)
on.exit(dbDisconnect(con), add = TRUE)
dbExecute(con, "PRAGMA busy_timeout = 60000")

texts <- as.data.table(dbGetQuery(
  con,
  paste(
    "SELECT meeting_number, iteration, role AS source_role, text, text_hash,",
    "word_count",
    "FROM corpus_texts",
    sprintf("WHERE run_id = '%s'", run_id),
    "AND complete = 1"
  )
))
if (nrow(texts) != 2130L) {
  stop(sprintf("Expected 2,130 corpus rows, found %d", nrow(texts)))
}

# The original is identical across the five draw indices. Retain it once per
# meeting so it does not receive five times the weight of a unique document.
texts <- rbindlist(list(
  texts[source_role == "original", .SD[1L], by = meeting_number],
  texts[source_role != "original"]
), use.names = TRUE)
texts[, document_id := sprintf(
  "m%03d_i%02d_%s",
  meeting_number,
  iteration,
  source_role
)]
setorder(texts, meeting_number, source_role, iteration)

tone <- as.data.table(dbGetQuery(
  con,
  paste(
    "SELECT meeting_number, iteration, role AS source_role, 100.0 * score AS tone_pp",
    "FROM tone_scores",
    sprintf("WHERE run_id = '%s'", run_id)
  )
))
texts <- merge(
  texts,
  tone,
  by = c("meeting_number", "iteration", "source_role"),
  all.x = TRUE
)
if (anyNA(texts$tone_pp)) stop("Missing tone scores after corpus merge")

corp <- corpus(texts$text, docnames = texts$document_id)
toks <- tokens(
  corp,
  remove_punct = TRUE,
  remove_numbers = TRUE,
  remove_symbols = TRUE,
  remove_separators = TRUE,
  remove_url = TRUE
)
toks <- tokens_tolower(toks)
toks <- tokens_remove(toks, stopwords("pt"), padding = FALSE)
toks <- tokens_keep(toks, pattern = "^[[:alpha:]]{3,}$", valuetype = "regex")
document_feature_matrix <- dfm(toks)
document_feature_matrix <- dfm_trim(
  document_feature_matrix,
  min_docfreq = 10L,
  docfreq_type = "count"
)
document_feature_matrix <- dfm_trim(
  document_feature_matrix,
  max_docfreq = 0.95,
  docfreq_type = "prop"
)
if (nfeat(document_feature_matrix) < 250L) {
  stop(sprintf("Topic vocabulary unexpectedly small: %d", nfeat(document_feature_matrix)))
}

stm_input <- convert(document_feature_matrix, to = "stm")
meta <- copy(texts)[match(names(stm_input$documents), document_id)]
meta[, source_role := relevel(factor(source_role), ref = "original")]
meta[, meeting_factor := factor(meeting_number)]

set.seed(seed)
topic_model <- stm(
  documents = stm_input$documents,
  vocab = stm_input$vocab,
  K = topic_count,
  prevalence = ~ source_role + meeting_factor,
  data = as.data.frame(meta),
  init.type = "Spectral",
  max.em.its = 100L,
  seed = seed,
  verbose = FALSE
)

theta <- as.data.table(topic_model$theta)
setnames(theta, paste0("topic_", seq_len(topic_count)))
topic_documents <- cbind(
  meta[, .(
    document_id, meeting_number, iteration, source_role = as.character(source_role),
    text_hash, word_count, tone_pp
  )],
  theta
)

labels <- labelTopics(topic_model, n = 8L)
topic_labels <- data.table(
  topic = seq_len(topic_count),
  highest_probability_terms = apply(labels$prob, 1L, paste, collapse = ", "),
  frex_terms = apply(labels$frex, 1L, paste, collapse = ", ")
)
topic_columns <- paste0("topic_", seq_len(topic_count))
tone_correlations <- vapply(
  topic_columns,
  function(column) cor(
    topic_documents[[column]],
    topic_documents$tone_pp,
    method = "spearman",
    use = "complete.obs"
  ),
  numeric(1)
)
tone_topic <- which.max(abs(tone_correlations))
topic_labels[, `:=`(
  spearman_with_lexicon_tone = tone_correlations,
  designated_tone_topic = topic == tone_topic
)]

topic_meeting <- topic_documents[, lapply(.SD, mean),
  by = .(meeting_number, source_role),
  .SDcols = topic_columns]
topic_meeting[, source_role := relevel(factor(source_role), ref = "original")]

topic_effect_rows <- vector("list", topic_count)
for (topic_index in seq_len(topic_count)) {
  outcome <- topic_columns[[topic_index]]
  model <- lm(
    reformulate(
      c("source_role", "factor(meeting_number)"),
      response = outcome
    ),
    data = topic_meeting
  )
  vc <- vcovCL(model, cluster = ~meeting_number, type = "HC1")
  ct <- coeftest(model, vcov. = vc)
  terms <- rownames(ct)
  keep <- startsWith(terms, "source_role")
  original_sd <- sd(topic_meeting[source_role == "original"][[outcome]])
  topic_effect_rows[[topic_index]] <- data.table(
    topic = topic_index,
    comparison = sub("source_role", "", terms[keep], fixed = TRUE),
    estimate = ct[keep, "Estimate"],
    cluster_se = ct[keep, "Std. Error"],
    statistic = ct[keep, "t value"],
    p_value = ct[keep, "Pr(>|t|)"],
    standardized_effect = ct[keep, "Estimate"] / original_sd,
    observations = nobs(model),
    meeting_clusters = uniqueN(topic_meeting$meeting_number),
    designated_tone_topic = topic_index == tone_topic
  )
}
topic_effects <- rbindlist(topic_effect_rows)
topic_effects[, p_value_holm := p.adjust(p_value, method = "holm")]

original_topics <- copy(topic_meeting[source_role == "original"])
setnames(
  original_topics,
  topic_columns,
  paste0(topic_columns, "_original")
)
topic_comparison <- merge(
  topic_meeting,
  original_topics,
  by = "meeting_number",
  suffixes = c("", "_reference")
)

cosine_similarity <- function(left, right) {
  sum(left * right) / sqrt(sum(left^2) * sum(right^2))
}
jensen_shannon <- function(left, right) {
  midpoint <- 0.5 * (left + right)
  kl <- function(x, y) sum(ifelse(x > 0, x * log(x / y), 0))
  0.5 * kl(left, midpoint) + 0.5 * kl(right, midpoint)
}

topic_comparison[, `:=`(
  topic_cosine_similarity = vapply(seq_len(.N), function(row) {
    cosine_similarity(
      as.numeric(topic_comparison[row, topic_columns, with = FALSE]),
      as.numeric(topic_comparison[row, paste0(topic_columns, "_original"), with = FALSE])
    )
  }, numeric(1)),
  topic_jensen_shannon = vapply(seq_len(.N), function(row) {
    jensen_shannon(
      as.numeric(topic_comparison[row, topic_columns, with = FALSE]),
      as.numeric(topic_comparison[row, paste0(topic_columns, "_original"), with = FALSE])
    )
  }, numeric(1))
)]
topic_similarity <- topic_comparison[, .(
  mean_cosine_similarity = mean(topic_cosine_similarity),
  cosine_ci_low = quantile(topic_cosine_similarity, 0.025),
  cosine_ci_high = quantile(topic_cosine_similarity, 0.975),
  mean_jensen_shannon = mean(topic_jensen_shannon),
  jensen_shannon_ci_low = quantile(topic_jensen_shannon, 0.025),
  jensen_shannon_ci_high = quantile(topic_jensen_shannon, 0.975),
  meetings = .N
), by = source_role]

non_tone_columns <- setdiff(topic_columns, paste0("topic_", tone_topic))
non_tone_response <- as.matrix(topic_meeting[, non_tone_columns, with = FALSE])
non_tone_manova <- manova(
  non_tone_response ~ source_role + factor(meeting_number),
  data = topic_meeting
)
non_tone_stats <- summary(non_tone_manova, test = "Pillai")$stats
non_tone_test <- data.table(
  test = "Joint role effect on non-tone topic proportions",
  statistic = non_tone_stats["source_role", "approx F"],
  numerator_df = non_tone_stats["source_role", "num Df"],
  denominator_df = non_tone_stats["source_role", "den Df"],
  p_value = non_tone_stats["source_role", "Pr(>F)"],
  tone_topic = tone_topic,
  non_tone_topics = paste(setdiff(seq_len(topic_count), tone_topic), collapse = ",")
)

effect_energy <- topic_effects[, .(
  treatment_effect_energy = sum(estimate^2)
), by = topic]
effect_energy[, treatment_effect_share :=
  treatment_effect_energy / sum(treatment_effect_energy)]
effect_energy[, designated_tone_topic := topic == tone_topic]

dbWithTransaction(con, {
  dbWriteTable(
    con,
    "referee_topic_proportions",
    as.data.frame(topic_documents),
    overwrite = TRUE
  )
  dbWriteTable(
    con,
    "referee_topic_effects",
    as.data.frame(topic_effects),
    overwrite = TRUE
  )
})

model_path <- file.path(
  model_dir,
  paste0("referee_d15_stm_topics_", stamp, ".rds")
)
saveRDS(topic_model, model_path)

write_table <- function(data, name) {
  path <- file.path(table_dir, paste0(name, "_", stamp, ".csv"))
  fwrite(data, path)
  path
}

outputs <- list(
  labels = write_table(topic_labels, "referee_d15_topic_labels"),
  effects = write_table(topic_effects, "referee_d15_topic_effects"),
  similarity = write_table(topic_similarity, "referee_d15_topic_similarity"),
  joint_test = write_table(non_tone_test, "referee_d15_non_tone_joint_test"),
  effect_energy = write_table(effect_energy, "referee_d15_topic_effect_energy"),
  model = model_path
)

summary_path <- file.path(
  table_dir,
  paste0("referee_d15_topic_preservation_", stamp, ".json")
)
write_json(
  release_metadata(list(
    generated_at = format(Sys.time(), tz = "UTC", usetz = TRUE),
    implementation = list(
      language = "R",
      packages = c("quanteda", "stopwords", "stm", "sandwich", "lmtest"),
      run_id = run_id,
      documents = nrow(topic_documents),
      meetings = uniqueN(topic_documents$meeting_number),
      topics = topic_count,
      vocabulary = nfeat(document_feature_matrix),
      original_deduplicated = TRUE
    ),
    topic_labels = topic_labels,
    designated_tone_topic = tone_topic,
    topic_similarity = topic_similarity,
    non_tone_joint_test = non_tone_test,
    effect_energy = effect_energy,
    outputs = outputs
  ), root, stamp),
  summary_path,
  auto_unbox = TRUE,
  pretty = TRUE,
  digits = 16
)

cat(sprintf("Completed D15 topic-preservation analysis: %s\n", summary_path))
