# Import only the selected complete real blinded-score cohort.
suppressPackageStartupMessages({library(DBI);library(RSQLite);library(data.table);library(jsonlite);library(digest)})
spec_path <- Sys.getenv("REPLICATION_SPEC_PATH")
if (!nzchar(spec_path)) stop("REPLICATION_SPEC_PATH is required")
source_root <- fromJSON(spec_path)$source_root
source(file.path(source_root,"code","read_run_spec.R"))
args <- commandArgs(trailingOnly=TRUE)
if (length(args) %% 2L || !length(args)) stop("Supply --database, --run-id, --source-run-id and --validation-dir")
options <- setNames(as.list(args[seq.int(2L,length(args),by=2L)]),sub("^--","",args[seq.int(1L,length(args),by=2L)]))
if (!setequal(names(options),c("database","run-id","source-run-id","validation-dir"))) stop("Unexpected/missing importer arguments")
run_id <- options[["run-id"]]
source_run_id <- options[["source-run-id"]]
if (run_id!=spec$run_ids$blinded_scoring || source_run_id!=spec$run_ids$dose_text) stop("Wrong scorer/source cohort")
database_path <- run_path(options$database)
if (database_path!=spec$paths$database) stop("Wrong selected database")
validation_dir <- run_path(options[["validation-dir"]])
if (validation_dir!=file.path(root,"data","interim","dose_response",run_id)) stop("Wrong selected validation directory")
migrated_at <- format(Sys.time(),tz="UTC",usetz=TRUE)

