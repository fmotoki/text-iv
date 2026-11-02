# Check the frozen diagnostic with explicitly labelled synthetic inputs only.
suppressPackageStartupMessages({
  library(data.table)
  library(DBI)
  library(RSQLite)
  library(haven)
  library(jsonlite)
})
data.table::setDTthreads(1L)
source_root <- normalizePath(getwd(), winslash="/", mustWork=TRUE)
record <- Sys.getenv("QA_RECORD_DIR", unset="")
if (!nzchar(record) || !dir.exists(record)) stop("Set QA_RECORD_DIR to the owned execution record")
fixture_parent <- tempfile("terra56_cutoff_", tmpdir=Sys.getenv("TEMP"))
if (!dir.create(fixture_parent)) stop("Could not reserve exclusive fixture root")
write_json(list(status="RUNNING",synthetic=TRUE,fixture_parent=fixture_parent),
  file.path(record,"cutoff-fixtures-running.json"),auto_unbox=TRUE,pretty=TRUE)
base_spec <- fromJSON(file.path(source_root,"code","replication_spec.json"), simplifyVector=TRUE)
dates <- fread(base_spec$sample$dates_csv, showProgress=FALSE)[meeting_number %in% 203:281]
setorder(dates,meeting_number)
stopifnot(identical(as.integer(dates$meeting_number),203:281))
set.seed(20261101L)
index <- seq_len(79L)
synthetic <- data.table(reuniao=203:281, date_daily=as.Date(dates$meeting_date),
  forecast_anchored=3+sin(index/3)+rnorm(79,0,.03),
  forecast_unanchored=4+cos(index/8)+rnorm(79,0,.04),
  wc_orig=as.integer(1200+50*sin(index/4)+rnorm(79,0,20)),
  ibcbr_agro=100+cumsum(rnorm(79,0,.5)),
  ibcbr_industry=110+cumsum(rnorm(79,0,.4)),
  ibcbr_service=90+cumsum(rnorm(79,0,.3)))
synthetic[, inflacao12m := .4*forecast_anchored+.6*forecast_unanchored+.001*wc_orig+
  .01*ibcbr_agro-.02*ibcbr_industry+.015*ibcbr_service+rnorm(79,0,.02)]