# jsonlite stream_in requires an open connection. This existing narrow reader
# owns that resource so imports do not retain Windows file handles.
read_jsonl <- function(path) {
  connection <- file(path,open="rb")
  on.exit(close(connection),add=TRUE)
  as.data.table(stream_in(connection,verbose=FALSE,simplifyDataFrame=TRUE))
}
semantic_tasks <- read_jsonl(file.path(validation_dir,"semantic_tone_manifest.jsonl"))
semantic_events <- read_jsonl(file.path(validation_dir,"semantic_tone_results.jsonl"))
pairwise_tasks <- read_jsonl(file.path(validation_dir,"pairwise_tone_manifest.jsonl"))
pairwise_events <- read_jsonl(file.path(validation_dir,"pairwise_tone_results.jsonl"))
semantic_lines <- readLines(file.path(validation_dir,"semantic_tone_results.jsonl"),encoding="UTF-8",warn=FALSE)
pairwise_lines <- readLines(file.path(validation_dir,"pairwise_tone_results.jsonl"),encoding="UTF-8",warn=FALSE)
semantic_events$raw_json <- semantic_lines[nzchar(semantic_lines)]
pairwise_events$raw_json <- pairwise_lines[nzchar(pairwise_lines)]
semantic_scores <- semantic_events[!duplicated(task_id,fromLast=TRUE)]
pairwise_scores <- pairwise_events[!duplicated(task_id,fromLast=TRUE)]
realized <- fread(file.path(validation_dir,"realized_dose_dataset.csv"),encoding="UTF-8")
roles <- c("original","dove_high","dove_low","neutral","hawk_low","hawk_high")
pairs <- c("original_vs_neutral","dove_high_vs_dove_low","dove_low_vs_neutral","neutral_vs_hawk_low","hawk_low_vs_hawk_high")
semantic_keys <- c("meeting_number","iteration","text_role")
pairwise_keys <- c("meeting_number","iteration","pair_type")
expected_semantic <- CJ(meeting_number=as.integer(spec$sample$meeting_numbers),iteration=seq_len(spec$draws$dose),text_role=roles)
if (is.null(spec$pairwise_pairs) || !setequal(names(spec$pairwise_pairs),pairs)) stop("Pairwise protocol differs from specification")
for (pair in pairs) if (!identical(as.character(spec$pairwise_pairs[[pair]]),strsplit(pair,"_vs_",fixed=TRUE)[[1L]])) stop("Pairwise role mapping changed")
expected_pairs <- CJ(meeting_number=as.integer(spec$sample$meeting_numbers),iteration=seq_len(spec$draws$dose),pair_type=pairs)
for (data in list(semantic_tasks,semantic_scores,realized)) if (anyDuplicated(data[,..semantic_keys]) || !fsetequal(data[,..semantic_keys],expected_semantic)) stop("Incomplete/duplicate semantic Cartesian keys")
for (data in list(pairwise_tasks,pairwise_scores)) if (anyDuplicated(data[,..pairwise_keys]) || !fsetequal(data[,..pairwise_keys],expected_pairs)) stop("Incomplete/duplicate pairwise Cartesian keys")
for (data in list(semantic_tasks,semantic_scores,pairwise_tasks,pairwise_scores)) {
  if (anyDuplicated(data$task_id) || any(data$run_id!=run_id | data$source_run_id!=source_run_id | data$spec_fingerprint!=spec$fingerprint | data$model!=spec$model | data$reasoning_effort!=spec$reasoning_effort)) stop("Scorer cohort/model/specification mismatch")
}
if (any(semantic_tasks$prompt_version!="semantic_step3_tone_v0.2") || any(pairwise_tasks$prompt_version!="pairwise_step3_tone_v0.1")) stop("Wrong scorer prompt protocol")
for (data in list(semantic_scores,pairwise_scores)) {
  if (any(data$status!="completed" | is.na(data$runner_dry_run) | data$runner_dry_run)) stop("Only real completed scores can be imported")
  if (any(!grepl("^[0-9a-f]{64}$",data$request_input_hash) | !grepl("^[0-9a-f]{64}$",data$raw_response_hash))) stop("Missing request/response identity")
}
for (item in list(list(tasks=semantic_tasks,scores=semantic_scores,fields=c("run_id","source_run_id","model","reasoning_effort","spec_fingerprint","task_id",semantic_keys,"text_hash","prompt_version","prompt_hash","request_input_hash")),list(tasks=pairwise_tasks,scores=pairwise_scores,fields=c("run_id","source_run_id","model","reasoning_effort","spec_fingerprint","task_id",pairwise_keys,"role_a","role_b","text_a_hash","text_b_hash","prompt_version","prompt_hash","request_input_hash")))) {
  fields <- item$fields
  if (!fsetequal(item$tasks[,..fields],item$scores[,..fields])) stop("Task/result source or request identity mismatch")
}
if (any(!semantic_scores$label %in% c("dovish","neutral","hawkish"))) stop("Invalid semantic label")
if (any(!is.finite(semantic_scores$stance_score) | abs(semantic_scores$stance_score)>1 | !is.finite(semantic_scores$confidence) | semantic_scores$confidence<0 | semantic_scores$confidence>1)) stop("Semantic score outside its JSON schema")
for (i in seq_len(nrow(pairwise_tasks))) if (!setequal(c(pairwise_tasks$role_a[i],pairwise_tasks$role_b[i]),spec$pairwise_pairs[[pairwise_tasks$pair_type[i]]])) stop("Pairwise A/B roles do not match the named comparator")
expected_winner <- ifelse(pairwise_scores$more_hawkish=="A",pairwise_scores$role_a,ifelse(pairwise_scores$more_hawkish=="B",pairwise_scores$role_b,"tie"))
if (any(pairwise_scores$more_hawkish_role!=expected_winner)) stop("Pairwise encoded winner disagrees with role")
if (any(!pairwise_scores$more_hawkish %in% c("A","B","tie") | !is.finite(pairwise_scores$confidence) | pairwise_scores$confidence<0 | pairwise_scores$confidence>1)) stop("Invalid pairwise JSON schema")
if (any(realized$run_id!=source_run_id | realized$scorer_run_id!=run_id | realized$source_run_id!=source_run_id | realized$model!=spec$model | realized$reasoning_effort!=spec$reasoning_effort | realized$spec_fingerprint!=spec$fingerprint | realized$status!="completed" | realized$runner_dry_run)) stop("Realized-dose provenance mismatch")
for (json in c(semantic_scores$raw_json,pairwise_scores$raw_json)) {
  record <- fromJSON(json,simplifyVector=FALSE)
  if (is.null(record$response_metadata$returncode) || record$response_metadata$returncode!=0) stop("Runner provenance rejected or missing")
}
semantic_check <- merge(semantic_scores[,c(semantic_keys,"text_hash","stance_score"),with=FALSE],realized[,c(semantic_keys,"source_text_hash","semantic_score"),with=FALSE],by=semantic_keys,all=TRUE)
if (anyNA(semantic_check$stance_score) || anyNA(semantic_check$semantic_score) || any(semantic_check$text_hash!=semantic_check$source_text_hash) || any(abs(semantic_check$stance_score-semantic_check$semantic_score)>1e-12)) stop("Realized semantic scores/hash disagree")