fixture_records <- list()
outputs <- list()
for (case in c("base","perturbed","zero_benchmark","missing_neutral","hash_mismatch")) {
  case_root <- file.path(fixture_parent,case)
  stopifnot(dir.create(case_root))
  specification <- base_spec
  specification$design_type <- "qa_fixture"
  specification$release_id <- paste0("terra56_qa_cutoff_",case)
  specification$run_root <- normalizePath(case_root,winslash="/",mustWork=TRUE)
  specification$result_stamp <- "qa_cutoff"
  specification$raw_snapshot <- list(status="SYNTHETIC_FIXTURE",purpose="offline diagnostic validation")
  for (name in names(specification$run_ids)) specification$run_ids[[name]] <- paste0("terra56_qa_",name,"_",case)
  # Freeze only the source dependencies used by this bounded test.
  source_files <- c("code/read_run_spec.R","code/01_data_preparation/build_calibration_data.R",
                    "code/02_analysis/analyze_knowledge_cutoff.R")
  specification$source_hashes <- as.list(vapply(file.path(source_root,source_files),
    digest::digest,character(1),algo="sha256",file=TRUE,serialize=FALSE))
  names(specification$source_hashes) <- source_files
  specification$input_hashes <- setNames(list(digest::digest(base_spec$sample$dates_csv,
    algo="sha256",file=TRUE,serialize=FALSE)),base_spec$sample$dates_csv)
  specification$fingerprint <- digest::digest(list(case=case,source=specification$source_hashes,
    input=specification$input_hashes,model=specification$model),algo="sha256")
  paths <- lapply(specification$paths,function(path) file.path(case_root,path))
  for (path in c(paths$work_database,paths$sector_controls,paths$availability_audit)) {
    dir.create(dirname(path),recursive=TRUE,showWarnings=FALSE)
  }
  panel <- copy(synthetic)
  if (case=="perturbed") panel[reuniao %in% 272:281,inflacao12m:=inflacao12m+seq_len(.N)*.2]
  if (case=="zero_benchmark") panel[reuniao %in% 271:281,inflacao12m:=4]
  fwrite(panel[,.(reuniao,date_daily,ibcbr_agro,ibcbr_industry,ibcbr_service)],paths$sector_controls)
  availability <- CJ(meeting_number=203:281,series_code=c(29602L,29604L,29606L))
  availability[, availability_status:="READY"]
  availability[, vintage_status:="SYNTHETIC_VERIFIED_RELEASE_VINTAGE"]
  availability[, release_id:="synthetic_release"]
  availability[, reference_month:="synthetic_month"]
  availability[, meeting_cutoff:="SYNTHETIC_CUTOFF"]
  availability[, evidence_url:="synthetic_fixture"]
  fwrite(availability,paths$availability_audit)
  fwrite(data.table(status="SYNTHETIC_FIXTURE"),file.path(case_root,"data/analysis/meeting_focus_target_audit.csv"))
  # Original forecasts are genuine sequential synthetic paths, initialized at 3.80.
  original <- melt(panel,id.vars=c("reuniao","date_daily"),
    measure.vars=c("forecast_anchored","forecast_unanchored"),
    variable.name="forecaster_type",value.name="new_expectation",variable.factor=FALSE)
  original[, forecaster_type:=sub("forecast_","",forecaster_type)]
  setnames(original,"reuniao","meeting_number")
  setorder(original,forecaster_type,meeting_number)
  original[, previous_expectation:=shift(new_expectation),by=forecaster_type]
  original[meeting_number==203L,previous_expectation:=3.8]
  original[, forecast_revision:=new_expectation-previous_expectation]
  original[, run_id:=specification$run_ids$original_priors]
  original[, task_id:=paste(meeting_number,forecaster_type,sep="_")]
  original[, source_role:="original"]
  original[, scenario:="original"]
  original[, prompt_version:="forecaster_common_prior_v1"]
  original[, status:="completed"]
  original[, model:="gpt-5.6-terra"]
  original[, reasoning_effort:="high"]
  original[, corpus_iteration:=1L]
  main_corpus <- CJ(meeting_number=203:281,iteration=1:25,role="original")
  main_corpus[, run_id:=specification$run_ids$main_text]
  main_corpus[, text_hash:=paste0("synthetic_minute_",meeting_number)]
  main_corpus[, complete:=1L]
  main_tone <- CJ(meeting_number=203:281,iteration=1:25,role=c("original_text","hawk_text","dove_text"))
  main_tone[, run_id:=specification$run_ids$main_text]
  main_tone[, score:=sin(meeting_number/10)*.01+fifelse(role=="hawk_text",.02,fifelse(role=="dove_text",-.02,0))]
  main_tone[, word_count:=panel$wc_orig[match(meeting_number,panel$reuniao)]]
  original_tasks <- original[,.(run_id,task_id,meeting_number,forecaster_type,
    source_text_hash=paste0("synthetic_minute_",meeting_number),fixed_prior=previous_expectation,
    source_run_id=specification$run_ids$main_text,source_role,corpus_iteration)]
  dose <- CJ(meeting_number=272:281,corpus_iteration=1:5,
    forecaster_type=c("anchored","unanchored"),
    source_role=c("neutral","dove_high","dove_low","hawk_low","hawk_high"))
  dose <- merge(dose,original[,.(meeting_number,forecaster_type,previous_expectation)],
    by=c("meeting_number","forecaster_type"),all.x=TRUE)
  dose[, assigned:=fcase(source_role=="dove_high",-2,source_role=="dove_low",-1,
    source_role=="hawk_low",1,source_role=="hawk_high",2,default=0)]
  dose[, new_expectation:=previous_expectation+.03+.001*corpus_iteration+
    assigned*(.01+.0001*(meeting_number-272)+.0002*corpus_iteration)]
  dose[, forecast_revision:=new_expectation-previous_expectation]
  dose[, run_id:=specification$run_ids$common_prior_dose]
  dose[, task_id:=paste(meeting_number,corpus_iteration,forecaster_type,source_role,sep="_")]
  dose[, status:="completed"]
  dose[, model:="gpt-5.6-terra"]
  dose[, reasoning_effort:="high"]
  dose[, prompt_version:="forecaster_common_prior_v1"]
  dose[, scenario:="counterfactual"]
  dose_tasks <- dose[,.(run_id,task_id,meeting_number,forecaster_type,
    source_text_hash=paste(meeting_number,corpus_iteration,source_role,sep="_"),
    fixed_prior=previous_expectation,source_run_id=specification$run_ids$dose_text,
    source_role,corpus_iteration)]
  dose_corpus <- unique(dose_tasks[,.(run_id=source_run_id,meeting_number,
    iteration=corpus_iteration,role=source_role,text_hash=source_text_hash,complete=1L)])
  if (case=="missing_neutral") dose <- dose[!(meeting_number==272L & corpus_iteration==1L &
    forecaster_type=="anchored" & source_role=="neutral")]
  if (case=="hash_mismatch") dose_corpus[1L,text_hash:="deliberate_mismatch"]
  results <- rbindlist(list(original,dose),use.names=TRUE,fill=TRUE)
  tasks <- rbindlist(list(original_tasks,dose_tasks),use.names=TRUE,fill=TRUE)
  connection <- dbConnect(SQLite(),paths$work_database)
  dbWriteTable(connection,"agent_results_latest",as.data.frame(results))
  dbWriteTable(connection,"agent_tasks",as.data.frame(tasks))
  dbWriteTable(connection,"corpus_texts",as.data.frame(rbindlist(list(main_corpus,dose_corpus),use.names=TRUE)))
  dbWriteTable(connection,"tone_scores",as.data.frame(main_tone))
  focus <- panel[,.(date=as.character(date_daily),median=inflacao12m)]
  focus[, source_entity:="ExpectativasMercadoInflacao12Meses"]
  focus[, indicator:="IPCA"]
  focus[, smoothed:="N"]
  focus[, calculation_base:=0L]
  dbWriteTable(connection,"external_focus_statistics",as.data.frame(focus))
  dbDisconnect(connection)
  specification_path <- file.path(case_root,"specification.json")
  write_json(specification,specification_path,auto_unbox=TRUE,pretty=TRUE,null="null")
  stopifnot(file.copy(specification_path,file.path(case_root,"effective_spec.json")))
  Sys.setenv(REPLICATION_SPEC_PATH=specification_path)
  sys.source(file.path(source_root,"code/01_data_preparation/build_calibration_data.R"),envir=new.env())
  # tryCatch requires an error callback so negative fixtures can assert their failure.
  outcome <- tryCatch({sys.source(file.path(source_root,"code/02_analysis/analyze_knowledge_cutoff.R"),
    envir=new.env()); "PASS"},error=function(error) conditionMessage(error))
  cat("DIAGNOSTIC",case,outcome,"\n")
  expected_failure <- case %in% c("missing_neutral","hash_mismatch")
  if (expected_failure) {
    stopifnot(grepl("framing counterparts or source hashes",outcome,fixed=TRUE))
    stopifnot(!file.exists(file.path(paths$tables,"knowledge_cutoff_metrics_qa_cutoff.csv")))
  } else {
    stopifnot(identical(outcome,"PASS"))
    predictions <- fread(file.path(paths$tables,"knowledge_cutoff_predictions_qa_cutoff.csv"))
    metrics <- fread(file.path(paths$tables,"knowledge_cutoff_metrics_qa_cutoff.csv"))
    coefficients <- fread(file.path(paths$tables,"knowledge_cutoff_coefficients_qa_cutoff.csv"))
    meetings <- fread(file.path(paths$tables,"knowledge_cutoff_framing_meetings_qa_cutoff.csv"))
    draws <- fread(file.path(paths$tables,"knowledge_cutoff_framing_draws_qa_cutoff.csv"))
    summary <- fread(file.path(paths$tables,"knowledge_cutoff_framing_summary_qa_cutoff.csv"))
    stopifnot(nrow(predictions)==40L,nrow(metrics)==8L,all(metrics$n==5L),nrow(meetings)==120L)
    recalculated <- predictions[,.(rmse=sqrt(mean((prediction-focus)^2)),mae=mean(abs(prediction-focus)),
      mean_signed_error=mean(prediction-focus)),by=.(window,series)]
    compare <- merge(metrics,recalculated,by=c("window","series"))
    stopifnot(max(abs(compare$rmse.x-compare$rmse.y))<1e-12,
      max(abs(compare$mae.x-compare$mae.y))<1e-12,
      max(abs(compare$mean_signed_error.x-compare$mean_signed_error.y))<1e-12)
    expected_means <- draws[,.(expected=mean(contrast_pp)),by=.(meeting_number,contrast,forecaster_type)]
    compare <- merge(meetings,expected_means,by=c("meeting_number","contrast","forecaster_type"))
    stopifnot(max(abs(compare$contrast_pp-compare$expected))<1e-12)
    expected_windows <- meetings[,.(expected=mean(contrast_pp)),by=.(window,contrast,forecaster_type)]
    compare <- merge(summary,expected_windows,by=c("window","contrast","forecaster_type"))
    stopifnot(max(abs(compare$mean_contrast_pp-compare$expected))<1e-12)
    stopifnot(all(is.na(metrics[series!="frozen_calibrated_composite",benchmark_rmse_ratio])),
      all(metrics[series!="frozen_calibrated_composite",ratio_status]=="not_applicable"))
    composite_metrics <- metrics[series=="frozen_calibrated_composite"]
    if (case=="zero_benchmark") {
      stopifnot(all(is.na(composite_metrics$benchmark_rmse_ratio)),
        all(composite_metrics$ratio_status=="undefined_zero_benchmark_rmse"))
    } else {
      stopifnot(all(composite_metrics$ratio_status=="defined"),
        max(abs(composite_metrics$benchmark_rmse_ratio-
          composite_metrics$rmse/composite_metrics$benchmark_rmse))<1e-12)
    }
    outputs[[case]] <- list(predictions=predictions,metrics=metrics,coefficients=coefficients)
  }
  fixture_records[[case]] <- list(root=case_root,specification=specification_path,status=outcome)
  cat("FIXTURE",case,outcome,"\n")
}
# Holdout-only target perturbations cannot change fitted coefficients or raw/frozen forecasts.
stopifnot(isTRUE(all.equal(outputs$base$coefficients,outputs$perturbed$coefficients,tolerance=1e-12)))
compare <- merge(outputs$base$predictions,outputs$perturbed$predictions,
  by=c("meeting_number","series"),suffixes=c("_base","_perturbed"))
stopifnot(max(abs(compare[series!="focus_persistence",prediction_base-prediction_perturbed]))<1e-12)
persistence <- compare[series=="focus_persistence"]
setorder(persistence,meeting_number)
stopifnot(max(abs(persistence$prediction_perturbed-persistence$prediction_base-c(0,(1:9)*.2)))<1e-12)
# This analytical fixture distinguishes RMSE from MAE and fixes the error sign.
error <- c(0,0,0,0,5)
stopifnot(sqrt(mean(error^2))==sqrt(5),mean(abs(error))==1,mean(error)==1)
stopifnot(identical(as.integer(dates[meeting_number %in% c(275,276,277,281),format(as.Date(meeting_date),"%Y")]),
  c(2025L,2026L,2026L,2026L)))
write_json(list(status="PASS",synthetic=TRUE,fixture_parent=fixture_parent,fixtures=fixture_records),
  file.path(record,"cutoff-fixtures.json"),auto_unbox=TRUE,pretty=TRUE)
cat("PASS frozen69, persistence transitions, metrics, equal meeting weights, neutral/hash failures, zero denominator.\n")