con <- dbConnect(SQLite(),database_path)
tryCatch({
  identity <- dbGetQuery(con,"SELECT value FROM metadata WHERE key='replication_specification'")
  if (nrow(identity)!=1L) stop("Warehouse lacks immutable specification identity")
  warehouse <- fromJSON(identity$value)
  expected_identity <- list(spec_sha256=spec_sha256,fingerprint=spec$fingerprint,release_id=spec$release_id,design_type=spec$design_type)
  for (field in names(expected_identity)) if (!identical(warehouse[[field]],expected_identity[[field]])) stop("Warehouse specification mismatch")
  source <- as.data.table(dbGetQuery(con,"SELECT run_id AS source_run_id,meeting_number,iteration,role AS text_role,text_hash,complete FROM corpus_texts WHERE run_id=?",params=list(source_run_id)))
  if (anyDuplicated(source[,..semantic_keys]) || !fsetequal(source[,..semantic_keys],expected_semantic) || any(source$complete!=1L)) stop("Selected SQLite corpus is incomplete")
  source_check <- merge(source,realized[,c(semantic_keys,"source_text_hash"),with=FALSE],by=semantic_keys,all=TRUE)
  if (anyNA(source_check$text_hash) || any(source_check$text_hash!=source_check$source_text_hash)) stop("Realized dose source hash differs from SQLite corpus")
  # The standalone importer binds each blinded side to the current selected
  # corpus. Agreement between a task and its score alone cannot establish this.
  for (data in list(pairwise_tasks,pairwise_scores)) {
    for (arm in c("a","b")) {
      role_column <- paste0("role_",arm)
      hash_column <- paste0("text_",arm,"_hash")
      join_keys <- c("source_run_id","meeting_number","iteration",role_column)
      current <- copy(source[,.(source_run_id,meeting_number,iteration,text_role,text_hash)])
      setnames(current,c("text_role","text_hash"),c(role_column,"current_text_hash"))
      pair_columns <- c("task_id",join_keys,hash_column)
      pair_source_check <- merge(data[,..pair_columns],current,by=join_keys,all.x=TRUE,sort=FALSE)
      if (nrow(pair_source_check)!=nrow(data) || anyNA(pair_source_check$current_text_hash) || anyNA(pair_source_check[[hash_column]]) || any(pair_source_check[[hash_column]]!=pair_source_check$current_text_hash)) stop("Pairwise ",hash_column," differs from selected SQLite corpus")
    }
  }
  for (data in list(semantic_tasks,semantic_scores,pairwise_tasks,pairwise_scores,realized)) set(data,j="migrated_at",value=rep(migrated_at,nrow(data)))
  semantic_scores$score <- semantic_scores$stance_score
  semantic_task_fields <- c("run_id","source_run_id","task_id","anonymous_id",semantic_keys,"assigned_dose","text_hash","prompt_version","prompt_hash","request_input_hash","model","reasoning_effort","spec_fingerprint","migrated_at")
  semantic_score_fields <- c("run_id","source_run_id","task_id","status",semantic_keys,"text_hash","score","label","confidence","model","reasoning_effort","prompt_version","prompt_hash","spec_fingerprint","request_input_hash","raw_response_hash","runner_dry_run","created_at","raw_json","migrated_at")
  pair_task_fields <- c("run_id","source_run_id","task_id","anonymous_pair_id",pairwise_keys,"role_a","role_b","text_a_hash","text_b_hash","prompt_version","prompt_hash","request_input_hash","model","reasoning_effort","spec_fingerprint","migrated_at")
  pair_score_fields <- c("run_id","source_run_id","task_id","status",pairwise_keys,"role_a","role_b","text_a_hash","text_b_hash","more_hawkish","more_hawkish_role","confidence","model","reasoning_effort","prompt_version","prompt_hash","spec_fingerprint","request_input_hash","raw_response_hash","runner_dry_run","created_at","raw_json","migrated_at")
  tables <- list(blinded_semantic_tasks=semantic_tasks[,..semantic_task_fields],blinded_semantic_scores=semantic_scores[,..semantic_score_fields],blinded_pairwise_tasks=pairwise_tasks[,..pair_task_fields],blinded_pairwise_scores=pairwise_scores[,..pair_score_fields],realized_dose_scores=realized)
  dbWithTransaction(con,{
    # Register the scorer identity without replacing an existing run or its source.
    registration <- list(fingerprint=spec$fingerprint,spec_sha256=spec_sha256,release_id=spec$release_id,design_type=spec$design_type,source_run_id=source_run_id,model=spec$model,reasoning_effort=spec$reasoning_effort)
    existing <- dbGetQuery(con,"SELECT metadata_json FROM runs WHERE run_id=?",params=list(run_id))
    if (nrow(existing)) {
      old <- fromJSON(existing$metadata_json)
      for (field in names(registration)) if (!identical(old[[field]],registration[[field]])) stop("Scorer registration cannot be changed")
    } else dbExecute(con,"INSERT INTO runs(run_id,run_dir,first_meeting,last_meeting,metadata_json,ingested_at) VALUES(?,?,?,?,?,?)",params=list(run_id,validation_dir,min(spec$sample$meeting_numbers),max(spec$sample$meeting_numbers),toJSON(registration,auto_unbox=TRUE),migrated_at))
    for (table_name in names(tables)) {
      data <- tables[[table_name]]
      selected_id <- if (table_name=="realized_dose_scores") source_run_id else run_id
      if (dbExistsTable(con,table_name)) {
        # Add nullable provenance fields while preserving unrelated historical rows.
        missing <- setdiff(names(data),dbListFields(con,table_name))
        for (column in missing) {
          type <- if (is.numeric(data[[column]]) || is.logical(data[[column]])) "REAL" else "TEXT"
          dbExecute(con,sprintf("ALTER TABLE %s ADD COLUMN %s %s",dbQuoteIdentifier(con,table_name),dbQuoteIdentifier(con,column),type))
        }
        dbExecute(con,sprintf("DELETE FROM %s WHERE run_id=?",dbQuoteIdentifier(con,table_name)),params=list(selected_id))
      }
      dbWriteTable(con,table_name,as.data.frame(data),append=TRUE)
      stored <- dbGetQuery(con,sprintf("SELECT COUNT(*) n FROM %s WHERE run_id=?",dbQuoteIdentifier(con,table_name)),params=list(selected_id))$n
      if (stored!=nrow(data)) stop("Scoped score import count mismatch")
    }
  })
},finally=dbDisconnect(con))
cat(sprintf("Imported selected scorer %s and source %s. Semantic=%d, pairwise=%d.\n",run_id,source_run_id,nrow(semantic_scores),nrow(pairwise_scores)))
